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
    pt += read(os.path.join("core", "plans.py")) or ""      # logica e in nucleu (Etapa 2)
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
        "    for m in [k for k in sys.modules if k == 'plan_tracker' or k.startswith('core')]: sys.modules.pop(m,None)\n"
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
    ag += read(os.path.join("core", "agent.py")) or ""      # logica e in nucleu (Etapa 2)
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
        "    for m in [k for k in sys.modules if k in ('plan_tracker','ai_agent') or k.startswith('core')]: sys.modules.pop(m,None)\n"
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
    pt = (read("plan_tracker.py") or "") + (read(os.path.join("core", "plans.py")) or "")
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


def check_file_identity():
    """Continutul fiecarui fisier trebuie sa fie al LUI. Documentatia de la
    inceputul modulelor incepe cu numele fisierului; daca numeste ALT fisier,
    continutul a fost urcat in locul gresit. S-a intamplat la o actualizare
    manuala: self_check.py continea codul lui generate_dashboard.py (auto-
    diagnosticul a fost dezactivat fara nicio eroare vizibila), iar un workflow
    a ajuns in radacina repo-ului, unde GitHub nu il ruleaza niciodata."""
    import ast as _ast
    import re as _re
    for f in sorted(os.listdir(".")):
        if not f.endswith(".py"):
            continue
        try:
            doc = _ast.get_docstring(_ast.parse(read(f) or "")) or ""
        except SyntaxError:
            continue
        first = next((l.strip() for l in doc.splitlines() if l.strip()), "")
        m = _re.match(r"^([A-Za-z0-9_]+\.py)\b", first)
        if m and m.group(1) != f and os.path.exists(m.group(1)):
            problems.append(f"{f} contine codul lui {m.group(1)} (documentatia lui incepe cu "
                            f"'{m.group(1)}') - continut urcat in fisierul gresit.")
    for f in sorted(os.listdir(".")):
        if f.endswith((".yml", ".yaml")):
            # avertisment, nu blocare: fisierul e inofensiv acolo, doar nu ruleaza
            print(f"[avertisment] {f} e in radacina repo-ului: GitHub ruleaza workflow-urile doar "
                  "din .github/workflows/ - muta-l acolo.")


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
        # Compactarea e necesara doar daca workflow-ul SALVEAZA planurile - adica
        # adauga tot data/ sau plans.json. Un workflow care salveaza doar
        # token_metadata.json si docs/ (metadata.yml) nu atinge plans.json.
        import re as _re
        adds = " ".join(_re.findall(r"git add ([^\n]+)", content))
        if not (_re.search(r"(^|\s)data/?(\s|$)", adds) or "plans.json" in adds):
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


