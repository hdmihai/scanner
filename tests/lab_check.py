# -*- coding: utf-8 -*-
"""tests.lab_check - verifica laboratorul agentului (core/lab.py, research_lab.py, lab_live.py).

Uz (din radacina repo-ului): python -m tests.lab_check

  1. caracteristicile: aceleasi valori pe ultimele 259 de bare (scanarea live) si pe tot istoricul;
     fara date din viitor (barele urmatoare nu schimba nimic din trecut);
  2. tranzactia invatata (bracket_outcome): fiecare regula, LONG si SHORT;
  3. eticheta vectorizata (numpy) = bracket_outcome, pe mii de cazuri aleatoare;
  4. modelul publicat: JSON -> core.lab.predict da aceleasi valori ca predict_np;
  5. alegerea pe bara (select) si promovarea (promotion);
  6. instantaneele scanarii (lab_live.snapshot_rows);
  7. CAP-COADA pe date construite: cu un efect plantat, laboratorul il gaseste (walk-forward peste
     referinta); fara efect, ramane in CERCETARE (nu inventeaza un avantaj).
Partile cu numpy/duckdb se sar, cu mesaj, daca bibliotecile lipsesc (workflow-ul de aplicare nu le are).
"""

import json
import math
import os
import random
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import lab as L    # noqa: E402

FAILS = []


def ok(cond, label):
    print(f"  {'OK ' if cond else 'ERR'} {label}")
    if not cond:
        FAILS.append(label)


def walk(n, seed, start=100.0, t0=1_600_000_000_000, drift=0.0, vol=0.02):
    rnd = random.Random(seed)
    out, p = [], start
    for i in range(n):
        o = p
        c = max(o * math.exp(drift + rnd.gauss(0, vol)), 1e-6)
        h = max(o, c) * (1 + abs(rnd.gauss(0, vol / 2)))
        lo = min(o, c) * (1 - abs(rnd.gauss(0, vol / 2)))
        out.append([t0 + i * 4 * 3600 * 1000, o, h, lo, c, 1000 * (1 + rnd.random())])
        p = c
    return out


def features_check():
    btc, alt = walk(1200, 1, 30000.0), walk(1200, 2, 5.0)
    rows = {r[0]: r[2] for r in L.aligned_features(alt, btc)}
    worst = 0.0
    for cut in (400, 700, 1000, 1200):
        full = rows.get(cut - 1)
        live = L.last_features(alt[cut - 259:cut], btc[cut - 259:cut])
        if full and live:
            worst = max(worst, max(abs(full[k] - live[k]) for k in L.FEATURES))
        else:
            worst = float("inf")
    ok(worst < 1e-3, f"live (259 de bare) = istoric complet, abatere maxima {worst:.2e}")
    # fara viitor: aceleasi bare, alt viitor
    alt2 = alt[:900] + walk(300, 99, alt[899][4], t0=alt[900][0])
    rows2 = {r[0]: r[2] for r in L.aligned_features(alt2, btc)}
    same = all(abs(rows[i][k] - rows2[i][k]) < 1e-12 for i in range(300, 900) if i in rows for k in L.FEATURES)
    ok(same, "fara date din viitor: barele urmatoare nu schimba caracteristicile trecute")
    # BTC lipsa la o bara: exemplul lipseste, nu se foloseste un BTC vechi
    btc_gap = [b for i, b in enumerate(btc) if i != 1000]
    r3 = {r[0] for r in L.aligned_features(alt, btc_gap)}
    ok(1000 not in r3 and 1001 in r3, "bara fara BTC aliniat nu produce exemplu")
    ok(len(L.FEATURES) == len(set(L.FEATURES)) and all(k in L.FEATURE_TEXT for k in L.FEATURES),
       f"{len(L.FEATURES)} caracteristici, fiecare cu descriere")
    ok(not L.tradable_base("USDC/USDT") and not L.tradable_base("BTCUP/USDT") and L.tradable_base("SOL/USDT"),
       "monedele stabile si cele cu efect de levier sunt excluse")


def bar(t, o, h, lo, c):
    return [t, o, h, lo, c, 1.0]


