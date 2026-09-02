#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
indicators.py
==============
Indicatorii vizibili in pozele de referinta, care lipseau din proiect:
VWAP, Volume Profile (POC / VAH / VAL), SuperTrend, MACD, si setul complet
de EMA 9/20/50/100/200.

Toti se calculeaza din OHLCV-ul deja descarcat de scanner - zero apeluri API
in plus. Python pur, fara pandas/numpy, ca sa mearga si pe un runner minimal
sau in Termux.

Fiecare functie e determinist definita si testabila; nu contine euristici
"magice" pe care sa nu le pot justifica.
"""


# ================================== EMA ====================================

def ema_series(values, period):
    """EMA clasica, cu media simpla ca samanta pentru primele `period` valori.
    Returneaza o serie de aceeasi lungime, cu None inainte de a fi definita."""
    if len(values) < period:
        return [None] * len(values)
    k = 2 / (period + 1)
    out = [None] * (period - 1)
    e = sum(values[:period]) / period
    out.append(e)
    for v in values[period:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def ema_last(values, period):
    s = ema_series(values, period)
    return s[-1] if s else None


def ema_set(closes, periods=(9, 20, 50, 100, 200)):
    """Setul de EMA din poze. Cele care n-au destule date raman None."""
    return {f"ema{p}": ema_last(closes, p) for p in periods}


# ================================= VWAP ====================================

def vwap(candles, period=None):
    """Volume Weighted Average Price pe pretul tipic (H+L+C)/3.

    `period` = cate lumanari intra in calcul (None = toate). VWAP-ul "adevarat"
    se reseteaza la inceputul sesiunii; cum lucram pe crypto, care e 24/7 si
    fara sesiuni clare, folosesc o fereastra glisanta - e alegerea uzuala si o
    declar explicit ca sa nu para altceva decat este."""
    data = candles[-period:] if period else candles
    total_pv = 0.0
    total_v = 0.0
    for c in data:
        typical = (c[2] + c[3] + c[4]) / 3
        vol = c[5] or 0
        total_pv += typical * vol
        total_v += vol
    return (total_pv / total_v) if total_v > 0 else None


# =========================== VOLUME PROFILE ================================

def volume_profile(candles, bins=24, value_area=0.70):
    """Volume Profile: POC, VAH, VAL - exact etichetele din poze.

    POC (Point of Control) = nivelul de pret cu cel mai mare volum tranzactionat.
    Value Area = zona din jurul POC care contine `value_area` din volumul total
    (standard: 70%); VAH/VAL sunt marginea de sus/jos a acestei zone.

    Volumul fiecarei lumanari e distribuit uniform pe intervalul ei high-low.
    E aproximarea standard cand nu ai date de volum pe nivel de pret - o spun
    explicit pentru ca un profil construit din date de tick ar fi mai precis.
    """
    if not candles:
        return None
    lo = min(c[3] for c in candles)
    hi = max(c[2] for c in candles)
    if hi <= lo:
        return None

    step = (hi - lo) / bins
    hist = [0.0] * bins

    for c in candles:
        c_low, c_high, vol = c[3], c[2], (c[5] or 0)
        if vol <= 0 or c_high <= c_low:
            continue
        first = max(0, min(bins - 1, int((c_low - lo) / step)))
        last = max(0, min(bins - 1, int((c_high - lo) / step)))
        n = last - first + 1
        share = vol / n
        for b in range(first, last + 1):
            hist[b] += share

    total = sum(hist)
    if total <= 0:
        return None

    poc_idx = hist.index(max(hist))
    center = lambda i: lo + step * (i + 0.5)

    # extind simetric de la POC pana acopar procentul cerut din volum
    included = {poc_idx}
    acc = hist[poc_idx]
    low_i = high_i = poc_idx
    while acc < total * value_area and (low_i > 0 or high_i < bins - 1):
        take_low = hist[low_i - 1] if low_i > 0 else -1
        take_high = hist[high_i + 1] if high_i < bins - 1 else -1
        if take_high >= take_low:
            high_i += 1
            included.add(high_i)
            acc += hist[high_i]
        else:
            low_i -= 1
            included.add(low_i)
            acc += hist[low_i]

    return {
        "poc": round(center(poc_idx), 8),
        "vah": round(center(max(included)), 8),
        "val": round(center(min(included)), 8),
        "value_area_pct": round(100 * acc / total, 1),
    }


# ================================ ATR ======================================

def atr_series(highs, lows, closes, period=14):
    """Seria ATR (Wilder), necesara pentru SuperTrend."""
    if len(closes) < period + 1:
        return [None] * len(closes)
    trs = [None]
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    out = [None] * period
    a = sum(trs[1:period + 1]) / period
    out.append(a)
    for i in range(period + 1, len(closes)):
        a = (a * (period - 1) + trs[i]) / period
        out.append(a)
    return out[:len(closes)]


# ============================= SUPERTREND ==================================

def supertrend(candles, period=10, multiplier=3.0):
    """SuperTrend - eticheta "SuperTrend Bullish/Bearish" din poze.

    Benzi bazate pe ATR in jurul pretului median, cu regula standard de
    "trailing": banda nu se relaxeaza contra trendului, iar directia se
    inverseaza cand pretul inchide dincolo de banda opusa.
    """
    if len(candles) < period + 2:
        return None
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    closes = [c[4] for c in candles]
    atr = atr_series(highs, lows, closes, period)

    final_upper = final_lower = None
    trend_up = True

    for i in range(len(closes)):
        if atr[i] is None:
            continue
        mid = (highs[i] + lows[i]) / 2
        upper = mid + multiplier * atr[i]
        lower = mid - multiplier * atr[i]

        if final_upper is None:
            final_upper, final_lower = upper, lower
            trend_up = closes[i] >= mid
            continue

        # banda superioara coboara doar daca pretul anterior era sub ea
        final_upper = upper if (upper < final_upper or closes[i - 1] > final_upper) else final_upper
        final_lower = lower if (lower > final_lower or closes[i - 1] < final_lower) else final_lower

        if trend_up and closes[i] < final_lower:
            trend_up = False
        elif (not trend_up) and closes[i] > final_upper:
            trend_up = True

    return {
        "direction": "BULLISH" if trend_up else "BEARISH",
        "level": round(final_lower if trend_up else final_upper, 8),
    }


# ================================ MACD =====================================

def macd(closes, fast=12, slow=26, signal=9):
    """MACD clasic: linia MACD, linia de semnal si histograma."""
    if len(closes) < slow + signal:
        return None
    ema_fast = ema_series(closes, fast)
    ema_slow = ema_series(closes, slow)
    macd_line = [(f - s) if (f is not None and s is not None) else None
                 for f, s in zip(ema_fast, ema_slow)]
    defined = [v for v in macd_line if v is not None]
    if len(defined) < signal:
        return None
    sig = ema_series(defined, signal)
    if sig[-1] is None:
        return None
    return {
        "macd": round(macd_line[-1], 8),
        "signal": round(sig[-1], 8),
        "histogram": round(macd_line[-1] - sig[-1], 8),
        "bullish": macd_line[-1] > sig[-1],
    }


# ============================== AGREGATOR ==================================

def compute_all(candles, vwap_period=100, profile_bins=24):
    """Toti indicatorii dintr-un singur set de lumanari."""
    if not candles or len(candles) < 30:
        return {}
    closes = [c[4] for c in candles]
    result = {
        "emas": ema_set(closes),
        "vwap": vwap(candles, vwap_period),
        "volume_profile": volume_profile(candles, profile_bins),
        "supertrend": supertrend(candles),
        "macd": macd(closes),
        "price": closes[-1],
    }
    vp = result["volume_profile"]
    if vp:
        # unde sta pretul fata de value area - context util intr-o singura fraza
        p = closes[-1]
        result["price_vs_value_area"] = (
            "PESTE VAH" if p > vp["vah"] else "SUB VAL" if p < vp["val"] else "IN VALUE AREA"
        )
    return result