def check_feature_extension():
    """Adaugarea unei caracteristici NU are voie sa reseteze invatarea.

    DE CE: proiectul a trecut prin 7 versiuni de geometrie, iar la fiecare
    agentul repornea de la zero. Masurat pe istoricul real: 72.837 de planuri
    aruncate, din care ~40.000 pentru schimbari care adaugau doar indicatori.
    Rezultatul planurilor deja inchise nu se schimba cand adaug o evidenta -
    se schimba doar vectorul de intrare. Resetul era pur si simplu gresit, si
    e motivul pentru care agentul nu trecea niciodata pragul de activare:
    ajungea aproape, apoi reincepea.

    Testez COMPORTAMENTAL: un model existent plus caracteristici noi trebuie
    sa pastreze exemplele si greutatile invatate.
    """
    import importlib
    sys.path.insert(0, ROOT)
    try:
        for m in ("core.plans", "core.agent", "plan_tracker", "ai_agent"):
            if m in sys.modules:
                importlib.reload(sys.modules[m])
        import plan_tracker as pt
        import ai_agent as ag
    except Exception as exc:
        problems.append(f"nu pot importa pentru testul de caracteristici: {exc}")
        return

    if not hasattr(pt, "FEATURE_VERSION"):
        problems.append(
            "plan_tracker.py nu are FEATURE_VERSION separat de GEOMETRY_VERSION. "
            "Fara separare, orice caracteristica noua reseteaza agentul si arunca "
            "toate exemplele invatate - desi rezultatele planurilor nu s-au schimbat.")
        return

    src = ag.current_source()
    if pt.FEATURE_VERSION in src:
        problems.append(
            f"sursa agentului ({src}) include versiunea de caracteristici. "
            f"Asta forteaza reset la fiecare indicator adaugat - exact ce trebuia "
            f"evitat. Sursa trebuie sa depinda DOAR de GEOMETRY_VERSION.")

    # Verific COMPORTAMENTAL ca extinderea chiar se aplica. Prima varianta a
    # acestui mecanism scria in `model.weights`, un obiect construit mai jos in
    # main() - deci extinderea nu se aplica NICIODATA, iar caracteristicile noi
    # erau ignorate tacut. Codul parea corect si testul pe logica izolata trecea.
    src_txt = (read("ai_agent.py") or "") + (read(os.path.join("core", "agent.py")) or "")
    if "Caracteristici noi adaugate fara reset" in src_txt:
        if "state.get(\"model\")" not in src_txt and "state[\"model\"]" not in src_txt:
            problems.append(
                "extinderea setului de caracteristici nu opereaza pe state[\"model\"] "
                "[\"weights\"]. Daca scrie intr-un obiect construit ulterior, "
                "caracteristicile noi sunt ignorate tacut si modelul ramane cu "
                "setul vechi de greutati.")

    # geometria trebuie sa ramana in sursa: acolo resetul chiar e necesar
    # Sursa trebuie sa urmeze FAMILIA de geometrie (versiune + timeframe): la
    # schimbarea regulilor planului agentul se reseteaza, dar nu la simpla
    # diferenta de capabilitati dintre scanarea live si agent.
    if getattr(pt, "GEOMETRY_FAMILY", pt.GEOMETRY_VERSION) not in src:
        problems.append(
            f"sursa agentului ({src}) nu include GEOMETRY_VERSION. La schimbarea "
            f"regulilor planului agentul NU s-ar reseta, si ar prezice folosind "
            f"greutati invatate pe rezultate care nu mai sunt comparabile.")


def check_signal_block_uses_own_series():
    """Evidentele fiecarui semnal trebuie calculate DOAR din seria acelui semnal.

    DE CE: in bucla per semnal, `candles` e al semnalului, dar modulele adaugate
    ulterior primeau `closes` din exterior - inchiderile candidatului principal.
    Ichimoku, regimul, Elliott si lichiditatea combinau astfel maximele unui
    token cu inchiderile altuia (NEAR aparea "SUB NOR" desi era peste), iar
    agentul invata din caracteristici corupte. Compila si rula fara nicio eroare.

    Blocul traieste acum in functia build_signal_context (folosita pe TOATE
    bursele). Verificarea cauta functia; daca nu o gaseste, raporteaza - o garda
    care nu mai gaseste ce verifica nu are voie sa treaca in tacere.
    """
    import re as _re
    # functia traieste in nucleu (Etapa 2); o caut acolo, apoi in locurile vechi
    src, a = "", -1
    for cand in (os.path.join("core", "analysis.py"), os.path.join("core", "scan.py"), "crypto_ai_scanner.py"):
        src = read(cand) or ""
        a = src.find("def build_signal_context(")
        if a >= 0:
            break
    if a < 0:
        problems.append("nucleul nu mai are build_signal_context - garda seriei proprii per semnal "
                        "nu mai poate verifica blocul de evidente.")
        return
    b = src.find("\ndef ", a + 10)
    body = src[a:b if b > 0 else len(src)]
    doc_end = body.find('"""', body.find('"""') + 3) + 3
    stray = _re.findall(r"(?<![\w.])(closes|highs|lows)(?![\w])", body[doc_end:])
    if stray:
        problems.append(
            f"build_signal_context foloseste {sorted(set(stray))} ({len(stray)}x) in loc de "
            f"closes_s / highs_s / lows_s: indicatorii s-ar calcula cu seria altui token.")
    if "book_levels" not in body[doc_end:]:
        problems.append("build_signal_context nu mai primeste order book-ul propriu al semnalului "
                        "(book_levels).")


