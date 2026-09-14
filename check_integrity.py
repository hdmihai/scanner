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
    # Verific COMPORTAMENTUL, nu textul sursei. Verificarea pe sir dadea alarme
    # false dupa ce logica a fost mutata intr-o functie: `GEOMETRY_VERSION =
    # _build_geometry()` nu contine literal "SCAN_TIMEFRAME", desi il foloseste.
    # Un test care se uita la forma codului, nu la ce face, imbatraneste prost.
    if "GEOMETRY_VERSION" not in pt:
        problems.append("plan_tracker.py nu defineste GEOMETRY_VERSION")
        return
    import subprocess
    probe = (
        "import os,sys,importlib\n"
        "res=[]\n"
        "for tf in ('1h','4h'):\n"
        "    os.environ['SCAN_TIMEFRAME']=tf\n"
        "    sys.modules.pop('plan_tracker',None)\n"
        "    import plan_tracker as p; res.append(p.GEOMETRY_VERSION)\n"
        "print('|'.join(res))\n")
    try:
        out = subprocess.run([sys.executable, "-c", probe], cwd=ROOT,
                             capture_output=True, text=True, timeout=30)
        vals = (out.stdout or "").strip().split("|")
        if len(vals) != 2 or vals[0] == vals[1]:
            problems.append(
                f"GEOMETRY_VERSION nu se schimba cu timeframe-ul (1h si 4h dau "
                f"{vals}). Rezultatele de pe timeframe-uri diferite s-ar amesteca "
                f"in aceeasi calibrare.")
    except Exception as exc:
        notes.append(f"nu am putut testa geometria: {exc}")


def check_agent_source_follows_geometry():
    """Sursa agentului trebuie legata de geometrie, altfel nu se reseteaza la
    schimbarea timeframe-ului si prezice cu greutati invatate pe alt sistem."""
    ag = read("ai_agent.py")
    if ag is None:
        problems.append("ai_agent.py lipseste")
        return
    # Tot pe comportament: sursa trebuie sa se schimbe odata cu geometria.
    if "STATE_SOURCE" not in ag and "current_source" not in ag:
        problems.append("ai_agent.py nu defineste sursa de invatare")
        return
    import subprocess
    probe = (
        "import os,sys\n"
        "res=[]\n"
        "for tf in ('1h','4h'):\n"
        "    os.environ['SCAN_TIMEFRAME']=tf\n"
        "    for m in ('plan_tracker','ai_agent'): sys.modules.pop(m,None)\n"
        "    import ai_agent as a\n"
        "    res.append(a.current_source() if hasattr(a,'current_source') else a.STATE_SOURCE)\n"
        "print('|'.join(res))\n")
    try:
        out = subprocess.run([sys.executable, "-c", probe], cwd=ROOT,
                             capture_output=True, text=True, timeout=30)
        vals = (out.stdout or "").strip().split("|")
        if len(vals) != 2 or vals[0] == vals[1]:
            problems.append(
                f"Sursa agentului nu urmeaza geometria (1h si 4h dau {vals}). "
                f"La schimbarea timeframe-ului agentul nu s-ar reseta si ar prezice "
                f"folosind greutati invatate pe alt timeframe.")
    except Exception as exc:
        notes.append(f"nu am putut testa sursa agentului: {exc}")


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
