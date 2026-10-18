#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""research_lab.py - LABORATORUL: agentul invata din depozitul de memorie (pasul `lab` din memory_sync.py,
o data pe zi). Logica de domeniu (caracteristicile, tranzactia invatata, alegerea, promovarea) e in
core/lab.py; aici sunt calculul vectorizat (numpy) si antrenarea.

CE FACE, la fiecare rulare:
  1. DATE: lumanarile de 4h din depozit - arhiva Binance (703 monede, din 2017) si, dupa sfarsitul ei,
     bursa principala (OKX) - pentru toate monedele tranzactionabile si lichide.
  2. EXEMPLE: la fiecare 12 ore (aceeasi grila pentru toate monedele), caracteristicile din
     core.lab.FeatureStream si R-ul tranzactiei LONG si SHORT (core.lab.bracket_outcome, vectorizat
     aici si verificat identic de tests/lab_check.py).
  3. WALK-FORWARD pe ani: pentru fiecare an, modelele se antreneaza doar pe ce s-a incheiat inainte
     de el (cu o pauza de MAX_HOLD bare, ca etichetele de antrenare sa nu vada anul testat) si se
     testeaza pe el. Doua modele: regresie liniara (ridge) si arbori gradient boosting. Alegerea
     agentului pe fiecare bara: cel mult TOP_K tranzactii cu EV peste cuantila 95% a predictiilor
     de antrenare (core.lab.select). Rezultat: R mediu pe alegere, IC95 pe blocuri de saptamani,
     fata de R-ul mediu al tuturor tranzactiilor posibile (ce ar face o alegere la intamplare).
  4. PERIOADA REZERVATA: ultimele HOLDOUT_DAYS zile, folosite o singura data, cu modelul ales dupa
     walk-forward si antrenat doar pe datele dinaintea ei.
  5. LIVE: alegerile facute de scanare (data/lab_live.json, salvate si in depozit), etichetate din
     lumanarile bursei de dupa ele - confirmarea pe date pe care modelul nu le-a vazut niciodata.
  6. MODELUL PUBLICAT: antrenat pe tot (inclusiv instantaneele live etichetate), ca JSON in
     data/memory_status.json -> "lab"; scanarea il aplica orar. Starea (CERCETARE / SHADOW / ACTIV /
     RETRAS) vine din core.lab.promotion.