def check_exchange_adapters():
    """Fiecare bursa din registru are modulul ei in adapters/exchanges/, cu acelasi id,
    iar fatada exchanges.py declara exact capabilitatile adaptoarelor. Verificat prin
    import real, intr-un proces separat - un modul urcat in fisierul gresit sau lipsa
    ar opri altfel scanarea abia la rulare."""
    import subprocess
    probe = (
        "import json, os, sys\n"
        "sys.path.insert(0, os.getcwd())\n"
        "import exchanges as E\n"
        "from adapters import exchanges as A\n"
        "out = {'ids': A.ids(), 'files': {}, 'reg': E.REGISTRY == {a.id: {'label': a.label, "
        "'declared': list(a.declared)} for a in A.all_adapters()}, 'order': E.DEFAULT_ORDER == A.ids()}\n"
        "for a in A.all_adapters():\n"
        "    out['files'][a.id] = (type(a).__module__ == 'adapters.exchanges.' + a.id)\n"
        "print(json.dumps(out))\n")
    import json as _json
    try:
        r = subprocess.run([sys.executable, "-B", "-c", probe], cwd=ROOT, capture_output=True,
                           text=True, timeout=60)
    except Exception as exc:
        problems.append(f"adaptoarele de bursa nu au putut fi verificate: {exc}")
        return
    if r.returncode != 0 or not (r.stdout or "").strip():
        last = ((r.stderr or "").strip().splitlines() or ["eroare necunoscuta"])[-1]
        problems.append(f"adaptoarele de bursa nu se pot importa ({last[:160]}) - scanarea ar esua "
                        f"la pornire. Verifica fisierele din adapters/exchanges/.")
        return
    out = _json.loads(r.stdout.strip().splitlines()[-1])
    for eid, ok in out["files"].items():
        if not ok:
            problems.append(f"adaptorul '{eid}' nu e definit in adapters/exchanges/{eid}.py")
    if not out["reg"] or not out["order"]:
        problems.append("exchanges.py nu mai reflecta registrul adaptoarelor (capabilitati sau ordine).")


