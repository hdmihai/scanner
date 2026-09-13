#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_integrity.py
===================
Verifica daca fisierele din repo sunt o versiune COERENTA intre ele.

DE CE EXISTA
------------
Proiectul are module care depind unele de altele prin conventii, nu prin
import-uri: geometria din plan_tracker trebuie sa se potriveasca cu sursa
agentului, workflow-urile apeleaza scripturi care trebuie sa existe, iar
timeframe-ul trebuie sa fie acelasi peste tot.

Cand se inlocuiesc doar o parte din fisiere - lucru care s-a intamplat
repetat - nimic nu crapa imediat. Sistemul ruleaza si produce rezultate
GRESITE in tacere: date de pe timeframe-uri diferite amestecate in aceeasi
calibrare, o poarta de decizie activa desi masuratorile arata ca strica,
un mod de workflow care apeleaza un fisier inexistent.

Verificarea asta ruleaza ca prim pas in ambele workflow-uri si opreste
rularea cu un mesaj clar in loc sa lase sistemul sa invete din date corupte.

RULARE
------
    python3 check_integrity.py
Cod de iesire 1 daca gaseste o inconsistenta.
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

problems = []
notes = []


def read(path):
    full = os.path.join(ROOT, path)
    if not os.path.exists(full):
        return None
    with open(full, encoding="utf-8") as f:
        return f.read()


def check_referenced_files_exist():
    """Orice `python X.py` dintr-un workflow trebuie sa existe in repo."""
    wf_dir = os.path.join(ROOT, ".github", "workflows")
    if not os.path.isdir(wf_dir):
        notes.append("nu exista .github/workflows - sar peste verificarea de referinte")
        return
    for name in sorted(os.listdir(wf_dir)):
        if not name.endswith((".yml", ".yaml")):
            continue
        content = read(os.path.join(".github", "workflows", name)) or ""
        for script in sorted(set(re.findall(r"python3?\s+([A-Za-z_][\w]*\.py)", content))):
            if not os.path.exists(os.path.join(ROOT, script)):
                problems.append(
                    f"{name} apeleaza `{script}`, dar fisierul NU exista in repo. "
                    f"Rularea ar esua in acel pas.")


def check_geometry_versioning():
    """Geometria trebuie sa includa timeframe-ul, altfel rezultatele de pe
    1h si 4h ajung in aceeasi calibrare."""
    pt = read("plan_tracker.py")
    if pt is None:
        problems.append("plan_tracker.py lipseste")
        return
    m = re.search(r"^GEOMETRY_VERSION\s*=\s*(.+)$", pt, re.M)
    if not m:
        problems.append("plan_tracker.py nu defineste GEOMETRY_VERSION")
        return
    expr = m.group(1).strip()
    if "SCAN_TIMEFRAME" not in expr:
        problems.append(
            f"GEOMETRY_VERSION = {expr} nu include timeframe-ul. Rezultatele de pe "
            f"timeframe-uri diferite s-ar amesteca in aceeasi calibrare, desi "
            f"masuratorile arata ca 1h da -0.035R si 4h +0.023R.")


def check_agent_source_follows_geometry():
    """Sursa agentului trebuie legata de geometrie, altfel nu se reseteaza la
    schimbarea timeframe-ului si prezice cu greutati invatate pe alt sistem."""
    ag = read("ai_agent.py")
    if ag is None:
        problems.append("ai_agent.py lipseste")
        return
    m = re.search(r"^STATE_SOURCE\s*=\s*(.+)$", ag, re.M)
    if not m:
        problems.append("ai_agent.py nu defineste STATE_SOURCE")
        return
    if "GEOMETRY_VERSION" not in m.group(1):
        problems.append(
            f"STATE_SOURCE = {m.group(1).strip()} nu urmeaza GEOMETRY_VERSION. "
            f"La schimbarea timeframe-ului agentul nu s-ar reseta si ar prezice "
            f"folosind greutati invatate pe alt timeframe.")


