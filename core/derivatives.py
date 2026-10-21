# -*- coding: utf-8 -*-

def _pq(level):
    """Extrage (price, qty) din orice format: [p,q] sau [p,q,orders] sau (p,q)."""
    try:
        return float(level[0]), float(level[1])
    except (IndexError, TypeError, ValueError):
        return None

def ob_imbalance(ob, depth_pct=0.01):
    if not ob or not ob.get("bids") or not ob.get("asks"):
        return {"ob_imbalance": 0.0, "ob_spread_bps": 0.0}
    bb, ba = _pq(ob["bids"][0]), _pq(ob["asks"][0])
    if not bb or not ba or bb[0] <= 0 or ba[0] <= 0:
        return {"ob_imbalance": 0.0, "ob_spread_bps": 0.0}
    mid = (bb[0] + ba[0]) / 2.0
    lo, hi = mid * (1 - depth_pct), mid * (1 + depth_pct)
    bu = au = 0.0
    for lv in ob["bids"]:
        p = _pq(lv)
        if p and p[0] >= lo: bu += p[0] * p[1]
    for lv in ob["asks"]:
        p = _pq(lv)
        if p and p[0] <= hi: au += p[0] * p[1]
    tot = bu + au
    return {"ob_imbalance": round((bu-au)/tot if tot > 0 else 0.0, 6),
            "ob_spread_bps": round(((ba[0]-bb[0])/mid)*1e4 if mid > 0 else 0.0, 4)}

def funding_zscore(hist, window=8):
    rates = []
    for h in (hist or []):
        v = h.get("fundingRate")
        if v is not None:
            try: rates.append(float(v))
            except (TypeError, ValueError): pass
    if len(rates) < 3:
        return {"funding_z": 0.0, "funding_last": 0.0}
    w = rates[-window:]
    m = sum(w) / len(w)
    sd = (sum((r-m)**2 for r in w) / max(1, len(w)-1)) ** 0.5
    return {"funding_z": round((rates[-1]-m)/sd if sd > 1e-12 else 0.0, 4),
            "funding_last": round(rates[-1], 8)}

def oi_change(hist, window=6):
    vals = []
    for h in (hist or []):
        v = h.get("openInterestValue") or h.get("openInterestAmount")
        if v is not None:
            try: vals.append(float(v))
            except (TypeError, ValueError): pass
    if len(vals) < 2:
        return {"oi_change": 0.0}
    ref = vals[max(0, len(vals) - 1 - min(window, len(vals) - 1))]
    return {"oi_change": round((vals[-1] - ref) / ref if ref > 0 else 0.0, 6)}

def liquidity_change(ohlcv, window=12):
    qvs = []
    for bar in (ohlcv or []):
        try:
            if len(bar) >= 6: qvs.append(float(bar[4]) * float(bar[5]))
        except (TypeError, ValueError): pass
    if len(qvs) < 2:
        return {"liq_change": 0.0}
    ref = qvs[max(0, len(qvs) - 1 - min(window, len(qvs) - 1))]
    return {"liq_change": round((qvs[-1] - ref) / ref if ref > 0 else 0.0, 6)}

FEATURE_KEYS = ["ob_imbalance", "funding_z", "oi_change", "liq_change"]

def derivatives_features(ohlcv=None, order_book=None, funding_history=None, oi_history=None):
    out = {}
    out.update(ob_imbalance(order_book))
    out.update(funding_zscore(funding_history))
    out.update(oi_change(oi_history))
    out.update(liquidity_change(ohlcv))
    return {k: out[k] for k in FEATURE_KEYS + ["ob_spread_bps", "funding_last"]}
