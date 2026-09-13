#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
exchanges.py
=============
Registru de burse cu CAPABILITATI declarate si sondate.

PROBLEMA PE CARE O REZOLVA
--------------------------
Bursele difera prin ce date ofera. OKX nu da order book istoric; Bybit publica
seturi L2 gratuite, dar de ordinul zecilor de mii de GB pentru universul nostru,
deci inutilizabile intr-un job de 90 de minute. Tranzactiile recente, in schimb,
sunt accesibile prin ccxt pe majoritatea burselor - de acolo iese order flow-ul,
dar doar LIVE, nu retroactiv.

Solutia nu e sa renunt la evidentele care nu merg peste tot, ci sa le fac
CONDITIONATE si EXPLICITE:
  - fiecare bursa declara ce capabilitati are
  - fiecare evidenta declara ce capabilitate ii trebuie
  - evidentele fara suport sunt omise, nu inlocuite cu valori inventate
  - setul activ de capabilitati intra in SEMNATURA care separa invatarea

Ultimul punct e cel care previne dezastrul tacut. Un plan creat cu order flow
si unul creat fara au vectori de caracteristici diferiti. Fara semnatura,
agentul ar invata din amandoua ca si cum ar fi acelasi lucru - exact greseala
pe care versionarea geometriei o previne la schimbarile de timeframe.

CAPABILITATI
------------
  ohlcv             lumanari istorice - obligatoriu, toate bursele
  orderbook_live    adancime curenta (ccxt fetch_order_book)
  trades_live       tranzactii recente cu partea agresoare (ccxt fetch_trades)
                    -> order flow / footprint delta, DOAR live
  orderbook_history arhive istorice de order book (doar Bybit public, dar
                    nefolosibile la scara noastra - declarat pentru corectitudine)
"""

import time

CAP_OHLCV = "ohlcv"
CAP_ORDERBOOK_LIVE = "orderbook_live"
CAP_TRADES_LIVE = "trades_live"
CAP_ORDERBOOK_HISTORY = "orderbook_history"

ALL_CAPS = [CAP_OHLCV, CAP_ORDERBOOK_LIVE, CAP_TRADES_LIVE, CAP_ORDERBOOK_HISTORY]

# Capabilitatile DECLARATE. Sunt un punct de plecare; `probe_exchange` le
# verifica efectiv, pentru ca o bursa poate declara o metoda in ccxt si totusi
# sa o blocheze pe IP-uri de cloud sau sa o limiteze pe regiune.
REGISTRY = {
    "okx":    {"label": "OKX",     "declared": [CAP_OHLCV, CAP_ORDERBOOK_LIVE, CAP_TRADES_LIVE]},
    "kucoin": {"label": "KuCoin",  "declared": [CAP_OHLCV, CAP_ORDERBOOK_LIVE, CAP_TRADES_LIVE]},
    "gate":   {"label": "Gate.io", "declared": [CAP_OHLCV, CAP_ORDERBOOK_LIVE, CAP_TRADES_LIVE]},
    "mexc":   {"label": "MEXC",    "declared": [CAP_OHLCV, CAP_ORDERBOOK_LIVE, CAP_TRADES_LIVE]},
    "kraken": {"label": "Kraken",  "declared": [CAP_OHLCV, CAP_ORDERBOOK_LIVE, CAP_TRADES_LIVE]},
    # Bybit publica arhive L2 gratuite la public.bybit.com. Declarat ca sa fie
    # vizibil in dashboard, dar NEFOLOSIT: pentru 59 de simboluri pe 5 ani ar
    # insemna zeci de mii de GB, adica sute de ore de descarcare.
    "bybit":  {"label": "Bybit",   "declared": [CAP_OHLCV, CAP_ORDERBOOK_LIVE,
                                                CAP_TRADES_LIVE, CAP_ORDERBOOK_HISTORY]},
}

DEFAULT_ORDER = ["okx", "kucoin", "gate", "mexc", "kraken", "bybit"]


def probe_exchange(ccxt_mod, exchange_id, probe_symbol=None, timeout_note=""):
    """Incearca sa se conecteze si sa verifice ce capabilitati chiar functioneaza.

    Returneaza o fisa de stare folosita si de logica de scanare, si de dashboard.
    Nu arunca exceptii: o bursa care nu raspunde e o stare valida, nu o eroare.
    """
    info = REGISTRY.get(exchange_id, {"label": exchange_id, "declared": [CAP_OHLCV]})
    card = {
        "id": exchange_id, "label": info["label"],
        "declared": list(info["declared"]),
        "available": [], "connected": False, "markets": 0,
        "error": None, "checked_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
    }

    cls = getattr(ccxt_mod, exchange_id, None)
    if cls is None:
        card["error"] = f"'{exchange_id}' nu exista in ccxt (redenumit sau eliminat)"
        return card, None

    try:
        ex = cls({"enableRateLimit": True})
        markets = ex.load_markets()
    except Exception as exc:
        card["error"] = str(exc)[:180]
        return card, None

    card["connected"] = True
    card["markets"] = len(markets)
    card["available"].append(CAP_OHLCV)

    sym = probe_symbol
    if not sym:
        for cand in markets:
            if cand.endswith("/USDT") and markets[cand].get("active", True):
                sym = cand
                break
    if not sym:
        card["error"] = "conectat, dar nicio pereche USDT activa"
        return card, ex

    if CAP_ORDERBOOK_LIVE in info["declared"]:
        try:
            ob = ex.fetch_order_book(sym, limit=20)
            if ob and (ob.get("bids") or ob.get("asks")):
                card["available"].append(CAP_ORDERBOOK_LIVE)
        except Exception:
            pass

    if CAP_TRADES_LIVE in info["declared"]:
        try:
            tr = ex.fetch_trades(sym, limit=50)
            if tr and any(t.get("side") for t in tr):
                card["available"].append(CAP_TRADES_LIVE)
        except Exception:
            pass

    return card, ex


def capability_signature(caps):
    """Semnatura scurta a setului de capabilitati active.

    Intra in versiunea geometriei: planuri create cu seturi diferite de evidente
    NU trebuie sa ajunga in aceeasi calibrare. `of` = doar OHLCV (cazul
    backtest-ului), `ofl` = ohlcv + order flow live, si asa mai departe.
    """
    letters = {CAP_OHLCV: "o", CAP_ORDERBOOK_LIVE: "b", CAP_TRADES_LIVE: "f",
               CAP_ORDERBOOK_HISTORY: "h"}
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
