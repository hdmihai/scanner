# -*- coding: utf-8 -*-
"""data_reset.py - reconstructia memoriei agentului de la zero (adaptor: operatiile pe disc).

Se executa O SINGURA DATA pe epoca de date (core/plan_audit.DATA_EPOCH), automat, la integrarea
unui backtest regenerat cu codul curent (backtest.py --merge sau merge_backtest.py). Nu exista
un pas manual separat: Actions -> Backtest (timeframe 4h, merge true) face tot.

CE SE STERGE
  - toate planurile de backtest vechi (inlocuite de cele regenerate acum);
  - planurile din alte geometrii si planurile live create de versiuni anterioare ale agentului
    (vector de caracteristici incomplet) - core/plan_audit.rebuild_selection;
  - arhiva planurilor din versiunile vechi (data/archive/) si statisticile "legacy" calculate
    din ea - nu mai intra in nicio decizie;
  - starea cercetarii autonome (regulile se cauta din nou pe datele noi);
  - modelul agentului (se reantreneaza de la zero, in pasul urmator al workflow-ului);
  - traiectoria ponderilor euristice si evaluarile vechi din istoricul scanarilor (pastrez doar
    ultimele SCAN_HISTORY_KEEP scanari, cat cere persistenta semnalelor).
CE RAMANE
  - planurile live complete ale geometriei curente: singura masuratoare reala. Stergerea lor ar
    ascunde rezultatul live si ar face statusul agentului sa para mai bun decat e.
CE SE SCRIE
  - data/scoring_policy.json: ponderile de scor cu care s-a generat backtest-ul, inghetate - live
    si backtest scoreaza pe aceeasi scara;
  - data/plan_audit.json: rezumatul reconstructiei (ce s-a scos si de ce);
  - plans.json: data_epoch, ca reconstructia sa nu se repete.
"""

import json
import os
import shutil
import time

from core import agent as agent_core
from core import evidence as ev_mod
from core import plan_audit as audit_core

DATA_DIR = "data"
PLANS_FILE = os.path.join(DATA_DIR, "plans.json")
ARCHIVE_DIR = os.path.join(DATA_DIR, "archive")
RESEARCH_FILE = os.path.join(DATA_DIR, "research.json")
AGENT_FILE = os.path.join(DATA_DIR, "agent_model.json")
POLICY_FILE = os.path.join(DATA_DIR, "scoring_policy.json")
WEIGHTS_FILE = os.path.join(DATA_DIR, "weights.json")
WEIGHTS_HISTORY_FILE = os.path.join(DATA_DIR, "weights_history.json")
HISTORY_FILE = os.path.join(DATA_DIR, "scan_history.json")
AUDIT_FILE = os.path.join(DATA_DIR, "plan_audit.json")
SCAN_HISTORY_KEEP = 72
DEFAULT_WEIGHTS = {"trend": 1.0, "momentum": 1.0, "volatility": 1.0, "volume": 1.0}


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp, path)


def needs_rebuild(store):
    return (store or {}).get("data_epoch") != audit_core.DATA_EPOCH


def live_family(store):
    """Familia de geometrie a celui mai nou plan live (cea pe care ruleaza scanarea), sau None."""
    live = [p for p in (store or {}).get("plans") or [] if p.get("source") != "backtest" and p.get("geometry")]
    if not live:
        return None
    g = max(live, key=lambda p: p.get("created_ts") or 0)["geometry"]
    parts = g.split("-")
    return "-".join(parts[:2]) if len(parts) >= 2 else g


def check_family(store, family):
    """Un backtest pe alt timeframe decat scanarea live nu are voie sa fie integrat: plans.json ar
    arhiva planurile live ca "geometrie veche" (masurat: 26.175 de planuri v6-4h ajunse astfel in
    arhiva). Intoarce mesajul de eroare sau None."""
    lf = live_family(store)
    if lf and lf != family:
        return (f"backtest-ul ruleaza pe familia {family}, scanarea live pe {lf}. Ruleaza backtest-ul cu "
                f"timeframe {lf.split('-')[-1]} - altfel planurile live ar fi arhivate ca geometrie veche.")
    return None


