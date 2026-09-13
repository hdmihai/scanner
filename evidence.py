#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
evidence.py
============
Transforma indicatorii bruti in EVIDENTE: fapte numite, cu directie si
intensitate, care pot fi si citite de om si folosite ca intrari de model.

DE CE ACEST STRAT
-----------------
Pana acum existau doua lumi separate: agentul invata din sase numere abstracte
(trend, momentum, volatility, volume), iar dashboard-ul afisa indicatori pe
care agentul nu-i vedea niciodata. Nimic nu lega ce invata de ce arata.

Aici ele devin acelasi obiect. O evidenta are eticheta in limbaj natural
("EMA 9 este peste EMA 20"), o directie (LONG / SHORT / NEUTRU) si o
intensitate 0..1. Lista completa se salveaza pe plan: dashboard-ul o afiseaza
ca atare, iar agentul o consuma ca vector de caracteristici.

ORIENTAREA DUPA DIRECTIE - detaliul care conteaza
-------------------------------------------------
Caracteristicile se orienteaza dupa directia planului: o evidenta care sustine
directia da +1, una care o contrazice da -1, indiferent daca planul e LONG sau
SHORT. Fara asta, modelul invata reguli separate pentru fiecare directie si
imparte datele in doua. Masurat pe proiectul asta: greutatea `is_long` ajunsese
-1.93, dominand tot restul - modelul memorase "shorturile castiga", ceea ce era
regimul de piata din acea fereastra, nu o relatie reala.

