# -*- coding: utf-8 -*-
"""tests.stats_check - verifica cifrele de onestitate statistica afisate pe dashboard si in email.

Uz (din radacina repo-ului): python -m tests.stats_check

  1. PLANUL URMARIT: core.plans.open_plan_for spune exact ce spune has_open_plan (aceeasi
     regula pentru scanare, dashboard si email) si intoarce planul activ cel mai recent;
  2. CALIBRAREA PE 12 LUNI: fereastra recenta + perioada anterioara = calibrarea pe tot
     istoricul, interval cu interval (aceleasi filtre); verdictul "sub"/"peste" apare doar
     cand intervalele Wilson nu se suprapun; date lipsa -> "date_insuficiente", fara exceptii;
  3. VECINII COMPARABILI: pe planuri reale cu R-ul amestecat aleator (nicio legatura intre
     caracteristici si rezultat) verdictul iese din marja rar (~2%: test conservator) -
     inainte, orice diferenta nenula era "FAVORABIL"/"NEFAVORABIL"; cu un avantaj real
     injectat (+1R pe vecinii unui punct), verdictul il gaseste;
  4. POARTA DE ECHIVALENTA: excluderea pe cai JSON ascunde exact calea declarata.
Foloseste planurile reale din data/plans.json; nu scrie nimic in repo.
"""

import copy
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SCAN_TIMEFRAME", "4h")

from core import agent as A          # noqa: E402
from core import plans as P          # noqa: E402
from tests import equivalence as EQ  # noqa: E402


def _store():
    with open(os.path.join(ROOT, "data", "plans.json")) as f:
        return json.load(f)


def check_open_plan(store, fails):
    pairs = {(p.get("symbol"), p.get("direction")) for p in store["plans"]}
    for sym, d in pairs:
        op = P.open_plan_for(store, sym, d)
        if P.has_open_plan(store, sym, d) != (op is not None):
            fails.append(f"open_plan_for / has_open_plan nu concorda pe {sym} {d}")
        act = [p for p in store["plans"] if p.get("symbol") == sym and p.get("direction") == d
               and p.get("state") not in P.CLOSED_STATES]
        if act and op is not max(act, key=lambda p: p.get("id") or 0):
            fails.append(f"open_plan_for nu intoarce planul activ cel mai recent pe {sym} {d}")
    if P.open_plan_for({}, "X", "LONG") is not None or P.open_plan_for(None, "X", "LONG") is not None:
        fails.append("open_plan_for pe un store gol trebuie sa intoarca None")
    n_open = sum(1 for p in store["plans"] if p.get("state") not in P.CLOSED_STATES)
    print(f"  1. plan urmarit: {len(pairs)} perechi simbol+directie, {n_open} planuri active - aceeasi regula")


def check_calibration(store, fails):
    full = P.build_calibration(store)
    rc = P.calibration_by_period(store)
    for b, e in full.items():
        r = rc["buckets"].get(b) or {}
        n = (r.get("total") or 0) + ((r.get("prior") or {}).get("total") or 0)
        if not r:                                     # interval fara planuri in fereastra
            n = None
        if n is not None and n != e["total"]:
            fails.append(f"calibrare interval {b}: recent+anterior={n}, tot istoricul={e['total']}")
    for b, r in rc["buckets"].items():
        q = r.get("prior")
        if r["vs_prior"] in ("sub", "peste"):
            overlap = not (r["ci_high"] < q["ci_low"] or r["ci_low"] > q["ci_high"])
            if overlap:
                fails.append(f"interval {b}: verdict '{r['vs_prior']}' desi intervalele se suprapun")
        if r["vs_prior"] is not None and not (r["reliable"] and q and q["reliable"]):
            fails.append(f"interval {b}: verdict pe esantion nesigur")
    if P.calibration_by_period({"plans": []}).get("status") != "date_insuficiente":
        fails.append("calibration_by_period pe date lipsa trebuie sa dea date_insuficiente")
    junk = {"plans": [{"state": "SL_HIT", "realized_r": -1.0, "score_at_entry": None, "geometry": P.GEOMETRY_VERSION},
                      {"state": "NO_ENTRY", "realized_r": 0.0, "score_at_entry": 50, "geometry": P.GEOMETRY_VERSION}]}
    if P.calibration_by_period(junk).get("status") != "date_insuficiente":
        fails.append("calibration_by_period trebuie sa ignore NO_ENTRY si planurile fara scor")
    v = {b: (r["win_rate"], r["vs_prior"]) for b, r in rc["buckets"].items()}
    print(f"  2. calibrare pe {rc['days']} zile ({rc['since']} - {rc['until']}): {v}")


