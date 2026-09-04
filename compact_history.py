#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compact_history.py
===================
Compacteaza scan_history.json ca sa nu umfle repo-ul la nesfarsit.

PROBLEMA
--------
La universe 200 si 6 scanari/zi, fisierul creste cu ~17 MB/luna, si fiecare
versiune e comisa in git. Dupa cateva luni, pasul de checkout din GitHub
Actions incepe sa manance din bugetul de minute la fiecare rulare.

CE PASTREZ SI CE ARUNC
----------------------
Arunc doar `results` - lista completa de semnale - din scanarile mai vechi de
KEEP_DETAIL_DAYS, si DOAR daca au fost deja evaluate si invatate.

Pastrez, pentru fiecare scanare compactata:
  - un `outcome_summary` cu numarul de hit/miss, ca sa nu se rupa curba de
    hit-rate din dashboard (calculata retroactiv din istoric)
  - top_long / top_short / best_candidate / deep_analysis, care sunt mici si
    alimenteaza afisarea

Nu ating niciodata plans.json: planurile sunt sursa de invatare a agentului si
sunt mici (cateva sute de octeti fiecare). Ar fi cel mai prost lucru de sters.
"""

import json
import os
import time

DATA_DIR = "data"
HISTORY_FILE = os.path.join(DATA_DIR, "scan_history.json")

KEEP_DETAIL_DAYS = 14   # sub atat, pastrez semnalele individuale intacte


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def summarize_results(results):
    """Rezumatul care inlocuieste lista de semnale, ca sa ramana calculabila
    curba de hit-rate."""
    hits = sum(1 for r in results if r.get("outcome") == "hit")
    misses = sum(1 for r in results if r.get("outcome") == "miss")
    return {"hits": hits, "misses": misses, "signals": len(results)}


def compact(history, keep_days=KEEP_DETAIL_DAYS, now=None):
    now = now or time.time()
    cutoff = now - keep_days * 86400
    compacted = 0

    for scan in history:
        if scan.get("compacted"):
            continue
        if scan.get("scan_id_ts", 0) >= cutoff:
            continue  # prea recenta, pastrez detaliile
        if not scan.get("evaluated"):
            continue  # inca neevaluata: as arunca date din care nu s-a invatat
        results = scan.get("results") or []
        if not results:
            continue
        scan["outcome_summary"] = summarize_results(results)
        scan["results"] = []
        scan["compacted"] = True
        compacted += 1

    return compacted


def main():
    history = load_json(HISTORY_FILE, [])
    if not history:
        print("Nu exista istoric de compactat.")
        return

    before = os.path.getsize(HISTORY_FILE)
    n = compact(history)
    if n == 0:
        print(f"Nimic de compactat (scanari mai vechi de {KEEP_DETAIL_DAYS} zile "
              f"si deja evaluate). Dimensiune: {before/1024:.0f} KB")
        return

    save_json(HISTORY_FILE, history)
    after = os.path.getsize(HISTORY_FILE)
    print(f"Compactate {n} scanari. {before/1024:.0f} KB -> {after/1024:.0f} KB "
          f"({100*(before-after)/before:.0f}% mai mic)")


if __name__ == "__main__":
    main()