CE NU E AICI, SI DE CE
----------------------
Order flow si footprint delta din sistemul de referinta cer date de tick sau
order book istoric. OKX nu ofera order book istoric, deci nu pot fi calculate
in backtest - iar o evidenta care exista doar live si lipseste in backtest ar
face cele doua incomparabile. Prefer sa lipseasca din amandoua.
"""

DIR_LONG = "LONG"
DIR_SHORT = "SHORT"
DIR_NEUTRAL = "NEUTRU"


def _clip(v, lo=-1.0, hi=1.0):
    return max(lo, min(hi, v))


def _item(key, label, direction, strength, value=None):
    return {"key": key, "label": label, "direction": direction,
            "strength": round(max(0.0, min(1.0, strength)), 4),
            "value": value}


def build_evidence(ind, price, atr, rsi=None, components=None):
    """Construieste lista de evidente dintr-un set de indicatori.

    `ind` e iesirea lui indicators.compute_all(). Orice lipseste se sare -
    pe simboluri cu istoric scurt unii indicatori nu se pot calcula, si e mai
    bine sa aiba mai putine evidente decat sa inventez valori.
    """
    ev = []
    if not ind or not price or price <= 0:
        return ev
    atr = atr or (price * 0.01)

    emas = ind.get("emas") or {}
    e9, e20, e50 = emas.get("ema9"), emas.get("ema20"), emas.get("ema50")
    e100, e200 = emas.get("ema100"), emas.get("ema200")

    if e9 and e20:
        up = e9 > e20
        gap = abs(e9 - e20) / atr
        ev.append(_item("ema_fast", f"EMA 9 este {'peste' if up else 'sub'} EMA 20",
                        DIR_LONG if up else DIR_SHORT, _clip(gap / 2, 0, 1), round(gap, 3)))

    stack = [x for x in (e20, e50, e100, e200) if x]
    if len(stack) >= 3:
        asc = sum(1 for a, b in zip(stack, stack[1:]) if a > b)
        desc = sum(1 for a, b in zip(stack, stack[1:]) if a < b)
        total = len(stack) - 1
        if asc > desc:
            ev.append(_item("ema_stack", "Structura EMA medie/lunga e ascendenta",
                            DIR_LONG, asc / total, f"{asc}/{total}"))
        elif desc > asc:
            ev.append(_item("ema_stack", "Structura EMA medie/lunga e descendenta",
                            DIR_SHORT, desc / total, f"{desc}/{total}"))

    st = ind.get("supertrend") or {}
    if st.get("direction"):
        bull = st["direction"] == "BULLISH"
        dist = abs(price - (st.get("level") or price)) / atr
        ev.append(_item("supertrend", f"SuperTrend confirma structura "
                                      f"{'ascendenta' if bull else 'descendenta'}",
                        DIR_LONG if bull else DIR_SHORT, _clip(dist / 3, 0, 1),
                        st.get("level")))

    macd = ind.get("macd") or {}
    if macd.get("histogram") is not None:
        h = macd["histogram"]
        ev.append(_item("macd", f"Histograma MACD e {'pozitiva' if h > 0 else 'negativa'}",
                        DIR_LONG if h > 0 else DIR_SHORT,
                        _clip(abs(h) / (atr * 0.5), 0, 1), round(h, 8)))

    vwap = ind.get("vwap")
    if vwap:
        above = price > vwap
        d = abs(price - vwap) / atr
        ev.append(_item("vwap", f"Pretul e {'peste' if above else 'sub'} VWAP",
                        DIR_LONG if above else DIR_SHORT, _clip(d / 2, 0, 1), round(vwap, 8)))

    vp = ind.get("volume_profile") or {}
    if vp.get("poc"):
        above = price > vp["poc"]
        d = abs(price - vp["poc"]) / atr
        ev.append(_item("poc", f"Pretul e {'peste' if above else 'sub'} POC",
                        DIR_LONG if above else DIR_SHORT, _clip(d / 3, 0, 1), vp["poc"]))
    pos = ind.get("price_vs_value_area")
    if pos == "PESTE VAH":
        ev.append(_item("value_area", "Pretul e acceptat peste value area", DIR_LONG, 0.7, pos))
    elif pos == "SUB VAL":
        ev.append(_item("value_area", "Pretul e acceptat sub value area", DIR_SHORT, 0.7, pos))
    elif pos:
        ev.append(_item("value_area", "Pretul e in interiorul value area", DIR_NEUTRAL, 0.3, pos))

    if rsi is not None:
        if rsi >= 70:
            ev.append(_item("rsi", f"RSI supracumparat ({rsi:.0f})", DIR_SHORT,
                            _clip((rsi - 70) / 20, 0, 1), round(rsi, 1)))
        elif rsi <= 30:
            ev.append(_item("rsi", f"RSI supravandut ({rsi:.0f})", DIR_LONG,
                            _clip((30 - rsi) / 20, 0, 1), round(rsi, 1)))
        else:
            up = rsi > 50
            ev.append(_item("rsi", f"RSI in zona neutra, inclinat {'in sus' if up else 'in jos'} ({rsi:.0f})",
                            DIR_LONG if up else DIR_SHORT, abs(rsi - 50) / 20, round(rsi, 1)))

    comp = components or {}
    if comp.get("volume") is not None:
        v = comp["volume"]
        ev.append(_item("volume", f"Volumul e {'peste' if v > 0.5 else 'sub'} media recenta",
                        DIR_NEUTRAL, abs(v - 0.5) * 2, round(v, 3)))
    if comp.get("volatility") is not None:
        ev.append(_item("volatility", "Volatilitatea e in intervalul favorabil"
                        if comp["volatility"] > 0.5 else "Volatilitatea e in afara intervalului favorabil",
                        DIR_NEUTRAL, abs(comp["volatility"] - 0.5) * 2, round(comp["volatility"], 3)))
    return ev


def fusion(evidence, direction):
    """Numara evidentele care sustin si care contrazic directia planului.
    Echivalentul panoului 'EVIDENCE FUSION' din sistemul de referinta, dar cu
    numere care chiar provin din ce s-a calculat."""
    support = [e for e in evidence if e["direction"] == direction]
    oppose = [e for e in evidence if e["direction"] not in (direction, DIR_NEUTRAL)]
    neutral = [e for e in evidence if e["direction"] == DIR_NEUTRAL]
    s = sum(e["strength"] for e in support)
    o = sum(e["strength"] for e in oppose)
    total = s + o
    return {
        "support": len(support), "oppose": len(oppose), "neutral": len(neutral),
        "support_weight": round(s, 3), "oppose_weight": round(o, 3),
        "score": round(100 * s / total, 1) if total > 0 else None,
    }


# Ordinea e fixa: vectorul de caracteristici trebuie sa aiba mereu aceeasi
# forma, altfel greutatile invatate nu mai corespund aceleiasi evidente.
EVIDENCE_KEYS = ["ema_fast", "ema_stack", "supertrend", "macd", "vwap",
                 "poc", "value_area", "rsi", "volume", "volatility"]


def evidence_features(evidence, direction):
    """Vector numeric pentru model, ORIENTAT dupa directia planului.

    +strength daca evidenta sustine directia, -strength daca o contrazice,
    0 daca e neutra sau lipseste. Asa modelul invata "evidenta aliniata ajuta",
    nu reguli separate pentru LONG si SHORT.
    """
    by_key = {e["key"]: e for e in evidence}
    feats = {}
    for k in EVIDENCE_KEYS:
        e = by_key.get(k)
        if not e:
            feats[f"ev_{k}"] = 0.0
        elif e["direction"] == direction:
            feats[f"ev_{k}"] = e["strength"]
        elif e["direction"] == DIR_NEUTRAL:
            feats[f"ev_{k}"] = 0.0
        else:
            feats[f"ev_{k}"] = -e["strength"]
    f = fusion(evidence, direction)
    feats["ev_fusion"] = ((f["score"] or 50.0) - 50.0) / 50.0
    return feats


FEATURE_KEYS = [f"ev_{k}" for k in EVIDENCE_KEYS] + ["ev_fusion"]