def bracket_check():
    atr = 1.0                                  # risc 1.5, TP la +3.0
    cost = L.COST_FRAC * 100 / 1.5
    base = [bar(0, 100, 100, 100, 100), bar(1, 100, 100.5, 99.5, 100)]
    cases = [
        ("LONG TP", base + [bar(2, 100, 103.2, 99.8, 103)], "LONG", L.TP_R - cost),
        ("LONG SL", base + [bar(2, 100, 100.2, 98.4, 99)], "LONG", -1 - cost),
        ("LONG SL si TP in aceeasi bara -> SL", base + [bar(2, 100, 103.5, 98.0, 101)], "LONG", -1 - cost),
        ("LONG deschidere sub SL -> la deschidere", base + [bar(2, 97.0, 97.5, 96.5, 97)], "LONG", (97 - 100) / 1.5 - cost),
        ("SHORT TP", base + [bar(2, 100, 100.2, 96.8, 97)], "SHORT", L.TP_R - cost),
        ("SHORT SL", base + [bar(2, 100, 101.6, 99.8, 101)], "SHORT", -1 - cost),
    ]
    for name, cs, d, want in cases:
        got = L.bracket_outcome(cs, 0, d, atr)
        ok(got is not None and abs(got - want) < 1e-9, f"{name}: {got if got is None else round(got, 4)} (asteptat {want:.4f})")
    # intrarea e la deschiderea barei 1; TP pe bara intrarii conteaza (maximul vine dupa deschidere)
    cs = [bar(0, 100, 100, 100, 100), bar(1, 100, 103.1, 99.9, 103)]
    ok(abs(L.bracket_outcome(cs, 0, "LONG", atr) - (L.TP_R - cost)) < 1e-9, "TP pe bara intrarii (dupa deschidere)")
    flat = [bar(i, 100, 100.4, 99.6, 100 + (0.3 if i == L.MAX_HOLD else 0)) for i in range(L.MAX_HOLD + 1)]
    want = 0.3 / 1.5 - cost
    got = L.bracket_outcome(flat, 0, "LONG", atr)
    ok(got is not None and abs(got - want) < 1e-9, f"iesire la timp dupa {L.MAX_HOLD} bare: {got}")
    ok(L.bracket_outcome(flat[:10], 0, "LONG", atr) is None, "tranzactie neincheiata -> fara eticheta")


def numpy_checks():
    try:
        import numpy as np
        import research_lab as R
    except ImportError as e:
        print(f"  --  sar verificarile cu numpy ({e})")
        return False
    cs = walk(3000, 5)
    arr = np.asarray(cs, dtype=float)
    rnd = random.Random(3)
    idx = np.asarray(sorted(rnd.sample(range(5, len(cs)), 1500)))
    atr = np.asarray([cs[i][4] * rnd.uniform(0.005, 0.04) for i in idx])
    Y = R.labels_np(arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4], idx, atr)
    bad = 0
    for j, i in enumerate(idx):
        for col, d in ((0, "LONG"), (1, "SHORT")):
            a, b = L.bracket_outcome(cs, int(i), d, float(atr[j])), Y[j, col]
            if (a is None) != bool(np.isnan(b)) or (a is not None and abs(a - b) > 1e-9):
                bad += 1
    ok(bad == 0, f"eticheta vectorizata = bracket_outcome pe {2 * len(idx)} cazuri ({bad} diferente)")
    # modelul publicat
    rng = np.random.default_rng(1)
    X = rng.normal(size=(20000, len(L.FEATURES))).astype(np.float32)
    y = 0.3 * X[:, 0] - 0.2 * (X[:, 3] > 0.5) + rng.normal(scale=1.0, size=len(X))
    for kind in ("gbm", "linear"):
        m = R.fit_gbm(X, y, params=dict(R.GBM, trees=30, min_leaf=300)) if kind == "gbm" else R.fit_linear(X, y)
        m = json.loads(json.dumps(m))
        a = R.predict_np(m, X[:500])
        b = [L.predict(m, dict(zip(L.FEATURES, map(float, row)))) for row in X[:500]]
        diff = max(abs(x - z) for x, z in zip(a, b))
        corr = float(np.corrcoef(R.predict_np(m, X), y)[0, 1])
        ok(diff < 1e-9 and corr > 0.2, f"modelul {kind}: JSON + predict = predict_np (abatere {diff:.1e}), "
                                       f"corelatie cu tinta {corr:.2f}")
    return True


def select_promotion_check():
    rows = [(1, "A", "LONG", 0.5), (1, "B", "SHORT", 0.4), (1, "C", "LONG", 0.3), (1, "D", "LONG", 0.2),
            (1, "A", "SHORT", 0.45), (2, "A", "LONG", -0.1), (2, "E", "LONG", 0.05)]
    got = [(r[0], r[1], r[2]) for r in L.select(rows, theta=0.1)]
    ok(got == [(1, "A", "LONG"), (1, "B", "SHORT"), (1, "C", "LONG")],
       f"alegerea: cel mult {L.TOP_K} pe bara, o directie pe moneda, peste prag ({got})")
    good = {"n": 900, "mean": 0.08, "lo": 0.02, "hi": 0.14, "years": {"2021": {"mean": 0.1}, "2022": {"mean": 0.05},
                                                                     "2023": {"mean": -0.01}}}
    hold = {"n": 200, "mean": 0.05}
    cases = [
        (None, {"n": 900, "mean": 0.01, "lo": -0.03, "hi": 0.05, "years": {}}, hold, None, "CERCETARE"),
        (None, good, {"n": 200, "mean": -0.02}, None, "CERCETARE"),
        (None, good, hold, {"n": 10, "mean": 0.3, "lo": -0.5, "hi": 1.0}, "SHADOW"),
        ("SHADOW", good, hold, {"n": 80, "mean": 0.2, "lo": 0.03, "hi": 0.4}, "ACTIV"),
        ("ACTIV", good, hold, {"n": 90, "mean": 0.05, "lo": -0.05, "hi": 0.2}, "ACTIV"),
        ("ACTIV", good, hold, {"n": 90, "mean": -0.3, "lo": -0.5, "hi": -0.1}, "RETRAS"),
        (None, dict(good, years={"2021": {"mean": 0.1}, "2022": {"mean": -0.1}, "2023": {"mean": -0.1}}), hold, None,
         "CERCETARE"),
    ]
    res = [L.promotion(p, w, h, lv)[0] for p, w, h, lv, _ in cases]
    ok(res == [c[-1] for c in cases], f"promovarea: {res}")


