# -*- coding: utf-8 -*-
"""adapters.memory.market_history - istoricul complet de lumanari de pe bursa principala (ccxt).

Aceeasi metoda ca backtest.py (find_listing_ts + paginare): bursele intorc GOL pentru un `since`
anterior listarii, deci startul se cauta binar. Doar lumanari INCHISE (ultima, in formare, se scoate)."""

import time


def tradable_usdt_spot(markets):
    """Perechile spot active cotate in USDT, fara stablecoin-uri si tokeni cu levier."""
    stable = {"USDT", "USDC", "DAI", "TUSD", "FDUSD", "USDD", "PYUSD", "USDE", "USDS", "BUSD", "EUR", "GBP"}
    out = []
    for sym, m in (markets or {}).items():
        base = str(m.get("base") or "").upper()
        if (m.get("spot") is not False and m.get("type", "spot") == "spot" and m.get("quote") == "USDT" and m.get("active", True) is not False
                and base not in stable and not any(base.endswith(x) for x in ("3L", "3S", "UP", "DOWN", "BULL", "BEAR"))):
            out.append(sym)
    return sorted(out)


def listing_ts(exchange, symbol, tf, max_days=3650, probes=16):
    now = exchange.milliseconds()
    oldest = now - max_days * 86400000

    def has(ts):
        try:
            return bool(exchange.fetch_ohlcv(symbol, timeframe=tf, since=ts, limit=5))
        except Exception:
            return False
    if has(oldest):
        return oldest
    hi = next((now - d * 86400000 for d in (30, 7, 2, 1) if has(now - d * 86400000)), None)
    if hi is None:
        return None
    lo = oldest
    for _ in range(probes):
        if hi - lo < 86400000:
            break
        mid = (lo + hi) // 2
        hi, lo = (mid, lo) if has(mid) else (hi, mid)
    return hi


def fetch_since(exchange, symbol, tf, since, deadline, page=300):
    """Lumanarile inchise de la `since` (ms) pana acum sau pana la `deadline` (time.monotonic)."""
    ms = exchange.parse_timeframe(tf) * 1000
    now = exchange.milliseconds()
    out, stall = {}, 0
    while time.monotonic() < deadline:
        try:
            batch = exchange.fetch_ohlcv(symbol, timeframe=tf, since=since, limit=page)
        except Exception as e:
            print(f"  [!] {symbol} {tf}: {str(e)[:120]}")
            break
        if not batch:
            break
        for c in batch:
            if c[0] + ms <= now:                    # doar lumanari inchise
                out[c[0]] = c
        nxt = batch[-1][0] + ms
        if nxt <= since:
            stall += 1
            if stall > 2:
                break
            nxt = since + ms * len(batch)
        since = nxt
        if batch[-1][0] >= now - ms:
            break
    return [out[k] for k in sorted(out)]
