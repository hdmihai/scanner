# -*- coding: utf-8 -*-
"""
plan_tracker.py - adaptorul de stocare al planurilor si fatada nucleului core/plans.py.

Logica planurilor (creare, evaluare, calibrare, decizie, geometrie) e in nucleu,
fara fisiere. Aici raman doar operatiile pe disc: plans.json (scriere atomica si
garda de dimensiune), arhiva pe geometrii si citirea atenuarilor din
self_check.json. Celelalte nume (plan_tracker.decide, plan_tracker.GEOMETRY_VERSION,
plan_tracker.TP1_FRACTION, ...) se citesc si se scriu direct in nucleu - vezi
adapters/compat.py. Auto-verificarea poate redirectiona PLANS_FILE si ARCHIVE_DIR
catre un dosar temporar, ca inainte.
"""

import json
import os

from adapters import compat
from core import plans as _core

DATA_DIR = "data"


ARCHIVE_DIR = os.path.join(DATA_DIR, "archive")


ARCHIVE_INDEX_FILE = os.path.join(ARCHIVE_DIR, "_index.json")


PLANS_FILE = os.path.join(DATA_DIR, "plans.json")


def _archive_filename(geometry):
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in geometry) or "unknown"
    if len(safe) > 100:
        # Nume de fisier limitat: gasit de testul de proprietati cu o geometrie
        # de 300 de caractere, care arunca "File name too long" pe Linux (limita
        # tipica 255 pentru intreaga cale). SCAN_TIMEFRAME e citit din mediu -
        # o valoare custom neobisnuit de lunga nu trebuie sa poata crapa
        # arhivarea intregii rulari. Hash-ul pastreaza unicitatea.
        import hashlib
        h = hashlib.sha256(geometry.encode("utf-8")).hexdigest()[:16]
        safe = safe[:80] + "-" + h
    return os.path.join(ARCHIVE_DIR, f"{safe}.json")


def archive_stale_plans(store):
    """Muta DEFINITIV planurile din geometrii vechi in fisiere de arhiva
    separate, unul per geometrie. plans.json ramane cu DOAR geometria curenta.

    DE CE ASTA, SI NU DOAR O GARDA DE DIMENSIUNE
    ---------------------------------------------
    Verificat pe date reale: 73.020 de planuri acumulate din 9 geometrii
    anterioare (proiectul a schimbat geometria de 9 ori pana acum), plus
    13.077 noi intr-o singura rulare de backtest - 86.097 in total, 100.83 MB,
    respins de GitHub. O garda care doar TAIE cele mai vechi ID-uri cand se
    depaseste un prag are un defect serios: daca se ruleaza backtest de doua
    ori pe ACEEASI geometrie curenta, planurile din prima rulare au ID mai mic
    decat cele din a doua. Daca totalul depaseste pragul, taierea le-ar elimina
    pe cele din prima rulare - planuri din geometria ACTIVA, nu doar istoric
    mort - stricand direct calibrarea, nu doar arhiva.

    O geometrie veche e INCHISA definitiv: nu mai primeste NICIODATA planuri
    noi dupa ce geometria curenta se schimba. Deci fiecare fisier de arhiva
    are dimensiune FINITA garantat, iar plans.json ramane mereu mic - oricat
    de multe schimbari de geometrie mai vin.

    Fisierele de arhiva individuale raman disponibile pe disc pentru audit;
    doar nu mai sunt pe calea critica de citire/scriere la fiecare rulare.
    Un index mic (_index.json) tine count si R total per geometrie arhivata,
    ca summarize() sa poata raporta legacy_total_r fara sa recitesca totul.
    """
    # BUG FIX CRITIC: pastrez toata FAMILIA versiune+timeframe, nu semnatura exacta.
    #
    # Scanarea live sondeaza bursa, detecteaza capabilitati si fixeaza geometria
    # la v6-4h-obf prin set_capabilities(). Dar ai_agent.py ruleaza ca proces
    # SEPARAT, fara SCAN_CAPS in mediu, deci GEOMETRY_VERSION e v6-4h-o acolo.
    # Rezultatul masurat: scanerul crea 7 planuri, ai_agent le arhiva pe toate,
    # si plans.json ramanea gol dupa fiecare rulare. Pierdere totala de date
    # live, tacuta, la fiecare ora.
    #
    # Familia = acelasi numar de versiune si acelasi timeframe. Calibrarea si
    # agentul filtreaza in continuare pe semnatura EXACTA, deci separarea pe
    # capabilitati ramane intacta - doar ca datele nu mai sunt distruse de un
    # proces care nu stie ce capabilitati a detectat alt proces.
    parts = _core.GEOMETRY_VERSION.split("-")
    family = "-".join(parts[:2]) + "-" if len(parts) >= 2 else _core.GEOMETRY_VERSION

    plans = store.get("plans") or []
    keep, by_geo = [], {}
    for p in plans:
        geo = p.get("geometry", "v1")
        (keep if geo.startswith(family) else by_geo.setdefault(geo, [])).append(p)

    if not by_geo:
        return 0

    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    index = load_json(ARCHIVE_INDEX_FILE, {})
    moved = 0
    for geo, geo_plans in by_geo.items():
        path = _archive_filename(geo)
        existing = load_json(path, {"plans": []})
        existing_ids = {p.get("id") for p in existing["plans"]}
        fresh = [p for p in geo_plans if p.get("id") not in existing_ids]
        if fresh:
            existing["plans"].extend(fresh)
            save_json(path, existing)
            moved += len(fresh)
        closed = [p for p in existing["plans"] if p.get("realized_r") is not None
                 and p.get("state") != _core.STATE_NO_ENTRY]
        index[geo] = {
            "count": len(existing["plans"]), "closed": len(closed),
            "total_r": round(sum(p["realized_r"] for p in closed), 2),
        }

    store["plans"] = keep
    save_json(ARCHIVE_INDEX_FILE, index)
    return moved