def live_snapshot_check():
    import lab_live
    btc = walk(259, 11, 30000.0)
    cache = {"BTC/USDT": btc, "SOL/USDT": walk(259, 12, 150.0), "ETH/USDT": walk(259, 13, 3000.0),
             "USDC/USDT": walk(259, 14, 1.0, vol=0.0005), "NEW/USDT": walk(120, 15, 1.0)}
    for k in cache:
        for b in cache[k]:
            b[5] = 10_000_000 / b[4]           # lichide
    lin = {"kind": "linear", "features": list(L.FEATURES), "mean": [0.0] * len(L.FEATURES),
           "scale": [1.0] * len(L.FEATURES), "lo": [-1e9] * len(L.FEATURES), "hi": [1e9] * len(L.FEATURES),
           "coef": [0.0] * len(L.FEATURES), "intercept": 0.1}
    short = dict(lin, intercept=-0.1)
    lab = {"version": "t", "state": "SHADOW", "model": {"LONG": lin, "SHORT": short, "theta": {"LONG": 0.05, "SHORT": 0.05}}}
    rows = lab_live.snapshot_rows(cache, btc, lab, "okx")
    syms = sorted(r["symbol"] for r in rows)
    ok(syms == ["BTC/USDT", "ETH/USDT", "SOL/USDT"], f"instantanee doar pentru monedele tranzactionabile cu istorie ({syms})")
    picks = [r for r in rows if r["pick"]]
    ok(len(picks) == min(L.TOP_K, 3) and all(r["pick_dir"] == "LONG" for r in picks)
       and all(r["model_version"] == "t" for r in rows), f"alegerile scanarii: {len(picks)} LONG, cu versiunea modelului")
    no_model = lab_live.snapshot_rows(cache, btc, {}, "okx")
    ok(no_model and not any(r["pick"] for r in no_model) and all(r["ev_long"] is None for r in no_model),
       "fara model publicat: instantaneele se salveaza (pentru invatare), fara alegeri")


def _write_parquet(path, rows):
    import duckdb
    fd, tmp = tempfile.mkstemp(suffix=".ndjson")
    with os.fdopen(fd, "w") as f:
        for r in rows:
            f.write(json.dumps(dict(zip(("symbol", "ts", "open", "high", "low", "close", "volume"), r))) + "\n")
    con = duckdb.connect()
    con.execute(f"COPY (SELECT * FROM read_json('{tmp}', format='newline_delimited', columns={{'symbol':'VARCHAR',"
                f"'ts':'BIGINT','open':'DOUBLE','high':'DOUBLE','low':'DOUBLE','close':'DOUBLE','volume':'DOUBLE'}})) "
                f"TO '{path}' (FORMAT PARQUET)")
    con.close()
    os.remove(tmp)


def planted_series(n, seed, effect, t0):
    """Bare de 4h; cu `effect`, dupa o scadere puternica pe o zi urmeaza o revenire (de invatat)."""
    rnd = random.Random(seed)
    out, p, push = [], 10.0, 0
    for i in range(n):
        o = p
        drift = 0.0
        if push > 0:
            drift, push = effect, push - 1
        c = max(o * math.exp(drift + rnd.gauss(0, 0.012)), 1e-6)
        h = max(o, c) * (1 + abs(rnd.gauss(0, 0.004)))
        lo = min(o, c) * (1 - abs(rnd.gauss(0, 0.004)))
        out.append([t0 + i * 4 * 3600 * 1000, o, h, lo, c, 1_000_000 / c])
        p = c
        if effect and len(out) > 7 and math.log(out[-1][4] / out[-7][4]) < -0.045:
            push = 6
    return out