def scoring_weights(store):
    """Ponderile de scor pentru backtest: cele inghetate daca exista politica; la o reconstructie,
    ponderile neutre (egale) - fara deriva euristica mostenita; altfel weights.json (ca inainte)."""
    pol = _load(POLICY_FILE, None) or {}
    if pol.get("frozen") and isinstance(pol.get("weights"), dict):
        return dict(pol["weights"]), "inghetate (scoring_policy.json)"
    if needs_rebuild(store):
        return dict(DEFAULT_WEIGHTS), "neutre - reconstructie de la zero"
    return _load(WEIGHTS_FILE, dict(DEFAULT_WEIGHTS)), "weights.json"


def rebuild(store, fresh, weights, same_family, family, fv):
    """Aplica reconstructia pe `store` (modificat pe loc) si pe disc. `fresh` = planurile de
    backtest regenerate acum (deja numerotate de apelant). Intoarce rezumatul."""
    now = time.time()
    when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(now))
    # ev_analyst a aparut odata cu aceasta epoca: planurile live verificate dinaintea ei raman (sunt
    # singura masuratoare reala; reevaluate pe lumanarile reale, identice). Agentul ii masoara
    # prezenta live doar de la prima aparitie (core/agent.feature_skew).
    keep, removed = audit_core.rebuild_selection(store.get("plans") or [], ev_mod.FEATURE_KEYS,
                                                 agent_core.PENDING_DEFAULT, same_family)
    store["plans"] = keep + list(fresh)
    by = {}
    for _p, why in removed:
        k = why.split(" (")[0]
        by[k] = by.get(k, 0) + 1
    live_removed = [audit_core.compact_record(p, why, when) for p, why in removed if p.get("source") != "backtest"]

    deleted = []
    if os.path.isdir(ARCHIVE_DIR):
        shutil.rmtree(ARCHIVE_DIR)
        deleted.append("data/archive/ (planurile versiunilor vechi si statisticile legacy)")
    for path, what in ((RESEARCH_FILE, "starea cercetarii autonome"), (AGENT_FILE, "modelul agentului")):
        if os.path.exists(path):
            os.remove(path)
            deleted.append(f"{os.path.basename(path)} ({what})")
    hist = _load(HISTORY_FILE, None)
    if isinstance(hist, list) and len(hist) > SCAN_HISTORY_KEEP:
        _save(HISTORY_FILE, hist[-SCAN_HISTORY_KEEP:])
        deleted.append(f"scan_history.json: {len(hist) - SCAN_HISTORY_KEEP} scanari vechi (evaluari euristice)")

    w = {k: float(weights.get(k, 1.0)) for k in DEFAULT_WEIGHTS}
    _save(POLICY_FILE, {"frozen": True, "weights": w, "since": when, "epoch": audit_core.DATA_EPOCH,
                        "reason": "aceleasi ponderi de scor pentru backtest si live; ajustarea euristica "
                                  "ramane doar diagnostic"})
    _save(WEIGHTS_FILE, w)
    _save(WEIGHTS_HISTORY_FILE, [{"ts": now, "time": when, **w}])

    summary = {"when": when, "ts": now, "epoch": audit_core.DATA_EPOCH, "family": family, "fv": fv,
               "kept_live": len(keep), "backtest_new": len(fresh), "removed": by,
               "removed_total": len(removed), "deleted": deleted, "weights": w}
    store["data_epoch"] = audit_core.DATA_EPOCH
    store["rebuilt"] = {k: summary[k] for k in ("when", "epoch", "kept_live", "backtest_new", "removed_total")}

    log = _load(AUDIT_FILE, {}) or {}
    log["rebuild"] = summary
    # separat de `removed` (planurile scoase de audit pentru date false): acestea erau corecte la
    # vremea lor, dar create de o versiune anterioara a agentului
    log["rebuild_removed_live"] = live_removed[-500:]
    _save(AUDIT_FILE, log)

    print("\nRECONSTRUCTIA MEMORIEI (epoca %d)" % audit_core.DATA_EPOCH)
    for k, n in sorted(by.items(), key=lambda kv: -kv[1]):
        print(f"  scoase: {n:6d}  {k}")
    print(f"  pastrate: {len(keep)} planuri live complete; adaugate: {len(fresh)} planuri de backtest regenerate")
    for d in deleted:
        print(f"  sters: {d}")
    print(f"  ponderi de scor inghetate: {w}")
    return summary


def backtest_is_current(bt_store, fv):
    """Un backtest salvat (merge_only) se poate integra la reconstructie doar daca a fost generat
    de codul curent (aceeasi versiune de caracteristici, aceeasi epoca)."""
    meta = (bt_store or {}).get("meta") or {}
    return meta.get("fv") == fv and meta.get("data_epoch") == audit_core.DATA_EPOCH