def check_core_is_pure():
    """Regulile arhitecturii hexagonale, verificate pe sursa, la fiecare scanare.

    1. NUCLEUL (core/) nu face I/O: fara retea (ccxt, requests, urllib, socket,
       smtplib, subprocess), fara fisiere (open, os.makedirs/replace/remove,
       os.path.exists, json.load/json.dump pe fisiere, shutil). Datele intra prin
       porturi; adaptoarele le aduc de la burse, din fisiere sau din Telegram.
    2. Nucleul importa DOAR din nucleu, din porturi si din biblioteca standard -
       niciodata un adaptor sau un modul vechi din radacina (plan_tracker, ...).
    3. PORTURILE (ports/) nu depind de nimic din proiect.
    4. PREZENTAREA (dashboard/) nu face retea si nu importa adaptoare de date.

    DE CE: o dependenta ascunsa (un open() uitat intr-o functie de calcul, un
    import de ccxt "doar pentru un apel") rupe separarea in tacere - codul merge,
    dar nucleul nu mai poate fi testat izolat si o bursa cazuta il poate opri.
    """
    import ast as _ast
    net = {"ccxt", "requests", "urllib", "urllib3", "http", "socket", "smtplib", "subprocess", "sqlite3", "shutil"}
    root_mods = ({f[:-3] for f in os.listdir(ROOT) if f.endswith(".py")}
                 | {"adapters", "dashboard", "selfrepair", "improve", "tests", "core", "ports"})
    file_calls = {("os", "makedirs"), ("os", "replace"), ("os", "remove"), ("os", "unlink"), ("os", "rename"),
                  ("os", "fsync"), ("os", "listdir"), ("os", "walk"), ("os", "rmdir"), ("path", "exists"),
                  ("path", "isfile"), ("path", "isdir"), ("path", "getsize"), ("path", "getmtime"),
                  ("json", "load"), ("json", "dump")}
    for pkg, allowed_pkgs in (("core", {"core", "ports"}), ("ports", {"ports"})):
        base = os.path.join(ROOT, pkg)
        if not os.path.isdir(base):
            continue
        for f in sorted(os.listdir(base)):
            if not f.endswith(".py"):
                continue
            rel = os.path.join(pkg, f)
            try:
                tree = _ast.parse(read(rel) or "")
            except SyntaxError:
                continue
            for n in _ast.walk(tree):
                mods = []
                if isinstance(n, _ast.Import):
                    mods = [a.name for a in n.names]
                elif isinstance(n, _ast.ImportFrom) and n.module and not n.level:
                    mods = [n.module]
                for m in mods:
                    top = m.split(".")[0]
                    if top in net:
                        problems.append(f"{rel}:{n.lineno} importa {m} - {pkg}/ nu are voie sa faca retea sau "
                                        f"procese externe; foloseste un port.")
                    elif top in root_mods and top not in allowed_pkgs:
                        problems.append(f"{rel}:{n.lineno} importa {m} - {pkg}/ poate importa doar "
                                        f"{sorted(allowed_pkgs) or 'biblioteca standard'} (regula dependentelor).")
                if pkg != "core" or not isinstance(n, _ast.Call):
                    continue
                fn = n.func
                if isinstance(fn, _ast.Name) and fn.id == "open":
                    problems.append(f"{rel}:{n.lineno} deschide un fisier - nucleul citeste si scrie doar prin porturi.")
                elif isinstance(fn, _ast.Attribute) and isinstance(fn.value, (_ast.Name, _ast.Attribute)):
                    owner = fn.value.id if isinstance(fn.value, _ast.Name) else fn.value.attr
                    if (owner, fn.attr) in file_calls:
                        problems.append(f"{rel}:{n.lineno} apeleaza {owner}.{fn.attr} - I/O pe disc in nucleu; "
                                        f"foloseste portul de stocare.")
    # 4. PREZENTAREA (dashboard/) doar citeste starea salvata si deseneaza: fara retea si fara
    #    adaptoarele care aduc date (altseason, istoricul pe 10 ani, CoinGecko, burse) - numele
    #    si regulile domeniului le ia din nucleu. Altfel generarea paginii ar putea face cereri
    #    sau ar depinde de un adaptor care se schimba.
    pres_allowed = {"dashboard", "core", "ports", "chart_render", "exchanges"}
    for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "dashboard")):
        for f in sorted(files):
            if not f.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, f), ROOT)
            try:
                tree = _ast.parse(read(rel) or "")
            except SyntaxError:
                continue
            for n in _ast.walk(tree):
                mods = []
                if isinstance(n, _ast.Import):
                    mods = [a.name for a in n.names]
                elif isinstance(n, _ast.ImportFrom) and n.module and not n.level:
                    mods = [n.module]
                for m in mods:
                    top = m.split(".")[0]
                    if top in net:
                        problems.append(f"{rel}:{n.lineno} importa {m} - prezentarea (dashboard/) nu face retea.")
                    elif top in root_mods and top not in pres_allowed:
                        problems.append(f"{rel}:{n.lineno} importa {m} - prezentarea (dashboard/) poate importa doar "
                                        f"{sorted(pres_allowed)} (regula dependentelor); domeniul vine din nucleu.")