def end_to_end():
    try:
        import numpy  # noqa: F401
        import duckdb  # noqa: F401
        import research_lab as R
    except ImportError as e:
        print(f"  --  sar testul cap-coada ({e})")
        return
    t0 = 1_514_764_800_000                     # 2018-01-01
    n = 6 * 365 * 6 + 200                      # ~6 ani de bare 4h
    saved = dict(R.GBM)
    R.GBM.update(trees=40, min_leaf=400, lam=200.0)
    R.MIN_TRAIN = 2000
    try:
        for name, effect, expect in (("efect plantat", 0.006, "gaseste"), ("fara efect", 0.0, "nu inventeaza")):
            with tempfile.TemporaryDirectory() as d:
                rows = []
                rows += [["BTC/USDT"] + b[:] for b in planted_series(n, 1, 0.0, t0)]
                for k in range(10):
                    rows += [[f"C{k}/USDT"] + b[:] for b in planted_series(n, 100 + k, effect, t0)]
                path = os.path.join(d, "binance__spot__4h__all.parquet")
                _write_parquet(path, rows)
                t = time.monotonic()
                rep = R.run_lab([("binance", [path], 0)], deadline=time.monotonic() + 900, log=lambda *_: None)
                ch = rep["walk_forward"][rep["chosen"]]
                base = rep["baseline_wf"]
                if effect:
                    ok(ch["mean"] is not None and ch["lo"] is not None and ch["lo"] > base["mean"] and ch["mean"] > 0.1,
                       f"{name}: laboratorul {expect} - walk-forward {ch['mean']:+.3f}R (IC95 {ch['lo']:+.3f}..) fata de "
                       f"referinta {base['mean']:+.3f}R, stare {rep['state']} ({time.monotonic() - t:.0f} s)")
                else:
                    ok(rep["state"] == "CERCETARE",
                       f"{name}: laboratorul {expect} - stare {rep['state']}, walk-forward "
                       f"{ch['mean'] if ch['mean'] is None else round(ch['mean'], 3)}R ({rep['reason']})")
                if effect:
                    pub = (t0 + int(n * 0.8) * 4 * 3600 * 1000) / 1000
                    fwd = R.forward_picks([{"version": "v1", "ts": pub, "model": rep["model"]}], [path],
                                          now_ms=t0 + n * 4 * 3600 * 1000, log=lambda *_: None)
                    early = [p for p in fwd if p["ts"] + 4 * 3600 * 1000 < pub * 1000]
                    done = [p["r"] for p in fwd if p["r"] is not None]
                    ok(fwd and not early and done and sum(done) / len(done) > 0.1,
                       f"{name}: testul inainte alege doar dupa publicarea modelului ({len(fwd)} alegeri, "
                       f"{len(done)} incheiate, R mediu {sum(done) / max(len(done), 1):+.3f})")
                ok(rep["model"]["LONG"]["kind"] == rep["chosen"] and json.loads(json.dumps(rep)) is not None,
                   f"{name}: raportul si modelul publicat se serializeaza ({len(json.dumps(rep)) / 1e3:.0f} KB)")
    finally:
        R.GBM.clear()
        R.GBM.update(saved)


def audit_lever_check():
    """Auto-auditul vede laboratorul: parghia lui cu dovada, si constatari cand lipseste, e vechi sau
    scanarea nu-i salveaza instantaneele."""
    from core import self_audit as SA
    now = 1_800_000_000
    rep = {"state": "SHADOW", "chosen": "gbm", "ts": now - 3600, "when": "t",
           "walk_forward": {"gbm": {"n": 900, "mean": 0.05, "lo": 0.01, "hi": 0.09}},
           "holdout": {"n": 150, "mean": 0.04}, "live": {"forward": {"n": 12}}}
    cases = [({}, {}, "laborator.neantrenat"), ({"error": "x"}, {}, "laborator.eroare"),
             (dict(rep, ts=now - 72 * 3600), {}, "laborator.vechi"), (rep, {"error": "y"}, "laborator.scanare")]
    for lab, live, want in cases:
        rows, found = SA.levers({"lab": lab, "lab_live": live, "lab_expected": True, "now": now})
        ok(any(f["id"] == want for f in found), f"auto-auditul semnaleaza {want}")
    rows, found = SA.levers({"lab": rep, "lab_live": {}, "lab_expected": True, "now": now})
    lab_rows = [r for r in rows if r["lever"] == "laborator"]
    ok(lab_rows and lab_rows[0]["verdict"] == "in proba live" and not [f for f in found if f["id"].startswith("laborator")],
       f"parghia laboratorului in tabel: {lab_rows and lab_rows[0]['evidence']}")


def main():
    print("LABORATORUL")
    features_check()
    bracket_check()
    numpy_checks()
    select_promotion_check()
    live_snapshot_check()
    audit_lever_check()
    end_to_end()
    print("\nLABORATOR: " + ("RESPINS - " + "; ".join(FAILS) if FAILS else "TRECUT"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
