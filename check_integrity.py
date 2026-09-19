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


def check_no_direct_plan_writes():
    """Niciun modul nu are voie sa scrie plans.json altfel decat prin save_plans.

    DE CE: garda de dimensiune si arhivarea traiesc in plan_tracker.save_plans().
    Orice modul care scrie fisierul direct le ocoleste pe amandoua. backtest.py
    a facut exact asta si a produs un plans.json de 100.98 MB, respins de GitHub
    de doua ori - desi plan_tracker.py continea garda si `check_integrity` o
    testa cu succes. Verificarea trecea, pentru ca testa functia, nu CINE o
    apeleaza.
    """
    for name in sorted(os.listdir(ROOT)):
        if not name.endswith(".py") or name in ("plan_tracker.py", "compact_plans.py",
                                                "check_integrity.py"):
            continue
        src = read(name) or ""
        for i, line in enumerate(src.split("\n"), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if re.search(r"\bsave_json\s*\(\s*PLANS_FILE", stripped):
                problems.append(
                    f"{name}:{i} scrie plans.json direct cu save_json, ocolind "
                    f"plan_tracker.save_plans() - deci ocolind arhivarea si garda "
                    f"de dimensiune. Exact asta a produs 100.98 MB si push respins. "
                    f"Foloseste plan_tracker.save_plans(store).")


def check_compaction_step():
    """Pasul de compactare trebuie sa existe si sa ruleze INAINTE de commit."""
    wf_dir = os.path.join(ROOT, ".github", "workflows")
    if not os.path.isdir(wf_dir):
        return
    if not os.path.exists(os.path.join(ROOT, "compact_plans.py")):
        problems.append(
            "compact_plans.py lipseste. E singurul mecanism care garanteaza "
            "dimensiunea lui plans.json indiferent ce modul l-a scris.")
        return
    for name in sorted(os.listdir(wf_dir)):
        if not name.endswith((".yml", ".yaml")):
            continue
        content = read(os.path.join(".github", "workflows", name)) or ""
        if "git commit" not in content:
            continue
        if "compact_plans.py" not in content:
            problems.append(
                f"{name} face commit dar nu ruleaza compact_plans.py inainte. "
                f"Fara el, un plans.json prea mare ajunge la push si e respins.")
            continue
        if content.index("compact_plans.py") > content.index("git commit"):
            problems.append(
                f"{name} ruleaza compact_plans.py DUPA git commit - prea tarziu. "
                f"Trebuie sa fie inainte, altfel se comite fisierul necompactat.")


def check_archival_behavior():
    """Verifica COMPORTAMENTAL ca arhivarea per geometrie chiar functioneaza -
    nu doar ca textul 'archive' apare undeva in sursa.

    DE CE COMPORTAMENTAL: verificarile pe text (cauta un nume de functie, un
    cuvant cheie) au dat deja fals pozitiv o data in acest proiect - un
    GEOMETRY_VERSION mutat intr-o functie tot trecea testul pe text, desi
    comportamentul se schimbase. Aici construiesc un store mic cu planuri din
    doua geometrii, rulez save_plans, si verific ce a ramas cu adevarat.

    Motivul pentru care asta conteaza specific: plans.json a ajuns la 100.83 MB
    si a fost respins de GitHub, in ciuda faptului ca o garda de dimensiune
    exista in cod. check_integrity.py nu verifica pana acum daca acea garda
    arhiveaza sau doar taie oarba - iar taierea oarba poate elimina planuri din
    geometria ACTIVA daca se ruleaza backtest de mai multe ori pe rand.
    """
    import tempfile
    sys.path.insert(0, ROOT)
    for m in ("plan_tracker",):
        globals().pop(m, None)
    try:
        import importlib
        if "plan_tracker" in sys.modules:
            importlib.reload(sys.modules["plan_tracker"])
        import plan_tracker as pt
    except Exception as exc:
        problems.append(f"nu pot importa plan_tracker pentru testul de arhivare: {exc}")
        return

    if not hasattr(pt, "archive_stale_plans"):
        problems.append(
            "plan_tracker.py nu are archive_stale_plans(). Fara arhivare per "
            "geometrie, planurile din geometrii vechi se acumuleaza la nesfarsit "
            "in plans.json si il pot duce peste limita de 100 MB a GitHub - "
            "exact ce s-a intamplat (86.097 planuri, 100.83 MB, push respins).")
        return

    with tempfile.TemporaryDirectory() as tmp:
        orig_plans, orig_dir, orig_idx = pt.PLANS_FILE, pt.ARCHIVE_DIR, pt.ARCHIVE_INDEX_FILE
        pt.PLANS_FILE = os.path.join(tmp, "plans.json")
        pt.ARCHIVE_DIR = os.path.join(tmp, "archive")
        pt.ARCHIVE_INDEX_FILE = os.path.join(pt.ARCHIVE_DIR, "_index.json")
        try:
            old_geo = "test-old-geometry"
            store = {"next_id": 21, "plans": [
                {"id": i, "symbol": "X", "direction": "LONG", "state": pt.STATE_SL,
                 "realized_r": 0.5, "geometry": old_geo, "created_ts": i,
                 "closed_ts": i + 1} for i in range(1, 11)
            ] + [
                {"id": i, "symbol": "X", "direction": "LONG", "state": pt.STATE_SL,
                 "realized_r": 0.5, "geometry": pt.GEOMETRY_VERSION, "created_ts": i,
                 "closed_ts": i + 1} for i in range(11, 21)
            ]}
            pt.save_plans(store)
            remaining_geos = {p["geometry"] for p in store["plans"]}
            if old_geo in remaining_geos:
                problems.append(
                    f"archive_stale_plans nu a mutat planurile din geometria veche "
                    f"'{old_geo}' - au ramas in plans.json in loc sa fie arhivate.")
            if pt.GEOMETRY_VERSION not in remaining_geos:
                problems.append(
                    "archive_stale_plans a eliminat din greseala planuri din "
                    "GEOMETRY_VERSION curenta - ar trebui sa ramana toate.")
            arch_path = pt._archive_filename(old_geo)
            if not os.path.exists(arch_path):
                problems.append(
                    f"planurile din '{old_geo}' au disparut fara sa ajunga intr-un "
                    f"fisier de arhiva - date pierdute, nu doar mutate.")
            else:
                arch = pt.load_json(arch_path, {"plans": []})
                if len(arch.get("plans", [])) != 10:
                    problems.append(
                        f"arhiva pentru '{old_geo}' are {len(arch.get('plans', []))} "
                        f"planuri, asteptam 10 - date pierdute la arhivare.")
        finally:
            pt.PLANS_FILE, pt.ARCHIVE_DIR, pt.ARCHIVE_INDEX_FILE = orig_plans, orig_dir, orig_idx


def check_family_preservation():
    """Un proces care nu stie ce capabilitati a detectat alt proces nu are voie
    sa-i stearga datele.

    DE CE: scanarea live fixeaza geometria la v6-4h-obf dupa ce sondeaza bursa.
    ai_agent.py si compact_plans.py ruleaza ca procese SEPARATE, fara SCAN_CAPS
    in mediu, deci calculeaza v6-4h-o. Daca arhivarea compara semnatura EXACTA,
    al doilea proces sterge tot ce a scris primul. Masurat: scanerul crea 7
    planuri, ai_agent le arhiva pe toate, plans.json ramanea gol - tacut, la
    fiecare rulare.

    Testez COMPORTAMENTAL: planuri din doua semnaturi ale aceleiasi familii
    trebuie sa supravietuiasca amandoua.
    """
    import tempfile, importlib
    sys.path.insert(0, ROOT)
    try:
        if "plan_tracker" in sys.modules:
            importlib.reload(sys.modules["plan_tracker"])
        import plan_tracker as pt
    except Exception as exc:
        problems.append(f"nu pot importa plan_tracker: {exc}")
        return

    parts = pt.GEOMETRY_VERSION.split("-")
    if len(parts) < 3:
        notes.append(f"geometria {pt.GEOMETRY_VERSION} nu are forma vN-tf-caps")
        return
    sibling = "-".join(parts[:2]) + "-" + parts[2] + "bf"   # alta semnatura, aceeasi familie

    with tempfile.TemporaryDirectory() as tmp:
        orig = (pt.PLANS_FILE, pt.ARCHIVE_DIR, pt.ARCHIVE_INDEX_FILE)
        pt.PLANS_FILE = os.path.join(tmp, "plans.json")
        pt.ARCHIVE_DIR = os.path.join(tmp, "archive")
        pt.ARCHIVE_INDEX_FILE = os.path.join(pt.ARCHIVE_DIR, "_index.json")
        try:
            store = {"next_id": 21, "plans": [
                {"id": i, "symbol": "X", "direction": "LONG", "state": pt.STATE_SL,
                 "realized_r": 0.5, "geometry": sibling, "created_ts": i,
                 "closed_ts": i + 1} for i in range(1, 6)
            ] + [
                {"id": i, "symbol": "X", "direction": "LONG", "state": pt.STATE_SL,
                 "realized_r": 0.5, "geometry": pt.GEOMETRY_VERSION, "created_ts": i,
                 "closed_ts": i + 1} for i in range(6, 11)
            ]}
            pt.save_plans(store)
            left = {p["geometry"] for p in store["plans"]}
            if sibling not in left:
                problems.append(
                    f"save_plans a sters planurile cu geometria '{sibling}' desi e "
                    f"din aceeasi familie ca '{pt.GEOMETRY_VERSION}'. Un proces "
                    f"care ruleaza fara SCAN_CAPS ar distruge datele scrise de "
                    f"scanarea live, tacut, la fiecare rulare.")
            if len(store["plans"]) != 10:
                problems.append(
                    f"save_plans a pastrat {len(store['plans'])} din 10 planuri "
                    f"ale familiei curente - pierdere de date.")
        finally:
            pt.PLANS_FILE, pt.ARCHIVE_DIR, pt.ARCHIVE_INDEX_FILE = orig


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
    check_archival_behavior()
    check_family_preservation()
    check_no_direct_plan_writes()
    check_compaction_step()
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