def _dist(fa, fb):
    return sum((fa.get(key, 0.0) - fb.get(key, 0.0)) ** 2 for key in A.FEATURES)


def check_neighbors(store, fails):
    rnd = random.Random(7)
    closed = [p for p in store["plans"] if p.get("realized_r") is not None
              and p.get("state") != P.STATE_NO_ENTRY and P.same_family(p.get("geometry"))]
    pool = rnd.sample(closed, min(3000, len(closed)))
    feats = [A.extract_features(p) for p in pool]
    rs = [p["realized_r"] for p in pool]

    def verdict(q, r_values):
        fake = [{"components": p.get("components"), "persistence_at_entry": p.get("persistence_at_entry"),
                 "evidence": p.get("evidence"), "direction": p.get("direction"), "realized_r": r}
                for p, r in zip(pool, r_values)]
        return A.comparable_entries(feats[q], fake)

    # CONTROL: R amestecat - nicio legatura cu caracteristicile
    trials, sig = 120, 0
    for _ in range(trials):
        shuffled = rs[:]
        rnd.shuffle(shuffled)
        res = verdict(rnd.randrange(len(pool)), shuffled)
        sig += res["verdict"] != "NEUTRU"
        if res["ci_low"] is None or not res["ci_low"] <= res["points"] <= res["ci_high"]:
            fails.append("vecini: intervalul de incredere lipseste sau nu contine estimarea")
            break
    rate = sig / trials
    if rate > 0.08:
        fails.append(f"vecini pe date amestecate: {rate:.0%} verdicte in afara marjei (asteptat ~2%)")
    # PUTERE: +1R pe cei 60 de vecini ai unui punct
    found, power_trials = 0, 30
    for _ in range(power_trials):
        q = rnd.randrange(len(pool))
        dist = sorted(range(len(pool)), key=lambda i: _dist(feats[i], feats[q]))
        boosted = rs[:]
        for i in dist[:60]:
            boosted[i] += 1.0
        found += verdict(q, boosted)["verdict"] == "FAVORABIL"
    if found / power_trials < 0.8:
        fails.append(f"vecini: avantajul injectat de +1R gasit doar in {found}/{power_trials} cazuri")
    print(f"  3. vecini: date amestecate {sig}/{trials} in afara marjei ({rate:.0%}); "
          f"avantaj real +1R gasit {found}/{power_trials}")


def check_gate(fails):
    bad = EQ._self_test()
    if bad:
        fails.append("poarta de echivalenta: " + "; ".join(bad))
    a = {"summary": {"calibration_recent": {"x": 1}, "edge": 1}, "plans": [{"id": 1, "neighbors": {"v": 1}}]}
    b = copy.deepcopy(a)
    b["summary"]["calibration_recent"]["x"] = 2
    b["plans"][0]["neighbors"]["v"] = 2
    for ptr in ("/summary/calibration_recent", "/plans/*/neighbors"):
        EQ._prune(a, EQ._segments(ptr))
        EQ._prune(b, EQ._segments(ptr))
    if a != b:
        fails.append("poarta: caile declarate in EXPECTED_CHANGES.txt nu sunt excluse")
    print("  4. poarta de echivalenta: excluderea pe cai JSON ascunde exact calea declarata")


def main():
    store = _store()
    fails = []
    check_open_plan(store, fails)
    check_calibration(store, fails)
    check_neighbors(store, fails)
    check_gate(fails)
    print("\nVERIFICARE STATISTICA: " + ("RESPINS - " + "; ".join(fails) if fails else "OK"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