def auto_mitigations():
    """Atenuarile decise de modulul selfrepair la rularea anterioara: modul de
    siguranta si starea filtrului Elliott (pornit/oprit dupa datele recente).
    Lipsa fisierului inseamna comportamentul implicit."""
    try:
        with open(os.path.join(os.path.dirname(PLANS_FILE), "self_check.json")) as f:
            return json.load(f).get("mitigations") or {}
    except Exception:
        return {}


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # SCRIERE ATOMICA: fisier temporar, golit pe disc, apoi inlocuire intr-un singur
    # pas. Un job oprit in timpul scrierii (timeout, anulare) lasa intact fisierul
    # vechi - pasul de commit din workflow ruleaza cu if: always() si ar fi urcat
    # un JSON trunchiat, oprind toate scanarile urmatoare.
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_plans():
    return load_json(PLANS_FILE, {"next_id": 1, "plans": []})


def save_plans(store):
    # ARHIVAREA vine INAINTEA gardei de dimensiune, nu dupa: o geometrie veche e
    # inchisa definitiv, deci arhivarea completa e mereu sigura si mereu de
    # dimensiune finita. Taierea oarba de mai jos ramane doar ca plasa de
    # siguranta pentru cazul (rar) in care GEOMETRIA CURENTA singura ar
    # depasi pragul - caz in care nu exista alta solutie decat sa astepti mai
    # putine planuri per rulare sau sa muti memoria pe un fisier separat.
    archived = archive_stale_plans(store)
    if archived:
        print(f"Arhivate {archived} planuri din geometrii vechi in {ARCHIVE_DIR}/ "
              f"(plans.json pastreaza doar geometria curenta: {_core.GEOMETRY_VERSION}).")
    _core._strip_display_fields(store)
    # separators compacte: `indent=2` aproape dubleaza dimensiunea pe fisiere
    # cu zeci de mii de inregistrari, fara niciun castig - nimeni nu citeste
    # plans.json cu ochiul.
    payload = json.dumps(store, separators=(",", ":"))
    mb = len(payload.encode("utf-8")) / 1024 / 1024

    # Daca depaseste pragul, TAI cele mai vechi planuri in loc sa esuez.
    # A arunca o exceptie ar insemna sa pierd toata munca rularii - inclusiv
    # 18 minute de backtest. Planurile vechi au fost deja invatate de agent
    # (marcate `agent_trained`); pierderea lor costa ceva istoric la vecini si
    # calibrare, dar infinit mai putin decat pierderea intregii rulari.
    if mb > _core.SIZE_FAIL_MB:
        plans = sorted(store.get("plans") or [], key=lambda p: p.get("id", 0))
        before = len(plans)
        while plans and mb > _core.SIZE_FAIL_MB * 0.8:
            drop = max(1, len(plans) // 20)          # taie 5% odata
            plans = plans[drop:]
            store["plans"] = plans
            payload = json.dumps(store, separators=(",", ":"))
            mb = len(payload.encode("utf-8")) / 1024 / 1024
        print(f"[!] plans.json depasea {_core.SIZE_FAIL_MB} MB. Am taiat cele mai vechi "
              f"{before - len(plans)} planuri; raman {len(plans)} ({mb:.1f} MB).")
        print("    Agentul invatase deja din ele. Pentru mai mult istoric, "
              "mareste SIZE_FAIL_MB sau muta memoria intr-un fisier separat.")
    elif mb > _core.SIZE_WARN_MB:
        print(f"[!] plans.json: {mb:.1f} MB - se apropie de limita GitHub de 100 MB.")
    os.makedirs(os.path.dirname(PLANS_FILE) or ".", exist_ok=True)
    # SCRIERE ATOMICA (fisier temporar + inlocuire): pasul de commit ruleaza cu
    # if: always(), deci un job anulat in timpul scrierii ar fi urcat un fisier trunchiat
    tmp = f"{PLANS_FILE}.tmp"
    with open(tmp, "w") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, PLANS_FILE)


# Porturile de citire ale nucleului, legate la fisierele de mai sus.
_core.MITIGATIONS_SOURCE = lambda: auto_mitigations()
_core.ARCHIVE_INDEX_SOURCE = lambda: load_json(ARCHIVE_INDEX_FILE, {})

compat.bind(__name__, _core)