"""

import calendar
import os
import time

from core import lab as L
from core import plans as P

BAR_MS = 4 * 3600 * 1000
SAMPLE_EVERY = 3               # o bara din 3 (la 12 ore), pe grila globala de timp
HOLDOUT_DAYS = 180
PURGE_MS = (L.MAX_HOLD + 1) * BAR_MS
BINS = 32
GBM = {"trees": 160, "depth": 3, "lr": 0.05, "min_leaf": 1500, "lam": 2000.0, "subsample": 0.5}
MIN_TRAIN = 50_000
SEED = 7


def _np():
    import numpy as np
    return np


# ====================================================================== 1. date

def load_series(files, symbols=None):
    """{simbol: [[ts, o, h, l, c, v], ...]} din fisierele Parquet de 4h, cu DuckDB."""
    import duckdb
    if not files:
        return {}
    src = "read_parquet([" + ", ".join("'" + f.replace("'", "''") + "'" for f in files) + "])"
    con = duckdb.connect()
    where = ""
    if symbols is not None:
        where = " WHERE symbol IN (" + ", ".join("'" + s.replace("'", "''") + "'" for s in symbols) + ")"
    out = {}
    for sym, ts, o, h, lo, c, v in con.execute(
            f"SELECT symbol, ts, open, high, low, close, volume FROM {src}{where} ORDER BY symbol, ts").fetchall():
        if None in (ts, o, h, lo, c):
            continue
        out.setdefault(sym, []).append([ts, o, h, lo, c, v or 0.0])
    con.close()
    # bare duplicate (aceeasi ora din doua partitii): pastrez ultima
    for sym, rows in out.items():
        ded = {}
        for r in rows:
            ded[r[0]] = r
        out[sym] = [ded[t] for t in sorted(ded)]
    return out


# ====================================================================== 2. exemple

def labels_np(o, h, lo, c, idx, atr):
    """core.lab.bracket_outcome vectorizat, pentru LONG si SHORT, la barele `idx` (semnal la inchidere).
    NaN unde tranzactia nu s-a incheiat inca."""
    np = _np()
    n = len(c)
    out = np.full((len(idx), 2), np.nan)
    H = L.MAX_HOLD
    ok = (idx + 1 < n) & (atr > 0)
    for side, col in (("LONG", 0), ("SHORT", 1)):
        long_ = side == "LONG"
        for j in np.nonzero(ok)[0]:
            i = int(idx[j])
            entry = o[i + 1]
            risk = L.SL_ATR * atr[j]
            if entry <= 0 or risk <= 0:
                continue
            last = i + H
            end = min(last, n - 1)
            ks = np.arange(i + 1, end + 1)
            oo, hh, ll = o[ks], h[ks], lo[ks]
            if long_:
                sl, tp = entry - risk, entry + L.TP_R * risk
                gap = (oo <= sl) & (ks > i + 1)
                hit_sl = ll <= sl
                hit_tp = hh >= tp
            else:
                sl, tp = entry + risk, entry - L.TP_R * risk
                gap = (oo >= sl) & (ks > i + 1)
                hit_sl = hh >= sl
                hit_tp = ll <= tp
            ev = gap | hit_sl | hit_tp
            cost = L.COST_FRAC * entry / risk
            if ev.any():
                k = int(np.argmax(ev))
                if gap[k]:
                    r = ((oo[k] - entry) if long_ else (entry - oo[k])) / risk
                elif hit_sl[k]:
                    r = -1.0
                else:
                    r = L.TP_R
                out[j, col] = r - cost
            elif end == last:
                cl = c[last]
                out[j, col] = ((cl - entry) if long_ else (entry - cl)) / risk - cost
    return out


_SERIES_CACHE = {}


def _series(files, symbol):
    """Lumanarile unei monede din fisierele Parquet (in procesul care le foloseste)."""
    key = (tuple(files), symbol)
    if key not in _SERIES_CACHE:
        if len(_SERIES_CACHE) > 4:
            _SERIES_CACHE.clear()
        _SERIES_CACHE[key] = load_series(list(files), [symbol]).get(symbol) or []
    return _SERIES_CACHE[key]


def symbol_examples(args):
    """Proces separat: exemplele unei monede, citita direct din Parquet (memoria ramane mica - nu se
    copiaza tot istoricul in fiecare proces). Intoarce (simbol, (ts[], X[n,F], Y[n,2])) sau (simbol, None)."""
    sym, files, start_ts = args
    try:
        return _symbol_examples(sym, files, start_ts)
    except Exception as e:                     # o serie cu date stricate nu opreste laboratorul
        print(f"  [!] {sym}: {type(e).__name__}: {str(e)[:120]}")
        return sym, None


def _symbol_examples(sym, files, start_ts):
    np = _np()
    candles = _series(files, sym)
    btc = candles if sym.startswith("BTC/") else _series(files, "BTC/USDT")
    if len(candles) < L.MIN_BARS + 2 or not btc:
        return sym, None
    keep = (lambda ts: (ts // BAR_MS) % SAMPLE_EVERY == 0 and ts >= start_ts)
    rows = L.aligned_features(candles, btc, keep=keep, is_btc=sym.startswith("BTC/"))
    rows = [r for r in rows if r[2]["_dollar"] >= L.MIN_DOLLAR_BAR]
    if not rows:
        return sym, None
    arr = np.asarray(candles, dtype=float)
    idx = np.asarray([r[0] for r in rows])
    atr = np.asarray([r[2]["_atr"] for r in rows])
    Y = labels_np(arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4], idx, atr)
    X = np.asarray([[r[2][k] for k in L.FEATURES] for r in rows], dtype=np.float32)
    ts = np.asarray([r[1] for r in rows], dtype=np.int64)
    return sym, (ts, X, Y)


def list_symbols(files):
    """(simbol, numar de bare, ultima bara) pentru fiecare moneda din fisiere."""
    import duckdb
    if not files:
        return []
    src = "read_parquet([" + ", ".join("'" + f.replace("'", "''") + "'" for f in files) + "])"
    con = duckdb.connect()
    out = con.execute(f"SELECT symbol, count(*), max(ts) FROM {src} GROUP BY symbol ORDER BY symbol").fetchall()
    con.close()
    return out


def build_dataset(sources, deadline, procs=None, log=print):
    """`sources` = [(nume, fisiere Parquet 4h, prag_ts)] - dupa sursa principala (Binance), bursa continua
    seria doar dupa sfarsitul ei (prag_ts). Intoarce dict cu ts, sym, X, Y si lista de simboluri."""
    np = _np()
    import multiprocessing as mp
    jobs = []
    for _name, files, start in sources:
        for sym, n, last in list_symbols(files):
            if L.tradable_base(sym) and n >= L.MIN_BARS + L.MAX_HOLD and last >= start:
                jobs.append((sym, tuple(files), start))
    parts, syms = [], []
    procs = procs or max(1, os.cpu_count() or 1)
    # BTC intai in fiecare lot: fiecare proces il citeste o data (cache)
    with mp.Pool(procs, maxtasksperchild=200) as pool:
        for sym, res in pool.imap_unordered(symbol_examples, jobs, chunksize=8):
            if res is not None:
                parts.append(res)
                syms.append(sym)
            if time.monotonic() > deadline:
                pool.terminate()
                raise TimeoutError(f"buget depasit la construirea exemplelor ({len(parts)} din {len(jobs)} serii)")
    if not parts:
        return None
    sym_ix = np.concatenate([np.full(len(p[0]), k, dtype=np.int32) for k, p in enumerate(parts)])
    ts = np.concatenate([p[0] for p in parts])
    X = np.concatenate([p[1] for p in parts])
    Y = np.concatenate([p[2] for p in parts])
    order = np.argsort(ts, kind="stable")
    log(f"  exemple: {len(ts)} din {len(parts)} serii ({len(jobs)} cu istorie suficienta)")
    return {"ts": ts[order], "sym": sym_ix[order], "X": X[order], "Y": Y[order], "symbols": syms}


# ====================================================================== 3. modele

def _edges(X, bins=BINS):
    np = _np()
    qs = np.linspace(0, 1, bins + 1)[1:-1]
    out = []
    for f in range(X.shape[1]):
        e = np.unique(np.quantile(X[:, f], qs))
        out.append(e.astype(np.float64))
    return out


def _binize(X, edges):
    np = _np()
    B = np.empty(X.shape, dtype=np.uint8)
    for f, e in enumerate(edges):
        B[:, f] = np.searchsorted(e, X[:, f], side="left")
    return B


def fit_gbm(X, y, params=GBM, seed=SEED):
    """Arbori gradient boosting pentru regresie (pierdere patratica), pe histograme de BINS intervale.
    Intoarce modelul in formatul JSON al core.lab.predict (praguri reale: bin <= b <=> x <= edges[b])."""
    np = _np()
    rng = np.random.default_rng(seed)
    edges = _edges(X)
    Bx = _binize(X, edges)
    n, F = Bx.shape
    base = float(y.mean())
    pred = np.full(n, base)
    lam, ml, lr = params["lam"], params["min_leaf"], params["lr"]
    trees, gains = [], np.zeros(F)
    for _t in range(params["trees"]):
        g = y - pred
        rows = rng.random(n) < params["subsample"] if params["subsample"] < 1 else np.ones(n, bool)
        idx_all = np.nonzero(rows)[0]
        nodes = [None]                          # nodul 0 = radacina
        frontier = [(0, idx_all, 0)]
        tree = [None]
        while frontier:
            nid, idx, depth = frontier.pop()
            G, N = g[idx].sum(), len(idx)
            best = None
            if depth < params["depth"] and N >= 2 * ml:
                gi = g[idx]
                for f in range(F):
                    nb = len(edges[f]) + 1
                    b = Bx[idx, f]
                    sg = np.bincount(b, weights=gi, minlength=nb)
                    cn = np.bincount(b, minlength=nb).astype(float)
                    GL, NL = np.cumsum(sg)[:-1], np.cumsum(cn)[:-1]
                    GR, NR = G - GL, N - NL
                    valid = (NL >= ml) & (NR >= ml)
                    if not valid.any():
                        continue
                    gain = GL ** 2 / (NL + lam) + GR ** 2 / (NR + lam) - G ** 2 / (N + lam)
                    gain = np.where(valid, gain, -np.inf)
                    k = int(np.argmax(gain))
                    if gain[k] > 0 and (best is None or gain[k] > best[0]):
                        best = (float(gain[k]), f, k)
            if best is None:
                tree[nid] = [float(G / (N + lam))]
                continue
            gain, f, k = best
            gains[f] += gain
            go_left = Bx[idx, f] <= k
            li, ri = len(tree), len(tree) + 1
            tree += [None, None]
            tree[nid] = [int(f), float(edges[f][k]), li, ri]
            frontier.append((li, idx[go_left], depth + 1))
            frontier.append((ri, idx[~go_left], depth + 1))
        trees.append(tree)
        pred += lr * _tree_predict_bins(tree, Bx, edges)
    return {"kind": "gbm", "features": list(L.FEATURES), "base": base, "lr": lr, "trees": trees,
            "importance": {L.FEATURES[f]: float(gains[f]) for f in range(F)}}


def _tree_predict_bins(tree, Bx, edges):
    np = _np()
    n = Bx.shape[0]
    out = np.zeros(n)
    stack = [(0, np.arange(n))]
    while stack:
        nid, idx = stack.pop()
        node = tree[nid]
        if len(node) == 1:
            out[idx] = node[0]
            continue
        f, thr, li, ri = node
        b = int(np.searchsorted(edges[f], thr, side="left"))
        m = Bx[idx, f] <= b
        stack.append((li, idx[m]))
        stack.append((ri, idx[~m]))
    return out


def predict_np(model, X):
    """core.lab.predict pe o matrice (aceleasi rezultate, vectorizat)."""
    np = _np()
    if model["kind"] == "linear":
        Z = (np.clip(X, model["lo"], model["hi"]) - np.asarray(model["mean"])) / np.asarray(model["scale"])
        return Z @ np.asarray(model["coef"]) + model["intercept"]
    out = np.full(X.shape[0], model["base"])
    for tree in model["trees"]:
        stack = [(0, np.arange(X.shape[0]))]
        vals = np.zeros(X.shape[0])
        while stack:
            nid, idx = stack.pop()
            node = tree[nid]
            if len(node) == 1:
                vals[idx] = node[0]
                continue
            f, thr, li, ri = node
            m = X[idx, f] <= thr
            stack.append((li, idx[m]))
            stack.append((ri, idx[~m]))
        out += model["lr"] * vals
    return out


def fit_linear(X, y, alpha_frac=1e-3):
    """Regresie ridge pe caracteristici taiate la cuantilele 1%/99% si standardizate."""
    np = _np()
    lo, hi = np.quantile(X, 0.01, axis=0), np.quantile(X, 0.99, axis=0)
    Xc = np.clip(X, lo, hi)
    mu, sd = Xc.mean(axis=0), Xc.std(axis=0)
    sd = np.where(sd > 0, sd, 1.0)
    Z = (Xc - mu) / sd
    ym = float(y.mean())
    A = Z.T @ Z + alpha_frac * len(y) * np.eye(Z.shape[1])
    w = np.linalg.solve(A, Z.T @ (y - ym))
    return {"kind": "linear", "features": list(L.FEATURES), "mean": mu.tolist(), "scale": sd.tolist(),
            "lo": lo.tolist(), "hi": hi.tolist(), "coef": w.tolist(), "intercept": ym}


FITTERS = {"gbm": fit_gbm, "linear": fit_linear}


# ====================================================================== 4. evaluare

def _stats(rows):
    """[(ts_secunde, R)] -> {"n", "mean", "lo", "hi"} cu IC95 pe blocuri de timp (core.plans)."""
    if not rows:
        return {"n": 0, "mean": None, "lo": None, "hi": None}
    lo, hi = P.block_mean_ci(rows, n_boot=1000)
    return {"n": len(rows), "mean": round(sum(v for _, v in rows) / len(rows), 4),
            "lo": None if lo is None else round(lo, 4), "hi": None if hi is None else round(hi, 4)}


def fit_pair(kind, X, Y, seed=SEED):
    """Cate un model pe directie, plus pragul de alegere (cuantila SELECT_Q a predictiilor de antrenare)."""
    np = _np()
    models, theta = {}, {}
    for col, d in ((0, "LONG"), (1, "SHORT")):
        ok = ~np.isnan(Y[:, col])
        m = FITTERS[kind](X[ok], Y[ok, col].astype(float)) if kind == "linear" else \
            fit_gbm(X[ok], Y[ok, col].astype(float), seed=seed + col)
        models[d] = m
        p = predict_np(m, X[ok])
        theta[d] = float(np.quantile(p, L.SELECT_Q))
    return models, theta


def evaluate(models, theta, data, mask):
    """Alegerea agentului (core.lab.select) pe randurile din `mask`: lista (ts_secunde, R) a alegerilor si
    a tuturor tranzactiilor posibile (referinta)."""
    np = _np()
    X, Y, ts, sym = data["X"][mask], data["Y"][mask], data["ts"][mask], data["sym"][mask]
    rows, base = [], []
    for col, d in ((0, "LONG"), (1, "SHORT")):
        ok = ~np.isnan(Y[:, col])
        p = predict_np(models[d], X[ok])
        th = theta[d]
        for t, s, ev, r in zip(ts[ok], sym[ok], p, Y[ok, col]):
            base.append((int(t) / 1000, float(r)))
            if ev > max(th, 0.0):
                rows.append((int(t), int(s), d, float(ev), float(r)))
    picked = L.select(rows, theta=-1e9)
    return [(r[0] / 1000, r[4]) for r in picked], base


def year_of(ms):
    return time.gmtime(ms / 1000).tm_year


def run_lab(sources, prev=None, live_rows=None, deadline=None, log=print, forward=None):
    """Laboratorul complet. `sources` ca la build_dataset; `live_rows` = instantaneele live etichetate
    [{"ts", "symbol", "f": {...}, "r_long", "r_short", "pick", "pick_dir", "pick_r"}]; `prev` = raportul
    anterior. Intoarce raportul (cu modelul publicat)."""
    np = _np()
    t0 = time.monotonic()
    deadline = deadline or (t0 + 3600)
    data = build_dataset(sources, deadline, log=log)
    if data is None:
        return {"error": "fara exemple (lipsesc lumanarile din depozit)"}
    ts = data["ts"]
    labeled = ~np.isnan(data["Y"]).all(axis=1)
    t_end = int(ts[labeled].max())
    hold_start = t_end - HOLDOUT_DAYS * 86400 * 1000
    y0, y_hold = year_of(int(ts.min())), year_of(hold_start)
    folds = []
    for y in range(max(y0 + 2, 2020), y_hold + 1):
        a = calendar.timegm((y, 1, 1, 0, 0, 0)) * 1000
        b = calendar.timegm((y + 1, 1, 1, 0, 0, 0)) * 1000
        b = min(b, hold_start - PURGE_MS)
        tr = labeled & (ts < a - PURGE_MS)
        te = labeled & (ts >= a) & (ts < b)
        if tr.sum() >= MIN_TRAIN and te.sum() > 0:
            folds.append((str(y), tr, te))
    log(f"  walk-forward: {len(folds)} ani; perioada rezervata de la "
        f"{time.strftime('%Y-%m-%d', time.gmtime(hold_start / 1000))}")
    # WALK-FORWARD: fiecare (model, an) e independent -> in paralel, cate unul pe procesor
    tasks = [(kind, name) for kind in ("linear", "gbm") for name, _tr, _te in folds]
    _G.clear()
    _G.update(data=data, folds={name: (tr, te) for name, tr, te in folds})
    results = _parallel(_fold_task, tasks, deadline)
    wf, base_rows = {}, []
    for kind in ("linear", "gbm"):
        picks, years = [], {}
        for name, _tr, _te in folds:
            pk, base = results[(kind, name)]
            picks += pk
            if kind == "linear":
                base_rows += base
            years[name] = _stats(pk)
            log(f"  {kind} {name}: {years[name]}")
        st = _stats(picks)
        st["years"] = years
        wf[kind] = st
    chosen = max(wf, key=lambda k: (wf[k]["mean"] if wf[k]["mean"] is not None else -9))
    # perioada rezervata: o singura evaluare, cu modelul ales dupa walk-forward
    tr = labeled & (ts < hold_start - PURGE_MS)
    te = labeled & (ts >= hold_start)
    _G["folds"]["_hold"] = (tr, te)
    # modelul publicat: tot istoricul etichetat + instantaneele live etichetate (invatarea din scanari)
    X_all, Y_all = data["X"][labeled], data["Y"][labeled]
    n_live_train = 0
    if live_rows:
        lx = [[r["f"].get(k) for k in L.FEATURES] for r in live_rows
              if r.get("r_long") is not None and r.get("r_short") is not None]
        ly = [[r["r_long"], r["r_short"]] for r in live_rows
              if r.get("r_long") is not None and r.get("r_short") is not None]
        lx_ok = [(x, y) for x, y in zip(lx, ly) if all(v is not None for v in x)]
        if lx_ok:
            X_all = np.concatenate([X_all, np.asarray([x for x, _ in lx_ok], dtype=np.float32)])
            Y_all = np.concatenate([Y_all, np.asarray([y for _, y in lx_ok])])
            n_live_train = len(lx_ok)
    _G.update(X_all=X_all, Y_all=Y_all)
    res2 = _parallel(_final_task, [("hold", chosen), ("final", chosen)], deadline)
    pk_h, base_h = res2[("hold", chosen)]
    holdout = _stats(pk_h)
    holdout["baseline"] = _stats(base_h)
    holdout["from"] = time.strftime("%Y-%m-%d", time.gmtime(hold_start / 1000))
    holdout["to"] = time.strftime("%Y-%m-%d", time.gmtime(t_end / 1000))
    models_f, theta_f = res2[("final", chosen)]
    _G.clear()
    live = live_stats(live_rows or [], (prev or {}).get("version"))
    # confirmarea live = testul inainte pe tot universul (modelele publicate, pe barele de dupa publicare)
    live["forward"] = forward_stats(forward or [])
    wf_c = wf[chosen]
    state, reason = L.promotion((prev or {}).get("state"), wf_c, holdout, live["forward"])
    version = time.strftime("%Y%m%d-%H%M", time.gmtime())
    learned = what_it_learned(models_f, chosen, data["X"][labeled][-200_000:])
    rep = {
        "lab_version": L.LAB_VERSION, "version": version, "ts": time.time(),
        "when": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "state": state, "reason": reason, "chosen": chosen,
        "trade": {"sl_atr": L.SL_ATR, "tp_r": L.TP_R, "max_hold": L.MAX_HOLD, "cost_frac": L.COST_FRAC,
                  "top_k": L.TOP_K, "select_q": L.SELECT_Q, "bar": "4h", "sample_every": SAMPLE_EVERY},
        "data": {"examples": int(labeled.sum()), "symbols": len(data["symbols"]),
                 "from": time.strftime("%Y-%m-%d", time.gmtime(int(ts.min()) / 1000)),
                 "to": time.strftime("%Y-%m-%d", time.gmtime(t_end / 1000)),
                 "live_train": n_live_train},
        "walk_forward": wf, "baseline_wf": _stats(base_rows),
        "holdout": holdout, "live": live, "learned": learned,
        "model": {"LONG": models_f["LONG"], "SHORT": models_f["SHORT"], "theta": theta_f, "kind": chosen},
        "seconds": round(time.monotonic() - t0, 1),
    }
    hist = list((prev or {}).get("history") or [])
    hist.append({"version": version, "state": state, "chosen": chosen, "wf": wf_c.get("mean"),
                 "wf_lo": wf_c.get("lo"), "holdout": holdout.get("mean"), "live_n": live["forward"].get("n", 0),
                 "live_mean": live["forward"].get("mean")})
    rep["history"] = hist[-30:]
    return rep


_G = {}          # datele laboratorului pentru procesele de antrenare (mostenite la fork, fara copiere)


def _fold_task(task):
    kind, name = task
    data = _G["data"]
    tr, te = _G["folds"][name]
    models, theta = fit_pair(kind, data["X"][tr], data["Y"][tr])
    return task, evaluate(models, theta, data, te)


def _final_task(task):
    what, kind = task
    data = _G["data"]
    if what == "hold":
        tr, te = _G["folds"]["_hold"]
        models, theta = fit_pair(kind, data["X"][tr], data["Y"][tr])
        return task, evaluate(models, theta, data, te)
    return task, fit_pair(kind, _G["X_all"], _G["Y_all"])


def _parallel(fn, tasks, deadline):
    """Sarcinile de antrenare in procese separate (fork: datele din _G se mostenesc). Fara fork
    (Windows/macOS spawn) sau cu un singur procesor: secvential."""
    import multiprocessing as mp
    procs = min(len(tasks), max(1, os.cpu_count() or 1))
    if procs <= 1 or "fork" not in mp.get_all_start_methods():
        out = {}
        for t in tasks:
            if time.monotonic() > deadline:
                raise TimeoutError("buget depasit la antrenare")
            k, v = fn(t)
            out[k] = v
        return out
    out = {}
    ctx = mp.get_context("fork")
    with ctx.Pool(procs) as pool:
        for k, v in pool.imap_unordered(fn, tasks):
            out[k] = v
            if time.monotonic() > deadline:
                pool.terminate()
                raise TimeoutError("buget depasit la antrenare")
    return out


def what_it_learned(models, kind, X, k=8):
    """Ce a invatat modelul, in cuvinte: pentru caracteristicile cele mai importante, cum se schimba EV-ul
    estimat cand caracteristica trece de la cuantila 10% la 90% (restul raman ca in date)."""
    np = _np()
    rng = np.random.default_rng(SEED)
    S = X[rng.choice(len(X), size=min(3000, len(X)), replace=False)]
    out = {}
    for d, m in models.items():
        if kind == "gbm":
            imp = m.get("importance") or {}
            tot = sum(imp.values()) or 1.0
            top = sorted(imp, key=lambda f: -imp[f])[:k]
        else:
            top = [L.FEATURES[i] for i in np.argsort(-np.abs(np.asarray(m["coef"])))[:k]]
            tot = None
        rows = []
        for f in top:
            j = L.FEATURES.index(f)
            q10, q90 = np.quantile(X[:, j], [0.1, 0.9])
            A, B = S.copy(), S.copy()
            A[:, j], B[:, j] = q10, q90
            eff = float(predict_np(m, B).mean() - predict_np(m, A).mean())
            rows.append({"feature": f, "text": L.FEATURE_TEXT.get(f, f), "q10": round(float(q10), 4),
                         "q90": round(float(q90), 4), "effect": round(eff, 4),
                         "share": round((m.get("importance") or {}).get(f, 0) / tot, 3) if tot else None})
        out[d] = rows
    return out


def live_stats(live_rows, version=None):
    """Alegerile live (instantaneele marcate pick la scanare, cu modelul de atunci), etichetate: R mediu cu
    IC95 pe blocuri de zile, fata de toate tranzactiile posibile din aceleasi scanari."""
    picks = [(r["ts"] / 1000, r["pick_r"]) for r in live_rows if r.get("pick") and r.get("pick_r") is not None]
    base = [(r["ts"] / 1000, v) for r in live_rows for v in (r.get("r_long"), r.get("r_short")) if v is not None]
    pending = sum(1 for r in live_rows if r.get("pick") and r.get("pick_r") is None)
    out = {"picks": _stats_daily(picks), "baseline": _stats_daily(base), "pending": pending,
           "snapshots": len(live_rows)}
    return out


def _stats_daily(rows):
    if not rows:
        return {"n": 0, "mean": None, "lo": None, "hi": None}
    lo, hi = P.block_mean_ci(rows, block_s=86400, n_boot=1000)
    return {"n": len(rows), "mean": round(sum(v for _, v in rows) / len(rows), 4),
            "lo": None if lo is None else round(lo, 4), "hi": None if hi is None else round(hi, 4)}


FORWARD_DAYS = 180           # cat de departe in urma se pastreaza testul inainte (modelele arhivate)


def forward_picks(versions, files, now_ms=None, log=print):
    """TESTUL INAINTE pe tot universul bursei: fiecare model publicat alege pe barele care s-au inchis DUPA
    publicarea lui si inainte de urmatorul (deci pe date pe care nu le-a vazut la antrenare), cu aceleasi
    reguli ca in walk-forward (prag pe directie, cel mult TOP_K pe bara, doar monede lichide); fiecare
    alegere e etichetata cu bracket_outcome cand tranzactia s-a incheiat. E confirmarea live a
    laboratorului: ~4 alegeri pe zi pe ~400 de monede, fata de cateva pe saptamana pe cele 30 scanate.
    `versions` = [{"version", "ts" (secunde, publicarea), "model": {...}}]. Intoarce lista alegerilor."""
    vs = sorted((v for v in versions or [] if v.get("model") and v.get("ts")), key=lambda v: v["ts"])
    if not vs or not files:
        return []
    now_ms = now_ms or int(time.time() * 1000)
    first_ms = int(vs[0]["ts"] * 1000)
    warm_ms = first_ms - (L.MIN_BARS + 20) * BAR_MS
    import duckdb
    src = "read_parquet([" + ", ".join("'" + f.replace("'", "''") + "'" for f in files) + "])"
    con = duckdb.connect()
    series = {}
    for sym, ts, o, h, lo, c, v in con.execute(
            f"SELECT symbol, ts, open, high, low, close, volume FROM {src} WHERE ts >= {warm_ms} "
            "ORDER BY symbol, ts").fetchall():
        if None not in (ts, o, h, lo, c):
            series.setdefault(sym, {})[ts] = [ts, o, h, lo, c, v or 0.0]
    con.close()
    series = {k: [d[t] for t in sorted(d)] for k, d in series.items()}
    btc = series.get("BTC/USDT")
    if not btc:
        return []
    pub = [int(v["ts"] * 1000) for v in vs]

    def active(close_ms):
        k = None
        for i, p_ in enumerate(pub):
            if p_ <= close_ms:
                k = i
        return k

    cand, labels = [], {}
    for sym, cs in series.items():
        if not L.tradable_base(sym):
            continue
        for idx, ts, f in L.aligned_features(cs, btc, keep=lambda t: t + BAR_MS >= first_ms,
                                             is_btc=sym.startswith("BTC/")):
            if f["_dollar"] < L.MIN_DOLLAR_BAR:
                continue
            k = active(ts + BAR_MS)
            if k is None:
                continue
            m = vs[k]["model"]
            th = m.get("theta") or {}
            for d in ("LONG", "SHORT"):
                ev = L.predict(m.get(d), f)
                if ev is not None and ev > max(th.get(d, 0.0), 0.0):
                    cand.append((ts, sym, d, ev, vs[k]["version"]))
                    labels[(sym, ts, d)] = (cs, idx, f["_atr"])
    out = []
    for r in L.select(cand, theta=-1e9):
        cs, idx, atr = labels[(r[1], r[0], r[2])]
        out.append({"ts": r[0], "symbol": r[1], "dir": r[2], "ev": round(r[3], 4), "version": r[4],
                    "r": L.bracket_outcome(cs, idx, r[2], atr)})
    log(f"  test inainte: {len(out)} alegeri pe {len(series)} monede, {sum(1 for x in out if x['r'] is not None)} incheiate")
    return out


def forward_stats(picks):
    done = [(p["ts"] / 1000, p["r"]) for p in picks if p.get("r") is not None]
    st = _stats_daily(done)
    st["pending"] = sum(1 for p in picks if p.get("r") is None)
    st["from"] = time.strftime("%Y-%m-%d", time.gmtime(min(p["ts"] for p in picks) / 1000)) if picks else None
    st["recent"] = sorted(picks, key=lambda p: -p["ts"])[:12]
    return st


def label_live(snapshots, series):
    """Eticheteaza instantaneele live din lumanarile bursei (core.lab.bracket_outcome, aceeasi tranzactie).
    `series` = {simbol: lumanari 4h}. Instantaneele fara toate barele de dupa raman neetichetate."""
    out = []
    for s in snapshots:
        cs = series.get(s.get("symbol")) or []
        r = dict(s)
        pos = None
        if cs:
            lo_, hi_ = 0, len(cs) - 1
            while lo_ <= hi_:
                mid = (lo_ + hi_) // 2
                if cs[mid][0] == s["ts"]:
                    pos = mid
                    break
                if cs[mid][0] < s["ts"]:
                    lo_ = mid + 1
                else:
                    hi_ = mid - 1
        atr = (s.get("f") or {}).get("_atr")
        if pos is not None and atr:
            r["r_long"] = L.bracket_outcome(cs, pos, "LONG", atr)
            r["r_short"] = L.bracket_outcome(cs, pos, "SHORT", atr)
            if s.get("pick"):
                r["pick_r"] = r["r_long"] if s.get("pick_dir") == "LONG" else r["r_short"]
        out.append(r)
    return out
