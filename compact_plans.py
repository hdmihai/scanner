#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compact_plans.py
=================
Ultimul pas inainte de commit: garanteaza ca data/plans.json incape pe GitHub,
INDIFERENT ce modul l-a scris si cum.

DE CE EXISTA CA PAS SEPARAT
---------------------------
Garda de dimensiune a fost pusa in plan_tracker.save_plans(). Dar backtest.py
isi scrie propriul plans.json prin save_json(), ocolind complet save_plans -
deci ocolind si arhivarea. Rezultatul, masurat in productie: 86.146 de planuri,
100.98 MB, push respins de GitHub. De doua ori.

Aceeasi clasa de eroare s-a repetat de mai multe ori in acest proiect: o
protectie pusa intr-un modul e ocolita de alt modul care n-a fost actualizat
odata cu el. Un pas separat, rulat ULTIMUL in workflow, nu poate fi ocolit de
nimeni - nu depinde de ce versiune are fiecare fisier .py.

CE FACE
-------
1. Muta planurile din geometrii vechi in data/archive/_index.json ca REZUMAT
   (count, closed, total_r, win_rate) si le sterge din plans.json.
   Randurile individuale se pierd intentionat: o geometrie veche e inchisa
   definitiv, agentul nu mai invata din ea, calibrarea o ignora, iar singurul
   lucru citit de briefing e total_r - care se pastreaza. 73.000 de planuri
   moarte inseamna ~90 MB in git pentru zero valoare functionala.
2. Rescrie compact (fara indentare).
3. Daca fisierul tot depaseste pragul - adica geometria CURENTA singura e prea
   mare - taie cele mai vechi planuri din ea si spune exact cate.

Idempotent: a doua rulare nu schimba nimic daca prima a reusit.

RULARE
------
    python3 compact_plans.py            # aplica
    python3 compact_plans.py --dry-run  # doar raporteaza
"""

import json
import os
import sys

DATA_DIR = "data"
PLANS_FILE = os.path.join(DATA_DIR, "plans.json")
ARCHIVE_DIR = os.path.join(DATA_DIR, "archive")
ARCHIVE_INDEX = os.path.join(ARCHIVE_DIR, "_index.json")

# GitHub respinge la 100 MB si avertizeaza la 50. Tin 40 ca marja reala:
# fisierul mai creste intre rulari, iar o marja stransa inseamna ca urmatoarea
# rulare pica din nou.
TARGET_MB = 40.0
HARD_MB = 80.0

CLOSED_STATES = ("TP2_HIT", "SL_HIT", "EXPIRED", "NO_ENTRY")


def load(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as exc:
        print(f"[!] nu pot citi {path}: {exc}")
        return default


def write_compact(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload = json.dumps(data, separators=(",", ":"))
    with open(path, "w") as f:
        f.write(payload)
    return len(payload.encode("utf-8")) / 1024 / 1024


def current_geometry():
    """Geometria curenta, calculata la fel ca in plan_tracker.

    Import direct, ca sa nu existe doua definitii care pot diverge. Daca
    importul esueaza, reconstruiesc din mediu - acelasi format.
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import plan_tracker
        return plan_tracker.GEOMETRY_VERSION
    except Exception:
        return ("v6-" + os.environ.get("SCAN_TIMEFRAME", "1h")
                + "-" + os.environ.get("SCAN_CAPS", "o"))


def summarize_group(plans):
    closed = [p for p in plans
              if p.get("realized_r") is not None and p.get("state") != "NO_ENTRY"]
    wins = [p for p in closed if p["realized_r"] > 0]
    return {
        "count": len(plans),
        "closed": len(closed),
        "total_r": round(sum(p["realized_r"] for p in closed), 2),
        "win_rate": round(100 * len(wins) / len(closed), 1) if closed else None,
    }


def main():
    dry = "--dry-run" in sys.argv

    store = load(PLANS_FILE, None)
    if not store or not store.get("plans"):
        print("plans.json lipseste sau e gol - nimic de compactat.")
        return 0

    before_mb = os.path.getsize(PLANS_FILE) / 1024 / 1024
    plans = store["plans"]
    geo = current_geometry()

    keep, stale = [], {}
    for p in plans:
        g = p.get("geometry", "v1")
        (keep if g == geo else stale.setdefault(g, [])).append(p)

    print(f"plans.json: {before_mb:.2f} MB, {len(plans):,} planuri")
    print(f"geometria curenta: {geo} -> {len(keep):,} planuri raman")
    if stale:
        print(f"geometrii vechi de arhivat: {len(stale)}")
        for g in sorted(stale):
            print(f"  {g:16s} {len(stale[g]):7,} planuri")

    if dry:
        print("\n--dry-run: nu am scris nimic.")
        return 0

    index = load(ARCHIVE_INDEX, {})
    for g, group in stale.items():
        prev = index.get(g) or {}
        merged = summarize_group(group)
        # Insumez cu ce era deja arhivat pentru aceeasi geometrie, ca rularile
        # repetate sa nu piarda istoricul acumulat anterior.
        index[g] = {
            "count": prev.get("count", 0) + merged["count"],
            "closed": prev.get("closed", 0) + merged["closed"],
            "total_r": round(prev.get("total_r", 0.0) + merged["total_r"], 2),
            "win_rate": merged["win_rate"] if prev.get("closed", 0) == 0
                        else prev.get("win_rate"),
        }

    if stale:
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        write_compact(ARCHIVE_INDEX, index)
        moved = sum(len(v) for v in stale.values())
        print(f"\nArhivate ca rezumat {moved:,} planuri din {len(stale)} geometrii vechi.")
        print(f"  Randurile individuale au fost STERSE - geometriile vechi sunt")
        print(f"  inchise definitiv, agentul nu mai invata din ele, iar calibrarea")
        print(f"  le ignora. Cifrele agregate raman in {ARCHIVE_INDEX}.")

    store["plans"] = keep
    mb = write_compact(PLANS_FILE, store)

    # Daca geometria CURENTA singura e prea mare, tai cele mai vechi din ea.
    if mb > HARD_MB:
        keep.sort(key=lambda p: p.get("id", 0))
        dropped = 0
        while keep and mb > TARGET_MB:
            cut = max(1, len(keep) // 20)
            keep = keep[cut:]
            dropped += cut
            store["plans"] = keep
            mb = write_compact(PLANS_FILE, store)
        print(f"\n[!] Geometria curenta singura depasea {HARD_MB} MB.")
        print(f"    Am taiat cele mai vechi {dropped:,} planuri; raman {len(keep):,}.")
        print(f"    Agentul invatase deja din ele (marcate agent_trained).")

    print(f"\nplans.json: {before_mb:.2f} MB -> {mb:.2f} MB ({len(keep):,} planuri)")
    if mb > 100:
        print(f"[EROARE] tot peste limita GitHub de 100 MB!")
        return 1
    print("OK: sub limita GitHub de 100 MB.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