def check_package_identity():
    """Ca check_file_identity, pentru modulele din pachete: documentatia unui modul
    din dashboard/ sau adapters/ incepe cu numele lui complet (ex. `dashboard.page`).
    Daca numeste ALT modul existent, continutul a fost urcat in fisierul gresit."""
    import ast as _ast
    import re as _re
    for pkg in ("dashboard", "adapters", "core", "ports"):
        base = os.path.join(ROOT, pkg)
        if not os.path.isdir(base):
            continue
        for dirpath, _dirs, files in os.walk(base):
            for f in sorted(files):
                if not f.endswith(".py"):
                    continue
                full = os.path.join(dirpath, f)
                rel = os.path.relpath(full, ROOT)
                mod = rel[:-3].replace(os.sep, ".")
                if mod.endswith(".__init__"):
                    mod = mod[: -len(".__init__")]
                try:
                    doc = _ast.get_docstring(_ast.parse(read(rel) or "")) or ""
                except SyntaxError:
                    continue
                first = next((l.strip() for l in doc.splitlines() if l.strip()), "")
                m = _re.match(r"^((?:dashboard|adapters|core|ports)(?:\.[A-Za-z0-9_]+)+)\b", first)
                if not m or m.group(1) == mod:
                    continue
                other = os.path.join(ROOT, *m.group(1).split(".")) + ".py"
                if os.path.exists(other):
                    problems.append(f"{rel} contine codul lui {m.group(1)} (documentatia lui incepe cu "
                                    f"'{m.group(1)}') - continut urcat in fisierul gresit.")


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


def check_self_check_step():
    """Auto-diagnosticul trebuie sa RULEZE in scanare, nu doar sa existe.

    DE CE: self_check.py a fost adaugat fara pas in scan.yml. data/self_check.json
    nu a fost scris niciodata, desi dashboard-ul, self_repair.py, ai_agent.py
    (carantina) si plan_tracker.py (filtrul Elliott, modul de siguranta) il
    citesc. Totul arata "verde" - lipsa fisierului inseamna comportament implicit
    - deci eroarea era invizibila. Verific CINE il apeleaza, nu doar ca exista.
    """
    if not os.path.exists(os.path.join(ROOT, "self_check.py")):
        return
    scan = read(os.path.join(".github", "workflows", "scan.yml"))
    if not scan:
        return
    import re as _re
    m = _re.search(r"^\s*run:\s*python3?\s+self_check\.py\b", scan, _re.M)
    if not m:
        problems.append(
            "scan.yml nu ruleaza self_check.py. data/self_check.json nu se scrie, deci "
            "auto-diagnosticul, carantina, filtrul Elliott adaptiv si self_repair.yml "
            "raman inactive in tacere. Adauga pasul dupa ai_agent.py, inainte de "
            "generate_dashboard.py.")
        return
    pos = m.start()
    agent = _re.search(r"^\s*run:\s*python3?\s+ai_agent\.py\b", scan, _re.M)
    dash = _re.search(r"^\s*run:\s*python3?\s+generate_dashboard\.py\b", scan, _re.M)
    if agent and pos < agent.start():
        problems.append("scan.yml ruleaza self_check.py INAINTE de ai_agent.py - "
                        "diagnosticul ar vedea starea agentului de la scanarea anterioara.")
    if dash and pos > dash.start():
        problems.append("scan.yml ruleaza self_check.py DUPA generate_dashboard.py - "
                        "dashboard-ul ar afisa diagnosticul scanarii anterioare.")


def main():
    check_referenced_files_exist()
    check_geometry_versioning()
    check_agent_source_follows_geometry()
    check_decision_gate()
    check_archival_behavior()
    check_family_preservation()
    check_feature_extension()
    check_signal_block_uses_own_series()
    check_exchange_adapters()
    check_package_identity()
    check_core_is_pure()
    check_no_direct_plan_writes()
    check_compaction_step()
    check_file_identity()
    check_self_check_step()
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
