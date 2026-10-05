# -*- coding: utf-8 -*-
"""core.universe - universul scanat: perechile eligibile dintr-o bursa (watchlist cu aliasuri,
monedele de cotare acceptate, perechea cu volumul cel mai mare per token).
"""

from core.config import CONFIG


def build_eligible_pairs(markets, tickers, scope):
    """Construieste lista de perechi eligibile, cu doua imbunatatiri fata de
    varianta initiala:

    1. MAI MULTE MONEDE DE COTARE. Prima rulare reala a gasit doar 34 de
       simboluri pe Kraken, pentru ca acolo aproape totul e cotat in USD, nu
       USDT (44 perechi USDT din 1440 de piete). Accept acum USDT/USDC/USD.
    2. DEDUPLICARE PE SIMBOL DE BAZA. BTC/USDT si BTC/USD sunt acelasi
       proiect - pastrez varianta cu volum mai mare, ca sa nu apara de doua
       ori in universul scanat si sa umfle artificial numararea."""
    by_base = {}
    for symbol, m in markets.items():
        if not m.get("active", True):
            continue
        parts = symbol.split("/")
        if len(parts) != 2:
            continue
        base, quote = parts[0].upper(), parts[1].upper()
        if quote not in CONFIG["quotes"]:
            continue
        if scope and base not in scope:
            continue
        vol = tickers.get(symbol, {}).get("quoteVolume", 0) or 0
        prev = by_base.get(base)
        if prev is None or vol > prev[1]:
            by_base[base] = (symbol, vol)

    pairs = sorted(by_base.values(), key=lambda t: t[1], reverse=True)
    return [symbol for symbol, _ in pairs]
