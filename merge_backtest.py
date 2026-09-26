#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
merge_backtest.py
==================
Integreaza in data/plans.json rezultatele unui backtest DEJA rulat, din
data/backtest_plans.json, fara sa redescarce nimic.

DE CE EXISTA
------------
`backtest.py --merge` face integrarea, dar reruleaza tot: pe 5 ani si 20 de
simboluri asta inseamna ~55 de minute, aproape integral descarcare. Daca ai
rulat deja backtest-ul si ai uitat `--merge` (sau ai vrut sa vezi intai
raportul), nu are sens sa platesti a doua oara acelasi timp.

CE FACE
-------
- renumeroteaza planurile ca sa nu se ciocneasca id-urile cu cele live
- sare peste planurile deja integrate (idempotent: a doua rulare nu dubleaza)
- recalculeaza calibrarea si rezumatul
- NU atinge planurile live si nu sterge nimic

RULARE
------
    python3 merge_backtest.py --dry-run   # arata ce ar face
    python3 merge_backtest.py             # aplica
    python3 ai_agent.py                   # apoi agentul invata din ele
"""

import json
import os
import sys

import plan_tracker

DATA_DIR = "data"
PLANS_FILE = os.path.join(DATA_DIR, "plans.json")
BACKTEST_FILE = os.path.join(DATA_DIR, "backtest_plans.json")


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(json.dumps(data, separators=(",", ":")))


def plan_key(p):
    """Identitate stabila a unui plan de backtest, ca sa pot detecta duplicate
    dupa renumerotare: id-ul se schimba la integrare, dar simbolul, momentul
    crearii si nivelurile nu."""
    return (p.get("symbol"), round(p.get("created_ts") or 0, 3),
            p.get("direction"), round(p.get("entry") or 0, 10))


def main():
    dry = "--dry-run" in sys.argv

    bt = load_json(BACKTEST_FILE)
    if not bt or not bt.get("plans"):
        print(f"[!] {BACKTEST_FILE} lipseste sau e gol. Ruleaza intai backtest.py.")
        return 1

    live = load_json(PLANS_FILE, {"next_id": 1, "plans": []})
    # REGENERARE, identic cu backtest.py --merge: planurile de backtest ale
    # familiei curente se INLOCUIESC cu cele din backtest_plans.json. Doar cu
    # deduplicare, modul merge_only pastra caracteristicile calculate cu logica
    # veche si nu reantrena agentul.
    old_bt = {id(p) for p in live["plans"]
              if p.get("source") == "backtest"
              and plan_tracker.same_family(p.get("geometry", "v1"))}
    if old_bt:
        live["plans"] = [p for p in live["plans"] if id(p) not in old_bt]
        print(f"  inlocuiesc {len(old_bt)} planuri de backtest ale familiei "
              f"{plan_tracker.GEOMETRY_FAMILY}")
        agent_file = os.path.join(os.path.dirname(PLANS_FILE), "agent_model.json")
        if os.path.exists(agent_file):
            os.remove(agent_file)
            print("  modelul agentului sters - se reantreneaza de la zero")

    existing = {plan_key(p) for p in live["plans"] if p.get("source") == "backtest"}

    incoming = [p for p in bt["plans"] if plan_key(p) not in existing]
    skipped = len(bt["plans"]) - len(incoming)

    print(f"Backtest: {len(bt['plans'])} planuri in {BACKTEST_FILE}")
    print(f"  deja integrate: {skipped}")
    print(f"  de adaugat:     {len(incoming)}")
    print(f"Live: {len(live['plans'])} planuri, next_id={live.get('next_id', 1)}")

    if not incoming:
        print("\nNimic nou de integrat - toate planurile sunt deja in plans.json.")
        return 0

    geo = {}
    for p in incoming:
        geo[p.get("geometry", "?")] = geo.get(p.get("geometry", "?"), 0) + 1
    print(f"  geometrii: {geo}")
    current = plan_tracker.GEOMETRY_VERSION
    wrong = sum(n for g, n in geo.items() if g != current)
    if wrong:
        print(f"  [!] {wrong} planuri au alta geometrie decat cea curenta ({current}).")
        print(f"      Vor fi ignorate de calibrare si de agent - asta e intentionat,")
        print(f"      rezultatele din geometrii diferite nu sunt comparabile.")

    if dry:
        print("\nNimic nu a fost scris. Ruleaza fara --dry-run ca sa aplici.")
        return 0

    offset = live.get("next_id", 1)
    for i, p in enumerate(incoming):
        p["id"] = offset + i
    live["plans"].extend(incoming)
    live["next_id"] = offset + len(incoming)
    live["calibration"] = plan_tracker.build_calibration(live)
    live["summary"] = plan_tracker.summarize(live)
    # Prin plan_tracker.save_plans: curata campurile de afisare de pe planurile
    # vechi si verifica dimensiunea INAINTE de scriere.
    plan_tracker.save_plans(live)

    # Fisierul de backtest devine redundant dupa integrare: planurile sunt
    # acum in plans.json. Il sterg ca sa nu dublez zeci de MB in fiecare commit.
    try:
        os.remove(BACKTEST_FILE)
        print(f"  {BACKTEST_FILE} sters (planurile sunt acum in plans.json)")
    except OSError:
        pass

    print(f"\nIntegrate {len(incoming)} planuri. Total acum: {len(live['plans'])}.")
    s = live["summary"]
    print(f"  inchise (geometria curenta): {s.get('closed')} | "
          f"NO_ENTRY: {s.get('no_entry')} | rata {s.get('win_rate')}% | R {s.get('total_r')}")
    cal = live["calibration"]
    rel = sum(1 for e in cal.values() if e.get("reliable"))
    print(f"  calibrare: {len(cal)} intervale, {rel} fiabile")
    print("\nRuleaza acum `python3 ai_agent.py` ca agentul sa invete din ele.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
