# -*- coding: utf-8 -*-
"""core.relative - verdictul analistului pe token, partea masurata pe token (NUCLEU, fara I/O).

O SINGURA FUNCTIE PENTRU TREI LOCURI
------------------------------------
Pasii 1 si 4 ai Web3 Researcher, aplicati unui token, se reduc la trei masuratori: forta
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


# ------------------------------------------------------------------ istoricul verdictului
# CAT VALOREAZA VERDICTUL, masurat pe tot istoricul zilnic al bursei (depozitul de memorie), nu doar pe
# zilele de la pornirea jurnalului live de verdicte: pentru fiecare token lichid si fiecare zi,
# verdictul din inchiderile de pana atunci (aceeasi functie `verdict`) si randamentul URMATOR fata de
# BTC la 7 si 30 de zile. Randamentele pe h zile din zile consecutive se suprapun, deci intervalul de
# incredere al diferentei Bullish - Bearish se calculeaza pe blocuri de h zile, nu pe observatii.
TRACK_HORIZONS = (7, 30)
TRACK_MIN_QV = 1e6           # mediana volumului zilnic in USDT pe 30 de zile (tokeni tranzactionabili)
TRACK_WINDOW = 220           # inchiderile trimise lui `verdict` (MA200 + marja)
SENTIMENTS = ("Bullish", "Neutru", "Bearish")


def _day(ts_ms):
    import time as _t
    return _t.strftime("%Y-%m-%d", _t.gmtime(ts_ms / 1000.0))


def track_record(series, btc, horizons=TRACK_HORIZONS, min_qv=TRACK_MIN_QV, boot=1000, seed=1, exclude=()):
    """`series`: {simbol: [[ts, o, h, l, c, v], ...]} (zile inchise, crescator); `btc`: la fel, pentru BTC.
    Intoarce {"tokens", "obs", "from", "to", "min_qv", "horizons": {h: {sentiment: {n, mean, median, beat},
    "spread": {mean, ci_low, ci_high, days}, "by_year": {an: diferenta medie}}}} sau None fara date.
    `beat` = cat din cazuri tokenul a batut BTC pe urmatoarele h zile (randament relativ > 0)."""
    import random
    bmap = {int(c[0]): c[4] for c in btc or [] if c and c[4]}
    if not bmap:
        return None
    by_day = {}
    tokens, first, last = set(), None, None
    for sym, rows in (series or {}).items():
        if sym in exclude:
            continue
        rows = [r for r in rows if int(r[0]) in bmap and r[4]]
        ts = [int(r[0]) for r in rows]
        cl = [r[4] for r in rows]
        qv = [(r[5] or 0) * r[4] for r in rows]
        bc = [bmap[t] for t in ts]
        for i in range(MA_MIN_DAYS, len(rows)):
            q = sorted(qv[max(0, i - 29):i + 1])
            if q[len(q) // 2] < min_qv:
                continue
            lo = max(0, i - TRACK_WINDOW)
            vd = verdict(cl[lo:i + 1], bc[lo:i + 1])
            if not vd:
                continue
            fwd = {}
            for h in horizons:
                j = i + h
                if j < len(rows) and ts[j] - ts[i] == h * 86400000:
                    fwd[h] = ((cl[j] / cl[i]) / (bc[j] / bc[i]) - 1) * 100
            if not fwd:
                continue
            tokens.add(sym)
            first = ts[i] if first is None else min(first, ts[i])
            last = ts[i] if last is None else max(last, ts[i])
            by_day.setdefault(ts[i], {}).setdefault(vd["sentiment"], []).append(fwd)
    if not by_day:
        return None
    out = {"tokens": len(tokens), "obs": sum(len(v) for d in by_day.values() for v in d.values()),
           "from": _day(first), "to": _day(last), "min_qv": min_qv, "horizons": {}}
    rnd = random.Random(seed)
    for h in horizons:
        allv = {s: [] for s in SENTIMENTS}
        spreads = []
        for t in sorted(by_day):
            means = {}
            for s, lst in by_day[t].items():
                xs = [f[h] for f in lst if h in f]
                allv[s] += xs
                if len(xs) >= 3:
                    means[s] = sum(xs) / len(xs)
            if "Bullish" in means and "Bearish" in means:
                spreads.append((t, means["Bullish"] - means["Bearish"]))
        res = {}
        for s, xs in allv.items():
            if xs:
                xs.sort()
                res[s] = {"n": len(xs), "mean": round(sum(xs) / len(xs), 2), "median": round(xs[len(xs) // 2], 2),
                          "beat": round(100.0 * sum(1 for x in xs if x > 0) / len(xs), 1)}
        if len(spreads) >= 2 * h:
            sp = [x for _, x in spreads]
            blocks = [sp[k:k + h] for k in range(0, len(sp), h)]
            means = []
            for _ in range(boot):
                flat = [x for _b in blocks for x in blocks[rnd.randrange(len(blocks))]]
                means.append(sum(flat) / len(flat))
            means.sort()
            res["spread"] = {"mean": round(sum(sp) / len(sp), 2), "ci_low": round(means[int(0.025 * boot)], 2),
                             "ci_high": round(means[int(0.975 * boot) - 1], 2), "days": len(sp)}
            years = {}
            for t, x in spreads:
                years.setdefault(_day(t)[:4], []).append(x)
            res["by_year"] = {y: round(sum(v) / len(v), 2) for y, v in sorted(years.items())}
        out["horizons"][str(h)] = res
    return out