def check_decision_gate():
    """Poarta trebuie sa fie comutabila si implicit dezactivata - walk-forward
    a aratat ca inrautateste rezultatul in 2 din 3 rulari."""
    pt = read("plan_tracker.py") or ""
    if "USE_DECISION_GATE" not in pt:
        problems.append(
            "plan_tracker.py nu are USE_DECISION_GATE. Poarta filtreaza mereu, "
            "desi walk-forward a masurat -0.0278R/plan pe cel mai mare esantion.")
    if "superior" not in pt:
        problems.append(
            "plan_tracker.decide() nu verifica `superior` inainte de a folosi "
            "probabilitatea agentului. Agentul ar inlocui probabilitatea masurata "
            "chiar cand ordoneaza mai prost decat scorul brut.")


def check_timeframe_consistency():
    """Scanarea si backtest-ul trebuie sa foloseasca acelasi timeframe."""
    scan = read(os.path.join(".github", "workflows", "scan.yml"))
    bt = read(os.path.join(".github", "workflows", "backtest.yml"))
    if scan and "SCAN_TIMEFRAME" not in scan:
        problems.append("scan.yml nu seteaza SCAN_TIMEFRAME - ar folosi implicitul din cod")
    if bt and "SCAN_TIMEFRAME" not in bt:
        problems.append("backtest.yml nu propaga SCAN_TIMEFRAME catre backtest.py")
    if scan and bt:
        s_def = re.search(r"SCAN_TIMEFRAME:\s*\$\{\{\s*inputs\.timeframe\s*\|\|\s*'([^']+)'", scan)
        if s_def:
            notes.append(f"scan.yml ruleaza implicit pe timeframe {s_def.group(1)}")


def check_data_geometry_match():
    """Avertizez daca toate planurile salvate sunt din alta geometrie - atunci
    calibrarea porneste goala si agentul nu are din ce invata."""
    import json
    path = os.path.join(ROOT, "data", "plans.json")
    if not os.path.exists(path):
        notes.append("data/plans.json nu exista inca - normal la prima rulare")
        return
    try:
        with open(path) as f:
            store = json.load(f)
    except Exception as exc:
        problems.append(f"data/plans.json nu poate fi citit: {exc}")
        return
    plans = store.get("plans", [])
    if not plans:
        notes.append("data/plans.json e gol - normal la prima rulare")
        return
    os.environ.setdefault("SCAN_TIMEFRAME", "4h")
    sys.path.insert(0, ROOT)
    try:
        import plan_tracker
        current = plan_tracker.GEOMETRY_VERSION
    except Exception as exc:
        problems.append(f"nu pot importa plan_tracker: {exc}")
        return
    geos = {}
    for p in plans:
        g = p.get("geometry", "v1")
        geos[g] = geos.get(g, 0) + 1
    n_current = geos.get(current, 0)
    notes.append(f"planuri pe geometrii: {geos} (curenta: {current})")
    if n_current == 0:
        notes.append(
            f"ATENTIE: niciun plan din geometria curenta ({current}). Calibrarea "
            f"porneste goala si agentul nu are din ce invata pana la acumulare "
            f"sau pana integrezi un backtest de pe acest timeframe.")


def main():
    check_referenced_files_exist()
    check_geometry_versioning()
    check_agent_source_follows_geometry()
    check_decision_gate()
    check_timeframe_consistency()
    check_data_geometry_match()

    for n in notes:
        print(f"  [info] {n}")

    if problems:
        print()
        print(f"INCONSISTENTE GASITE: {len(problems)}")
        for p in problems:
            print(f"  [!] {p}")
        print()
        print("Repo-ul contine un AMESTEC de versiuni. Inlocuieste toate fisierele")
        print("din setul curent inainte de a rula, altfel sistemul invata din date")
        print("corupte fara sa semnaleze nimic.")
        return 1

    print()
    print("OK: fisierele sunt o versiune coerenta intre ele.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
