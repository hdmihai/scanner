#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lab_live.py - adaptorul laboratorului la scanare (portul LAB din core/scan.py, legat in
crypto_ai_scanner.py).

La fiecare scanare, pentru fiecare moneda scanata (bare de 4h inchise, deja aduse de scanare):
  - caracteristicile la ultima bara inchisa (core.lab.last_features - aceeasi functie ca pe istoric);
  - EV-ul estimat de modelul publicat de laborator pentru LONG si SHORT (data/memory_status.json ->
    "lab" -> "model", scris de jobul de memorie) si alegerea agentului pe bara (core.lab.select);
  - INSTANTANEUL (caracteristici, EV, alegere, versiunea modelului) in data/lab_live.json - o data pe
    (moneda, bara). Jobul de memorie le muta in depozit, le eticheteaza cand tranzactia s-a incheiat si
    invata din ele: asa agentul invata din FIECARE scanare, inclusiv din candidatii neales.
Nu arunca exceptii in scanare: la o problema scrie eroarea in fisier si scanarea continua.
"""

import json
import os
import time

from core import lab as L

LIVE_FILE = os.path.join("data", "lab_live.json")
STATUS_FILE = os.path.join("data", "memory_status.json")
KEEP_S = 48 * 3600            # instantaneele raman in git 2 zile; jobul de memorie (la 6 ore) le muta in depozit


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(path + ".tmp", path)


def _round(f):
    return {k: (float(f"{v:.6g}") if isinstance(v, float) else v) for k, v in f.items()}


def snapshot_rows(ohlcv_cache, btc, lab, exchange_id):
    """Instantaneele barei curente (pur, testabil): [(rand, scor)] pentru monedele cu istorie suficienta."""
    models = (lab or {}).get("model") or {}
    theta = models.get("theta") or {}
    out = []
    for sym, cs in sorted((ohlcv_cache or {}).items()):
        if not L.tradable_base(sym) or not cs:
            continue
        f = L.last_features(cs, btc, is_btc=sym.startswith("BTC/"))
        if f is None:
            continue
        sc = L.score_symbol(models, f) if models.get("LONG") else None
        row = {"ts": int(cs[-1][0]), "symbol": sym, "exchange": exchange_id, "f": _round(f),
               "ev_long": None if not sc or sc["ev_long"] is None else round(sc["ev_long"], 5),
               "ev_short": None if not sc or sc["ev_short"] is None else round(sc["ev_short"], 5),
               "model_version": (lab or {}).get("version"), "state": (lab or {}).get("state"),
               "liquid": f["_dollar"] >= L.MIN_DOLLAR_BAR, "pick": False, "pick_dir": None}
        out.append(row)
    # alegerea agentului pe bara: aceleasi reguli ca in walk-forward (prag pe directie, cel mult TOP_K)
    cand = []
    for r in out:
        if not r["liquid"]:
            continue
        for d, key in (("LONG", "ev_long"), ("SHORT", "ev_short")):
            if r[key] is not None and r[key] > max(theta.get(d, 0.0), 0.0):
                cand.append((r["ts"], r["symbol"], d, r[key]))
    chosen = {(c[1], c[0]): c[2] for c in L.select(cand, theta=-1e9)}
    for r in out:
        d = chosen.get((r["symbol"], r["ts"]))
        if d:
            r["pick"], r["pick_dir"] = True, d
    return out


def update(exchange, ohlcv_cache, timeframe, exchange_id=None):
    """Portul LAB: instantaneele scanarii curente in data/lab_live.json. Intoarce un rezumat scurt."""
    doc = _load(LIVE_FILE, {}) or {}
    try:
        if timeframe != "4h":
            doc.update(error=f"laboratorul invata pe bare de 4h; scanarea ruleaza pe {timeframe}",
                       when=time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()))
            _save(LIVE_FILE, doc)
            return {"skipped": doc["error"]}
        btc = (ohlcv_cache or {}).get("BTC/USDT")
        if not btc:
            o = exchange.fetch_ohlcv("BTC/USDT", timeframe="4h", limit=260)
            btc = o[:-1] if o and len(o) > 1 else None
        if not btc:
            raise RuntimeError("lipsesc barele BTC/USDT (referinta fortei relative)")
        lab = (_load(STATUS_FILE, {}) or {}).get("lab") or {}
        rows = snapshot_rows(ohlcv_cache, btc, lab, exchange_id)
        now = time.time()
        old = [r for r in (doc.get("snapshots") or []) if now - r.get("ts", 0) / 1000 < KEEP_S]
        have = {(r["symbol"], r["ts"]) for r in old}
        new = [r for r in rows if (r["symbol"], r["ts"]) not in have]
        snaps = old + new
        bar_ts = max((r["ts"] for r in rows), default=None)
        cur = [r for r in snaps if r["ts"] == bar_ts]
        ranked = sorted((r for r in cur if r["ev_long"] is not None),
                        key=lambda r: -max(r["ev_long"], r["ev_short"]))
        latest = []
        for r in ranked[:12]:
            d = "LONG" if r["ev_long"] >= r["ev_short"] else "SHORT"
            lv = L.trade_levels(r["f"], r["pick_dir"] or d)
            latest.append({"symbol": r["symbol"], "dir": r["pick_dir"] or d, "pick": r["pick"],
                           "ev": round(max(r["ev_long"], r["ev_short"]), 4), "ev_long": r["ev_long"],
                           "ev_short": r["ev_short"], "liquid": r["liquid"],
                           **{k: float(f"{v:.6g}") for k, v in lv.items()}})
        doc = {"when": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "bar_ts": bar_ts,
               "model_version": lab.get("version"), "state": lab.get("state") or "FARA_MODEL",
               "scored": len(rows), "new": len(new), "latest": latest, "snapshots": snaps}
        _save(LIVE_FILE, doc)
        return {"scored": len(rows), "new": len(new), "picks": sum(1 for r in cur if r["pick"])}
    except Exception as e:
        doc.update(error=f"{type(e).__name__}: {str(e)[:200]}",
                   when=time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()))
        _save(LIVE_FILE, doc)
        print(f"[!] laborator: {e}")
        return {"error": str(e)[:200]}
