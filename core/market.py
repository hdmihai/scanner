# -*- coding: utf-8 -*-
"""core.market - ce extrage nucleul din datele unei burse: fluxul de tranzactii, open
interest si semnatura capabilitatilor (intra in geometria planurilor).

Logica pura peste portul ports.market_data: primeste conexiunea, nu o creeaza.
Mutat din exchanges.py in Etapa 2; exchanges.py le re-exporta pentru compatibilitate.
"""

from ports.market_data import (ALL_CAPS, CAP_OHLCV, CAP_OPEN_INTEREST, CAP_ORDERBOOK_HISTORY,
                               CAP_ORDERBOOK_LIVE, CAP_TRADES_LIVE)


def capability_signature(caps):
    """Semnatura scurta a setului de capabilitati active.

    Intra in versiunea geometriei: planuri create cu seturi diferite de evidente
    NU trebuie sa ajunga in aceeasi calibrare. `of` = doar OHLCV (cazul
    backtest-ului), `ofl` = ohlcv + order flow live, si asa mai departe.
    """
    letters = {CAP_OHLCV: "o", CAP_ORDERBOOK_LIVE: "b", CAP_TRADES_LIVE: "f",
               CAP_ORDERBOOK_HISTORY: "h", CAP_OPEN_INTEREST: "i"}
    return "".join(letters[c] for c in ALL_CAPS if c in set(caps or [])) or "none"


def order_flow(ex, symbol, caps, limit=200):
    """Dezechilibrul de flux din tranzactiile recente: 'order flow favors
    sellers (78.2% sell)' din sistemul de referinta.

    Returneaza None daca bursa nu ofera capabilitatea - si atunci evidenta
    corespunzatoare pur si simplu lipseste, in loc sa fie inventata.
    """
    if CAP_TRADES_LIVE not in (caps or []):
        return None
    try:
        trades = ex.fetch_trades(symbol, limit=limit)
    except Exception:
        return None
    buy = sell = 0.0
    for t in trades or []:
        amt = t.get("amount") or 0
        if t.get("side") == "buy":
            buy += amt
        elif t.get("side") == "sell":
            sell += amt
    total = buy + sell
    if total <= 0:
        return None
    return {"buy": round(buy, 6), "sell": round(sell, 6),
            "sell_pct": round(100 * sell / total, 1),
            "buy_pct": round(100 * buy / total, 1),
            "trades": len(trades or [])}


def open_interest(ex, symbol, caps):
    """Open interest curent, daca bursa il ofera. None altfel - iar atunci harta
    de lichidari se construieste nescalata, ceea ce e perfect utilizabil."""
    if CAP_OPEN_INTEREST not in (caps or []):
        return None
    try:
        oi = ex.fetch_open_interest(symbol)
    except Exception:
        return None
    if not oi:
        return None
    return oi.get("openInterestAmount") or oi.get("openInterestValue")
