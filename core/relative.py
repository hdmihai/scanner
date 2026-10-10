# -*- coding: utf-8 -*-
"""core.relative - verdictul analistului pe token, partea masurata pe token (NUCLEU, fara I/O).

O SINGURA FUNCTIE PENTRU TREI LOCURI
------------------------------------
Pasii 1 si 4 ai Analistului Web3, aplicati unui token, se reduc la trei masuratori: forta
relativa fata de BTC pe 7 si pe 30 de zile si pozitia pretului fata de media de 200 de zile.
Din ele iese sentimentul tokenului:

  Bearish - sub MA200 1D si pierde fata de BTC pe 30 de zile;
  Bullish - peste MA200 1D si bate BTC pe 7 SI pe 30 de zile;
  Neutru  - altfel.

Aceeasi functie, pe aceleasi date (inchideri zilnice ale bursei, aceleasi zile inchise pentru
token si pentru BTC), e folosita de:
  - cardul analistului (core/analyst.token_report) - ce vede omul;
  - evidenta `analyst` a planurilor live (core/scan -> core/evidence) - ce vede agentul;
  - backtest-ul (backtest.replay_symbol, din lumanarile de 4h agregate pe zile) - pe ce invata.
Daca fiecare loc si-ar calcula verdictul separat, agentul ar invata pe o definitie si ar decide
pe alta - exact decalajul antrenare-utilizare pe care proiectul il masoara la fiecare rulare.

Partea de piata a pasului 4 (regimul bear / BTC sub MA200 fac tot Bearish) NU intra aici: agentul
o vede deja prin evidentele `altseason` si `regime`. Repetata, ar fi aceeasi informatie de doua
ori, cu doua greutati.
"""

MA_DAYS = 200
MA_MIN_DAYS = 150        # aceeasi regula ca media de 200 de zile din contextul ciclului BTC
RS_DAYS = (7, 30)


def sma(closes, n=MA_DAYS, min_n=MA_MIN_DAYS):
    """Media ultimelor `n` inchideri; pe istoric mai scurt, media tuturor daca sunt >= `min_n`."""
    k = len(closes or [])
    if k >= n:
        return sum(closes[-n:]) / n
    return sum(closes) / k if (min_n and k >= min_n) else None


def rs(closes, btc_closes, n):
    """Forta relativa fata de BTC pe ultimele `n` zile inchise, in procente:
    (1 + randament token) / (1 + randament BTC) - 1. Ambele serii se termina in aceeasi zi."""
    if not closes or not btc_closes or len(closes) <= n or len(btc_closes) <= n:
        return None
    a0, b0 = closes[-1 - n], btc_closes[-1 - n]
    if not a0 or not b0:
        return None
    return ((closes[-1] / a0) / (btc_closes[-1] / b0) - 1) * 100


def verdict(closes, btc_closes, price=None):
    """{rs7, rs30, ma200, above_ma200, sentiment} sau None daca lipsesc datele.

    `closes`: inchiderile zilnice ale tokenului (zile inchise, cea mai noua la final);
    `btc_closes`: inchiderile zilnice BTC pentru ACELEASI zile (aceeasi ultima zi);
    `price`: pretul curent (implicit ultima inchidere) - fata de el se judeca MA200."""
    if not closes or not btc_closes:
        return None
    r7, r30 = rs(closes, btc_closes, 7), rs(closes, btc_closes, 30)
    ma = sma(closes)
    px = price or closes[-1]
    above = (px > ma) if (px and ma) else None
    if r30 is None or above is None:
        return None
    if above is False and r30 < 0:
        sent = "Bearish"
    elif above is True and r7 is not None and r7 > 0 and r30 > 0:
        sent = "Bullish"
    else:
        sent = "Neutru"
    return {"rs7": None if r7 is None else round(r7, 2), "rs30": round(r30, 2),
            "ma200": ma, "above_ma200": above, "sentiment": sent}


def from_candles(candles, btc_candles, price=None):
    """`verdict` din lumanari zilnice [ts, o, h, l, c, v]: doar daca ambele serii se termina in
    ACEEASI zi (altfel randamentele ar compara perioade diferite)."""
    if not candles or not btc_candles or candles[-1][0] != btc_candles[-1][0]:
        return None
    return verdict([c[4] for c in candles], [c[4] for c in btc_candles], price)
