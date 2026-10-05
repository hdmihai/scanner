# -*- coding: utf-8 -*-
"""core.scoring - scorarea semnalelor: EMA, RSI, ATR si scorul ponderat al unui simbol (directie,
scor ajustat la risc, componente). Logica pura.
"""

import math


def round_price(value, sig=8):
    """Rotunjeste la cifre SEMNIFICATIVE, nu la un numar fix de zecimale.

    BUG FIX: peste tot se folosea `round(x, 6)`. Pentru o moneda la 0.0000234,
    ATR-ul tipic (~2%) e 0.00000047, care rotunjit la 6 zecimale devine 0.0.
    De aici `compute_trade_plan` returna None si scannerul crapa cu
    "TypeError: 'NoneType' object is not subscriptable".

    Chiar si cand nu crapa, era gresit: la un pret de 0.00000089, entry, SL,
    TP1 si TP2 deveneau toate 0.000001 - planul aratand valid, dar complet
    inutil. Universul top-200 contine multe monede sub 0.0001, deci problema
    afecta o parte reala din scanari, tacut.
    """
    if value is None or value == 0:
        return value
    exponent = math.floor(math.log10(abs(value)))
    decimals = max(0, min(sig - 1 - exponent, 18))
    return round(value, decimals)


def ema(values, period):
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    return sum(trs[-period:]) / period


def ema_series_full(values, period):
    """La fel ca ema(), dar returneaza toata seria (pentru desenat pe grafic in dashboard), nu doar ultima valoare."""
    if len(values) < period:
        return [None] * len(values)
    k = 2 / (period + 1)
    series = [None] * (period - 1)
    e = sum(values[:period]) / period
    series.append(e)
    for v in values[period:]:
        e = v * k + e * (1 - k)
        series.append(e)
    return series


RSI_LONG = (45, 75)   # banda RSI pentru LONG


RSI_SHORT = (25, 55)  # banda RSI pentru SHORT


REVERSE_SIGNAL = False  # diagnostic: inverseaza directia semnalului


def score_symbol(ohlcv, weights):
    """Primeste lumanari OHLCV brute de la exchange si returneaza un scor, directie si componente, sau None daca nu exista semnal clar."""
    closes = [c[4] for c in ohlcv]
    highs = [c[2] for c in ohlcv]
    lows = [c[3] for c in ohlcv]
    volumes = [c[5] for c in ohlcv]

    if len(closes) < 60:
        return None

    ema20 = ema(closes[-100:], 20)
    ema50 = ema(closes[-150:], 50)
    r = rsi(closes, 14)
    a = atr(highs, lows, closes, 14)
    price = closes[-1]
    avg_vol = sum(volumes[-20:]) / 20
    vol_now = volumes[-1]

    if not ema20 or not ema50 or not a or a == 0:
        return None

    trend_up = ema20 > ema50
    trend_strength = min(abs(ema20 - ema50) / ema50 * 20, 1.0)

    lo_l, hi_l = RSI_LONG
    lo_s, hi_s = RSI_SHORT
    if trend_up and lo_l <= r <= hi_l:
        direction = "LONG"
        momentum_strength = min((r - lo_l) / max(hi_l - lo_l, 1), 1.0)
    elif (not trend_up) and lo_s <= r <= hi_s:
        direction = "SHORT"
        momentum_strength = min((hi_s - r) / max(hi_s - lo_s, 1), 1.0)
    else:
        return None  # fara semnal clar in acest moment

    if REVERSE_SIGNAL:
        direction = "SHORT" if direction == "LONG" else "LONG"

    atr_pct = a / price
    volatility_score = max(1.0 - abs(atr_pct - 0.02) / 0.02, 0.0)  # favorizeaza ~2% ATR
    volume_score = min(vol_now / avg_vol, 1.5) / 1.5 if avg_vol > 0 else 0.3

    components = {
        "trend": round(trend_strength, 3),
        "momentum": round(momentum_strength, 3),
        "volatility": round(volatility_score, 3),
        "volume": round(volume_score, 3),
    }

    weighted_sum = sum(components[k] * weights.get(k, 1.0) for k in components)
    max_possible = sum(weights.get(k, 1.0) for k in components)
    risk_adjusted = round((weighted_sum / max_possible) * 100) if max_possible else 0
    probability = round(min(50 + risk_adjusted * 0.35, 88), 1)
    expected_r = round(1.5 + (risk_adjusted / 100) * 4, 2)

    return {
        "direction": direction,
        "risk_adjusted": risk_adjusted,
        "probability": probability,
        "expected_r": expected_r,
        "components": components,
        "price": price,
        "atr": round_price(a),
    }
