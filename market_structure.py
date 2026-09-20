#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
market_structure.py
====================
Panoul de structura de piata din capturile de referinta: regim, Golden/Death
Cross, Ichimoku si alinierea multi-timeframe.

DE CE ACESTE PATRU, SI IN ACEASTA ORDINE
-----------------------------------------
Toate evidentele de pana acum vin din ACELASI set de lumanari: EMA, MACD, RSI,
SuperTrend, VWAP sunt transformari ale aceleiasi serii de preturi. Informatia
lor se suprapune puternic, si de asta AUC-ul agentului creste greu - se adauga
caracteristici, dar nu se adauga informatie.

ALINIEREA MULTI-TIMEFRAME e singura de aici care aduce observatii cu adevarat
INDEPENDENTE. Un trend pe 1d nu e o functie de lumanarile de 4h; e alta serie,
cu alt continut. Restul (Ichimoku, cross, regim) sunt utile si standard, dar
raman derivate din aceleasi bare - le includ pentru ca sunt ieftine si pentru
ca structureaza ce era deja acolo, nu pentru ca ar aduce informatie noua.

Totul se calculeaza din OHLCV, deci exista identic in live si in backtest. Nu
adauga capabilitati noi si nu fragmenteaza invatarea.
"""


def ema_last(values, period):
    if not values or len(values) < period:
        return None
    k = 2.0 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def ichimoku(highs, lows, closes, tenkan=9, kijun=26, senkou=52):
    """Ichimoku clasic. `vote` e +1 peste nor, -1 sub, 0 in interior.

    Norul e format din Senkou A si B calculate ACUM dar proiectate inainte.
    Pentru pozitia pretului fata de nor folosesc valorile curente, nu cele
    proiectate - asta e conventia folosita de majoritatea platformelor cand
    raporteaza "above/below cloud" pentru bara curenta.
    """
    n = len(closes)
    if n < senkou or not highs or not lows:
        return None

    def mid(period):
        hi = max(highs[-period:])
        lo = min(lows[-period:])
        return (hi + lo) / 2.0

    t = mid(tenkan)
    k = mid(kijun)
    span_a = (t + k) / 2.0
    span_b = mid(senkou)
    price = closes[-1]
    top, bot = max(span_a, span_b), min(span_a, span_b)

    if price > top:
        pos, vote = "PESTE NOR", 1.0
    elif price < bot:
        pos, vote = "SUB NOR", -1.0
    else:
        pos, vote = "IN NOR", 0.0

    # cat de departe e pretul de nor, in latimi de nor - da intensitatea
    width = (top - bot) or (price * 0.001)
    if vote > 0:
        dist = (price - top) / width
    elif vote < 0:
        dist = (bot - price) / width
    else:
        dist = 0.0

    return {"position": pos, "vote": vote, "tenkan": t, "kijun": k,
            "span_a": span_a, "span_b": span_b,
            "strength": round(min(abs(dist) / 2.0, 1.0), 4)}


def ma_cross(closes, fast=50, slow=200):
    """Golden Cross / Death Cross pe EMA 50 si 200."""
    f, s = ema_last(closes, fast), ema_last(closes, slow)
    if f is None or s is None:
        return None
    golden = f > s
    gap = abs(f - s) / (s or 1)
    return {"type": "GOLDEN CROSS" if golden else "DEATH CROSS",
            "fast": f, "slow": s, "bullish": golden,
            "strength": round(min(gap / 0.08, 1.0), 4)}


def regime(closes, highs, lows, atr_value):
    """Clasificarea regimului, cu o masura de putere 0-100.

    Puterea combina alinierea mediilor mobile cu amplitudinea miscarii. Un
    trend in care mediile sunt ordonate si pretul s-a deplasat mult primeste
    scor mare; o piata laterala primeste scor mic, chiar daca e volatila.
    """
    if len(closes) < 60:
        return None
    price = closes[-1]
    e20, e50, e100 = ema_last(closes, 20), ema_last(closes, 50), ema_last(closes, 100)
    if None in (e20, e50, e100):
        return None

    up = price > e20 > e50 > e100
    down = price < e20 < e50 < e100
    span = max(highs[-60:]) - min(lows[-60:])
    move = abs(price - closes[-60]) / (span or price)

    if up or down:
        label = "TREND PUTERNIC SUS" if up else "TREND PUTERNIC JOS"
        strength = min(100, int(50 + move * 60))
        vote = 1.0 if up else -1.0
    elif price > e50:
        label, strength, vote = "TREND SLAB SUS", min(60, int(25 + move * 50)), 0.4
    elif price < e50:
        label, strength, vote = "TREND SLAB JOS", min(60, int(25 + move * 50)), -0.4
    else:
        label, strength, vote = "LATERAL", int(20 + move * 20), 0.0

    atr_pct = (atr_value / price * 100) if (atr_value and price) else None
    return {"label": label, "strength": strength, "vote": vote,
            "atr_pct": round(atr_pct, 2) if atr_pct is not None else None}


# Timeframe-urile de confirmare, relativ la cel principal. Cheia e timeframe-ul
# de baza; valorile sunt cele pe care le cer suplimentar. Ordinea e de la rapid
# la lent, ca in panoul din capturi.
CONFIRM_TIMEFRAMES = {
    "15m": ["5m", "1h", "4h"],
    "1h":  ["15m", "4h", "1d"],
    "4h":  ["1h", "1d", "1w"],
    "1d":  ["4h", "1w"],
}


def trend_vote(closes):
    """Votul de trend pentru o serie: +100 clar ascendent, -100 clar descendent."""
    if len(closes) < 50:
        return None
    price = closes[-1]
    e20, e50 = ema_last(closes, 20), ema_last(closes, 50)
    if e20 is None or e50 is None:
        return None
    score = 0
    if price > e20:
        score += 50
    else:
        score -= 50
    if e20 > e50:
        score += 50
    else:
        score -= 50
    return score


def multi_timeframe(base_tf, fetch_closes):
    """Alinierea pe mai multe timeframe-uri.

    `fetch_closes(tf)` returneaza lista de inchideri pentru timeframe-ul dat,
    sau None daca nu se poate. Un timeframe care nu raspunde e OMIS din vot,
    nu tratat ca neutru - un vot neutru inventat ar dilua semnalul celorlalte.

    ASTA e componenta care aduce informatie independenta: celelalte evidente
    sunt toate transformari ale aceleiasi serii de baza.
    """
    tfs = [base_tf] + CONFIRM_TIMEFRAMES.get(base_tf, [])
    rows, votes = [], []
    for tf in tfs:
        closes = fetch_closes(tf)
        if not closes:
            rows.append({"timeframe": tf, "trend": None, "score": None})
            continue
        v = trend_vote(closes)
        if v is None:
            rows.append({"timeframe": tf, "trend": None, "score": None})
            continue
        rows.append({"timeframe": tf,
                     "trend": "BULLISH" if v > 0 else ("BEARISH" if v < 0 else "NEUTRU"),
                     "score": v})
        votes.append(v)
    if not votes:
        return {"rows": rows, "alignment": None, "bullish": 0, "bearish": 0}
    bull = sum(1 for v in votes if v > 0)
    bear = sum(1 for v in votes if v < 0)
    return {"rows": rows,
            "alignment": round(sum(votes) / (100.0 * len(votes)), 4),
            "bullish": bull, "bearish": bear, "counted": len(votes)}


def liquidity_walls(order_book, price, top=3):
    """Zidurile de lichiditate din order book, cu putere relativa 0-100.

    Echivalentul panoului ADVANCED LIQUIDITY ZONES. Puterea e raportata la cel
    mai mare nivel din carte, deci e comparabila intre simboluri cu volume
    foarte diferite.
    """
    if not order_book:
        return None
    bids = order_book.get("bids") or []
    asks = order_book.get("asks") or []
    if not bids and not asks:
        return None

    def build(levels, side):
        # Accept ambele formate: perechi brute din ccxt ([pret, cantitate]) si
        # dictionare deja normalizate de scanner ({"price":..., "amount":...}).
        # Altfel ar trebui reconstruit order book-ul brut doar pentru aici.
        out = []
        for lv in levels:
            try:
                if isinstance(lv, dict):
                    p, amt = float(lv.get("price")), float(lv.get("amount"))
                else:
                    p, amt = float(lv[0]), float(lv[1])
            except (TypeError, ValueError, IndexError):
                continue
            if p <= 0 or amt <= 0:
                continue
            out.append({"side": side, "price": p, "amount": amt, "value": p * amt})
        out.sort(key=lambda x: -x["value"])
        return out[:top]

    walls = build(bids, "BID") + build(asks, "ASK")
    if not walls:
        return None
    peak = max(w["value"] for w in walls) or 1.0
    for w in walls:
        w["strength"] = int(round(100 * w["value"] / peak))
        w["distance_pct"] = round(100 * (w["price"] - price) / price, 3) if price else None
    walls.sort(key=lambda w: -w["strength"])

    bid_val = sum(w["value"] for w in walls if w["side"] == "BID")
    ask_val = sum(w["value"] for w in walls if w["side"] == "ASK")
    total = bid_val + ask_val
    return {"walls": walls,
            "bid_pct": round(100 * bid_val / total, 1) if total else None,
            "ask_pct": round(100 * ask_val / total, 1) if total else None}


def build(closes, highs, lows, atr_value, base_tf="4h", fetch_closes=None,
          order_book=None, price=None):
    """Panoul complet, asamblat. Orice componenta care nu se poate calcula
    lipseste din rezultat, in loc sa fie completata cu valori inventate."""
    px = price or (closes[-1] if closes else None)
    out = {
        "regime": regime(closes, highs, lows, atr_value),
        "cross": ma_cross(closes),
        "ichimoku": ichimoku(highs, lows, closes),
    }
    if fetch_closes is not None:
        out["mtf"] = multi_timeframe(base_tf, fetch_closes)
    if order_book is not None and px:
        out["liquidity"] = liquidity_walls(order_book, px)
    return out
