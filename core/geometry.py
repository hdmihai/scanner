# -*- coding: utf-8 -*-
"""core.geometry - geometria planului: pivoti, niveluri de structura, Fibonacci si planul de
tranzactionare (intrare pe pullback, SL, TP1/TP2 cu praguri minime in R). Logica pura;
backtest-ul variaza PULLBACK_ATR si SL_ATR prin crypto_ai_scanner (fatada le scrie aici).
"""

from core.scoring import round_price


def find_swing_points(highs, lows, lookback=5):
    """Puncte de swing simple: un maxim/minim local mai extrem decat 'lookback' lumanari de fiecare parte."""
    swing_highs, swing_lows = [], []
    for i in range(lookback, len(highs) - lookback):
        if highs[i] == max(highs[i - lookback:i + lookback + 1]):
            swing_highs.append(highs[i])
        if lows[i] == min(lows[i - lookback:i + lookback + 1]):
            swing_lows.append(lows[i])
    return swing_highs, swing_lows


def compute_structure_levels(highs, lows, n_levels=3):
    """Niveluri simple de suport/rezistenta, din cele mai recente puncte de swing (echivalentul S1-S3 / R1-R3 din poza ta)."""
    swing_highs, swing_lows = find_swing_points(highs, lows)
    resistance = sorted(set(round_price(h) for h in swing_highs[-12:]), reverse=True)[:n_levels]
    support = sorted(set(round_price(l) for l in swing_lows[-12:]), reverse=True)[-n_levels:]
    return {"resistance": resistance, "support": support}


def compute_fibonacci(highs, lows, lookback=100):
    """Retracement + extensie Fibonacci pe ultimul swing major (high/low) din fereastra de lookback."""
    swing_high = max(highs[-lookback:])
    swing_low = min(lows[-lookback:])
    diff = swing_high - swing_low
    retracement = {str(r): round_price(swing_high - diff * r) for r in (0.236, 0.382, 0.5, 0.618, 0.786)}
    extension = {str(r): round_price(swing_high + diff * (r - 1)) for r in (1.272, 1.618, 2.0)}
    return {"swing_high": swing_high, "swing_low": swing_low, "retracement": retracement, "extension": extension}


MIN_TP1_R = 1.0   # TP1 nu are voie mai aproape de 1R


MIN_TP2_R = 2.5   # TP2 trebuie sa justifice riscul


PULLBACK_ATR = 0.5  # cat de mult astept sa revina pretul inainte de intrare


SL_ATR = 1.5        # distanta stopului, in ATR


def compute_trade_plan(direction, price, atr_val, structure, fib):
    """Plan de tranzactionare: SL pe baza de ATR, TP1 la prima structura
    relevanta, TP2 la extensia Fibonacci 1.618.

    PRAGURI MINIME IN R (adaugate dupa analiza a 47 de planuri reale):
    Varianta initiala lua TP1 direct de la prima structura, fara sa verifice
    cat de departe e. Cand cea mai apropiata rezistenta era la 0.1% iar SL-ul
    la 1.5 ATR, iesea un TP1 la 0.05R - un plan care nu poate castiga. In
    datele reale, 11 din 47 de planuri aveau TP1 sub 0.35R, unul chiar la 0.00R.

    Efectul combinat cu regula "50% la TP1, apoi SL la breakeven": downside
    ramanea -1R intreg, dar upside era taiat la ~0.45R. Castigul mediu masurat
    a iesit 0.506R fata de pierdere medie 1.0R, deci ar fi fost nevoie de
    66.4% rata de succes doar pentru break-even. Observat: 34.8%.

    Acum structura e folosita doar daca ofera macar 1R; altfel TP1 se plaseaza
    la exact 1R. Nu inventez o tinta mai buna decat da piata - doar refuz sa
    generez planuri cu asteptare negativa prin constructie.
    """
    ext_1618 = fib["extension"]["1.618"]
    risk = atr_val * 1.5
    if risk <= 0:
        return None

    # BUG FIX (gasit pe planul real #62 SKR/USDT): varianta anterioara avea un
    # fallback pentru TP2 care nu verifica daca e dincolo de TP1. Cand structura
    # dadea un TP1 foarte departe (8.91R), fallback-ul punea TP2 la 2.50R - deci
    # MAI APROAPE decat TP1. Pretul ar fi atins TP2 primul, planul s-ar fi inchis
    # inregistrand 2.5R desi tinta structurala era la 8.9R, iar etapa TP1 nu s-ar
    # fi declansat niciodata. Acum TP2 e garantat dincolo de TP1, prin constructie.
    # INTRARE PE PULLBACK (v3): nu intru la pretul de semnal, ci astept o
    # revenire de PULLBACK_ATR. SL-ul ramane la nivelul structural (calculat din
    # pretul de semnal), deci riscul se micsoreaza si TP1 devine mult mai
    # aproape in termeni absoluti - 0.5 ATR fata de 1.69 ATR masurat pe planurile
    # v2. Daca pretul nu revine, planul expira fara pierdere.
    pullback = atr_val * PULLBACK_ATR
    entry = price - pullback if direction == "LONG" else price + pullback
    risk = abs(entry - (price - atr_val * SL_ATR if direction == "LONG" else price + atr_val * SL_ATR))
    if risk <= 0:
        return None

    if direction == "LONG":
        sl = price - atr_val * SL_ATR
        above = [r for r in structure["resistance"] if r >= entry + risk * MIN_TP1_R]
        tp1 = min(above) if above else entry + risk * MIN_TP1_R
        tp2_floor = max(tp1 + risk * 0.5, entry + risk * MIN_TP2_R)
        tp2_candidates = [c for c in (ext_1618, price + risk * 4) if c >= tp2_floor]
        tp2 = min(tp2_candidates) if tp2_candidates else tp2_floor
    else:
        sl = price + atr_val * SL_ATR
        below = [s for s in structure["support"] if s <= entry - risk * MIN_TP1_R]
        tp1 = max(below) if below else entry - risk * MIN_TP1_R
        tp2_ceiling = min(tp1 - risk * 0.5, entry - risk * MIN_TP2_R)
        tp2_candidates = [c for c in (ext_1618, price - risk * 4) if c <= tp2_ceiling]
        tp2 = max(tp2_candidates) if tp2_candidates else tp2_ceiling

    expected_r = round(abs(tp2 - entry) / risk, 2)
    return {
        "entry": round_price(entry), "sl": round_price(sl),
        "signal_price": round_price(price),
        "tp1": round_price(tp1), "tp2": round_price(tp2),
        "expected_r": expected_r,
    }
