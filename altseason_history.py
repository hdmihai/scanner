#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
altseason_history.py
=====================
Istoricul altseason pe ultimii 10 ani: ciclurile pietei, pozitia de ACUM fata
de istoric si invatarea zilnica a agentului.

SURSA ISTORICA (gratuita, pe GitHub): Coin Metrics community data
(github.com/coinmetrics/data, licenta CC BY-NC 4.0): serii zilnice de pret si
capitalizare, BTC din 2010. Descarcata doar la construirea istoricului (prima
rulare si saptamanal); apoi istoricul continua din lumanarile zilnice ale bursei
pe care scanerul le foloseste deja.

CE SE POATE RECONSTRUI CORECT, VERIFICAT PE EPISOADE CUNOSCUTE
--------------------------------------------------------------
- indicele altseason istoric: % din altcoins cu istoric care bat BTC pe 90 de
  zile. Verificat: ian. 2018 = 82, apr. 2021 = 95, dec. 2024 = 89 (altseason
  reale), sep. 2019 = 10, iun. 2023 = 4, iun. 2025 = 13 (Bitcoin season).
  Universul e cel al activelor cu istoric (~45), nu top 100 de azi: nivelul
  difera de indicele CoinMarketCap, dar ciclurile coincid.
- ETH/BTC, randamentele si distanta BTC fata de maximul istoric: exacte.
- dominanta BTC: doar DIRECTIA. Nivelul reconstruit e supraestimat cu 5-8 pp din
  2024 (SOL, TRX, TON nu au capitalizare in sursa), deci clasificarea istorica nu
  foloseste nivelul; nivelul real vine live din CoinGecko.

CE PRODUCE
----------
- linia de timp zilnica 2016 -> ieri, cu faza clasificata (acelasi clasificator
  ca live), ciclurile BTC (varf -> minim -> varf) si ferestrele altseason
- pozitia de acum: zile de la ultimul varf si minim, comparat cu ciclurile trecute
- analogii istorice (cele mai apropiate momente) si ce a urmat dupa ele
- probabilitati din tranzitiile istorice intre faze
- INVATARE: predictiile se inregistreaza zilnic si se puncteaza cand orizontul
  expira; ponderile celor doi predictori (analogii vs tranzitii) urmeaza
  scorurile lor. La construire, predictorii sunt evaluati walk-forward pe tot
  istoricul, deci pornesc cu o masura reala a acuratetii.
"""

import csv
import io
import json
import math
import os
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone

import altseason as A

VERSION = 2          # 2: decizia in doua straturi (regimul ciclului BTC) - reconstruire
# NU altseason_history.json: acela e istoricul ORAR al evaluarii live (altseason.py)
STATE_FILE = os.path.join("data", "altseason_cycles.json")
CM_RAW = "https://raw.githubusercontent.com/coinmetrics/data/master/csv/{}.csv"
CM_LOCAL_DIR = os.environ.get("CM_LOCAL_DIR")          # doar pentru teste offline
START = "2016-01-01"
REBUILD_DAYS = 7
WINDOW = 400               # zile pastrate per activ, pentru calculul zilelor noi
ALT_SEASON = 75            # pragul CoinMarketCap
STABLE = {"usdt", "usdc", "busd", "dai", "tusd", "usdp", "fdusd", "usde", "pyusd", "usds", "gusd", "pax"}

# Activele care au contat istoric in top 100 si exista in Coin Metrics.
CM_ASSETS = """btc eth xrp bnb sol ada doge trx ton avax shib dot link bch ltc near pol uni icp etc apt xlm fil hbar
atom vet arb op imx mnt okb cro inj rndr render grt stx algo xmr qnt aave mkr sand mana axs chz eos xtz theta ftm egld
flow neo miota xem zec dash bsv waves omg zrx bat enj lsk qtum icx ont zil nano dgb sc steem rep glm ht leo ftt luna
kava rune gala ape gmt ksm celo ar ens lrc 1inch ankr bal band ocean rsr storj zen crv snx comp yfi sushi ldo sei sui
tia pepe wif bonk fet kas wld pyth jup ena ondo strk hype trump xdc mina bgb nexo gt kcs tfuel rvn dcr xvg ardr ark bnt
pax usdt usdc busd dai tusd usdp fdusd usde pyusd usds gusd""".split()


# ---------------------------------------------------------------------------
def _d(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def _s(d):
    return d.strftime("%Y-%m-%d")


def _num(x):
    try:
        v = float(x)
        return v if v > 0 and math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _fetch_cm(asset):
    if CM_LOCAL_DIR:
        p = os.path.join(CM_LOCAL_DIR, f"{asset}.csv")
        return open(p).read() if os.path.exists(p) else None
    try:
        with urllib.request.urlopen(CM_RAW.format(asset), timeout=90) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:
        return None


def load_cm(assets=CM_ASSETS):
    """{activ: {zi: (pret, capitalizare)}} din Coin Metrics, doar coloanele necesare."""
    px, cap = {}, {}
    for a in dict.fromkeys(assets):
        txt = _fetch_cm(a)
        if not txt:
            continue
        p, c = {}, {}
        for r in csv.DictReader(io.StringIO(txt)):
            t = r.get("time")
            if not t or t < "2015-06-01":
                continue
            v = _num(r.get("PriceUSD")) or _num(r.get("ReferenceRateUSD"))
            if v:
                p[t] = v
            k = _num(r.get("CapMrktCurUSD"))
            if k:
                c[t] = k
        if p:
            px[a] = p
        if c:
            cap[a] = c
    return px, cap


# ---------------------------------------------------------------------------
def _median(v):
    v = sorted(x for x in v if x is not None)
    if not v:
        return None
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2


def day_features(day, px, cap, ath):
    """Indicatorii unei zile, in formatul clasificatorului live (altseason.classify).
    `px`/`cap`: {activ: {zi: valoare}}; `ath`: maximul BTC pana in acea zi."""
    d0 = _d(day)
    b = px.get("btc", {})
    ago = lambda n: _s(d0 - timedelta(days=n))
    bt, b30, b90, b200 = b.get(day), b.get(ago(30)), b.get(ago(90)), b.get(ago(200))
    if not bt or not b90:
        return None
    rb30 = (bt / b30 - 1) * 100 if b30 else None
    rb90 = (bt / b90 - 1) * 100
    beat90, beat30, rel30 = [], [], {}
    for a, s in px.items():
        if a == "btc" or a in STABLE:
            continue
        v, v90, v30 = s.get(day), s.get(ago(90)), s.get(ago(30))
        if v and v90:
            beat90.append((v / v90 - 1) * 100 > rb90)
        if v and v30 and rb30 is not None:
            r = (v / v30 - 1) * 100
            beat30.append(r > rb30)
            rel30[a] = ((1 + r / 100) / (1 + rb30 / 100) - 1) * 100
    if len(beat90) < 10:
        return None
    # niveluri dupa capitalizare (doar activele cu capitalizare)
    caps = sorted(((cap[a][day], a) for a in cap if a != "btc" and a not in STABLE
                   and day in cap[a] and a in rel30), reverse=True)
    ranked = [a for _, a in caps]
    tiers = {"large": ranked[:10], "mid": ranked[10:25], "small": ranked[25:]}
    small = tiers["small"]
    spec = (sum(1 for a in small if rel30.get(a, -999) > 50) / len(small)) if small else None
    # dominanta proxy: doar pentru DIRECTIE
    tot = sum(cap[a][day] for a in cap if day in cap[a])
    dom = 100 * cap["btc"][day] / tot if tot and day in cap.get("btc", {}) else None
    eth, eth30 = px.get("eth", {}).get(day), px.get("eth", {}).get(ago(30))
    eb30 = (((eth / eth30) / (bt / b30) - 1) * 100) if (eth and eth30 and b30) else None
    return {
        "day": day, "btc": bt, "dom_proxy": dom, "eth_btc": (eth / bt) if eth else None,
        "alt_index_90d": round(100 * sum(beat90) / len(beat90), 1), "n": len(beat90),
        "breadth30": round(100 * sum(beat30) / len(beat30), 1) if beat30 else None,
        "med_rel30_tier": {t: _median([rel30.get(a) for a in g]) for t, g in tiers.items()},
        "breadth30_tier": {t: (round(100 * sum(1 for a in g if rel30.get(a, -1) > 0) / len(g), 1) if g else None)
                           for t, g in tiers.items()},
        "spec_share": spec, "eth_btc_30d": eb30, "btc_r30": rb30, "btc_r90": rb90,
        "btc_r200": (bt / b200 - 1) * 100 if b200 else None,
        "btc_ath_dd": (bt / ath - 1) * 100 if ath else None,
    }


ROW_KEYS = ["day", "btc", "dom_proxy", "eth_btc", "ai", "n", "br30", "ml", "mm", "ms", "spec", "dd", "r30",
            "r90", "r200", "eb30", "dom_d30", "phase", "conf"]


def _row(f, dom_d30, cls):
    r = lambda v, k=4: None if v is None else round(v, k)
    m = f["med_rel30_tier"]
    return [f["day"], r(f["btc"], 2), r(f["dom_proxy"], 2), r(f["eth_btc"], 6), f["alt_index_90d"], f["n"],
            f["breadth30"], r(m.get("large"), 2), r(m.get("mid"), 2), r(m.get("small"), 2), r(f["spec_share"], 3),
            r(f["btc_ath_dd"], 2), r(f["btc_r30"], 2), r(f["btc_r90"], 2), r(f["btc_r200"], 2),
            r(f["eth_btc_30d"], 2), r(dom_d30, 2), cls["phase"], cls["confidence"]]


def R(row, k):
    return row[ROW_KEYS.index(k)]


def _ctx(price, ath, ath_day, low, low_day, day, closes):
    """Contextul ciclului BTC pentru o zi: aceleasi campuri ca live."""
    ma = sum(closes[-200:]) / len(closes[-200:]) if len(closes) >= 150 else None
    return {"dd": (price / ath - 1) * 100, "days_since_ath": (_d(day) - _d(ath_day)).days, "ath": ath,
            "ath_day": ath_day, "low": low, "low_day": low_day,
            "low_dd": (low / ath - 1) * 100 if low else None,
            "rec_from_low": (price / low - 1) * 100 if low else None,
            "ma200": ma, "above_ma200": (price > ma) if ma else None}


def classify_day(f, dom_d30, hist_ai, prev, ctx=None):
    ind = {"btc_d": None,                    # nivelul istoric nu e de incredere
           "btc_d_delta30": dom_d30, "eth_btc_30d": f["eth_btc_30d"], "btc_r30": f["btc_r30"],
           "btc_r200": f["btc_r200"], "btc_ath_dd": f["btc_ath_dd"], "alt_index_90d": f["alt_index_90d"],
           "breadth30": f["breadth30"], "breadth30_tier": f["breadth30_tier"],
           "med_rel30_tier": f["med_rel30_tier"], "spec_share": f["spec_share"], "alt_vol_share": None}
    # ACEEASI FEREASTRA CA LIVE: acolo istoricul are o intrare pe ora, iar cele
    # 720 de intrari folosite pentru "deteriorare fata de maximul recent" inseamna
    # 30 de zile. Pe seria zilnica, 720 de intrari ar fi insemnat 2 ani, iar faza 8
    # (distributie) aparea in peste jumatate din istoric.
    return A.classify(ind, [{"ai": x} for x in hist_ai[-30:]], prev, cycle=ctx)


def build_timeline(px, cap, start=START, end=None):
    days = sorted(px.get("btc", {}))
    end = end or days[-1]
    ath, rows, prev, hist_ai, doms = 0.0, [], None, [], {}
    ath_day, low, low_day, closes = None, None, None, []
    for day in days:
        p = px["btc"][day]
        if day > end:
            break
        closes.append(p)
        if p >= ath:
            ath, ath_day, low, low_day = p, day, None, None
        elif low is None or p < low:
            low, low_day = p, day
        if day < start:
            continue
        f = day_features(day, px, cap, ath)
        if not f:
            continue
        if f["dom_proxy"] is None and doms:              # o zi fara capitalizare: ultima valoare
            f["dom_proxy"] = doms[max(doms)]
        doms[day] = f["dom_proxy"]
        d30 = doms.get(_s(_d(day) - timedelta(days=30)))
        dom_d30 = (f["dom_proxy"] - d30) if (f["dom_proxy"] is not None and d30 is not None) else None
        cls = classify_day(f, dom_d30, hist_ai, prev, _ctx(p, ath, ath_day, low, low_day, day, closes))
        prev = cls["scores"]
        hist_ai.append(f["alt_index_90d"])
        rows.append(_row(f, dom_d30, cls))
    return rows, {"ath": ath, "ath_day": ath_day, "low": low, "low_day": low_day}


# ---------------------------------------------------------------------------
def cycles(rows, btc_full=None, min_dd=45.0):
    """Ciclurile BTC: varf -> minim (scadere >= min_dd%) -> varf, si ferestrele
    altseason (indice >= 75 cel putin 7 zile) din fiecare ciclu."""
    series = btc_full or [(R(r, "day"), R(r, "btc")) for r in rows]
    tops, lows = [], []
    ath_d, ath, low_d, low, in_bear = series[0][0], series[0][1], None, None, False
    for d, p in series:
        if p >= ath:
            if in_bear and low_d:
                lows.append((low_d, low))
                in_bear = False
            ath_d, ath = d, p
            low_d, low = None, None
        else:
            if low is None or p < low:
                low_d, low = d, p
            if (1 - p / ath) * 100 >= min_dd and not in_bear:
                in_bear = True
                tops.append((ath_d, ath))
    if in_bear and low_d:
        lows.append((low_d, low))            # minimul ciclului in curs (provizoriu)
    wins, cur = [], None
    for r in rows:
        if R(r, "ai") >= ALT_SEASON:
            cur = cur or {"start": R(r, "day"), "peak": 0}
            cur["end"] = R(r, "day")
            if R(r, "ai") > cur["peak"]:
                cur["peak"], cur["peak_day"] = R(r, "ai"), R(r, "day")
        elif cur:
            if (_d(cur["end"]) - _d(cur["start"])).days >= 6:
                wins.append(cur)
            cur = None
    if cur and (_d(cur["end"]) - _d(cur["start"])).days >= 6:
        wins.append(cur)
    out = []
    for i, (td, tp) in enumerate(tops):
        ld, lp = next(((d, p) for d, p in lows if d > td), (None, None))
        nxt = tops[i + 1] if i + 1 < len(tops) else None
        c = {"top": td, "top_price": round(tp, 2), "low": ld, "low_price": round(lp, 2) if lp else None,
             "drawdown": round((lp / tp - 1) * 100, 1) if lp else None,
             "days_top_to_low": (_d(ld) - _d(td)).days if ld else None,
             "next_top": nxt[0] if nxt else None}
        c["altseasons"] = [w for w in wins if ld and w["start"] > ld and (not nxt or w["start"] <= nxt[0])]
        c["altseasons_before_top"] = [w for w in wins if w["end"] <= td and
                                      (i == 0 or w["start"] > (out[-1]["low"] or ""))]
        out.append(c)
    return out, wins


# ---------------------------------------------------------------------------
FEATS = ["dd", "r200", "eb30", "ai", "dom_d30", "br30"]


def _zstats(rows):
    st = {}
    for k in FEATS:
        v = [R(r, k) for r in rows if R(r, k) is not None]
        m = sum(v) / len(v)
        sd = math.sqrt(sum((x - m) ** 2 for x in v) / max(1, len(v) - 1)) or 1.0
        st[k] = (m, sd)
    return st


def analogs(rows, cur, upto=None, k=8, gap=60, st=None):
    """Cele mai apropiate momente istorice de `cur` (distanta standardizata),
    separate de cel putin `gap` zile, doar dinainte de `upto`."""
    st = st or _zstats(rows)
    lim = upto or len(rows)
    cand = []
    for i in range(lim):
        r = rows[i]
        if any(R(r, f) is None for f in FEATS) or any(cur.get(f) is None for f in FEATS):
            continue
        d = math.sqrt(sum(((R(r, f) - cur[f]) / st[f][1]) ** 2 for f in FEATS))
        cand.append((d, i))
    cand.sort()
    picked = []
    for d, i in cand:
        if all(abs(i - j) >= gap for _, j in picked):
            picked.append((d, i))
        if len(picked) >= k:
            break
    return picked


def outcome(rows, i, h=90):
    """Ce a urmat dupa ziua i: indicele a atins pragul altseason in h zile?"""
    fut = rows[i + 1:i + 1 + h]
    if len(fut) < h:
        return None
    return {"alt90": any(R(r, "ai") >= ALT_SEASON for r in fut),
            "ai_change90": round(R(fut[-1], "ai") - R(rows[i], "ai"), 1),
            "phase30": R(rows[min(i + 30, len(rows) - 1)], "phase"),
            "btc90": round((R(fut[-1], "btc") / R(rows[i], "btc") - 1) * 100, 1)}


def transition_stats(rows, upto=None, h=90):
    """P(altseason in h zile | faza) si P(faza peste 30 de zile | faza), din istoric."""
    lim = (upto or len(rows)) - h
    alt, n, trans = {}, {}, {}
    for i in range(max(0, lim)):
        p = R(rows[i], "phase")
        n[p] = n.get(p, 0) + 1
        if any(R(r, "ai") >= ALT_SEASON for r in rows[i + 1:i + 1 + h]):
            alt[p] = alt.get(p, 0) + 1
        q = R(rows[i + 30], "phase")
        trans.setdefault(p, {})[q] = trans.setdefault(p, {}).get(q, 0) + 1
    return ({p: alt.get(p, 0) / n[p] for p in n}, n,
            {p: {q: c / sum(t.values()) for q, c in t.items()} for p, t in trans.items()})


def _cur(r):
    return {f: R(r, f) for f in FEATS}


def predict(rows, i, st=None):
    """Probabilitatea ca indicele sa atinga 75 in 90 de zile, dupa ziua i, din
    doua surse independente, folosind DOAR datele de dinaintea zilei i."""
    an = analogs(rows, _cur(rows[i]), upto=max(0, i - 90), st=st)
    outs = [outcome(rows, j) for _, j in an]
    outs = [o for o in outs if o]
    pa = (sum(o["alt90"] for o in outs) + 0.5) / (len(outs) + 1) if outs else None
    p_alt, n, _ = transition_stats(rows, upto=i)
    ph = R(rows[i], "phase")
    pt = ((p_alt.get(ph, 0) * n.get(ph, 0) + 0.5) / (n.get(ph, 0) + 1)) if n else None
    return pa, pt


def walk_forward(rows, step=7, h=90):
    """Evaluarea predictorilor pe TOT istoricul, fara privire in viitor. Referinta
    ("baza") e frecventa istorica a rezultatului, calculata doar din rezultatele
    deja cunoscute la acel moment - un predictor e util doar daca o bate."""
    st = _zstats(rows)
    err = {"analog": [], "trans": [], "base": []}
    ys = []                                      # (index, rezultat) cunoscute
    for i in range(400, len(rows) - h, step):
        known = [y for j, y in ys if j + h < i]
        pb = (sum(known) + 0.5) / (len(known) + 1) if known else None
        pa, pt = predict(rows, i, st)
        y = 1.0 if any(R(r, "ai") >= ALT_SEASON for r in rows[i + 1:i + 1 + h]) else 0.0
        for k, p in (("analog", pa), ("trans", pt), ("base", pb)):
            if p is not None:
                err[k].append((p - y) ** 2)
        ys.append((i, y))
    b = lambda v: round(sum(v) / len(v), 4) if v else None
    out = {"brier_analog": b(err["analog"]), "brier_trans": b(err["trans"]), "brier_base": b(err["base"]),
           "evaluations": len(ys), "step_days": step,
           "base_rate": round(sum(y for _, y in ys) / len(ys), 3) if ys else None}
    bb = out["brier_base"]
    out["skill_analog"] = round(1 - out["brier_analog"] / bb, 3) if bb and out["brier_analog"] else None
    out["skill_trans"] = round(1 - out["brier_trans"] / bb, 3) if bb and out["brier_trans"] else None
    return out


def weights(learn):
    """Ponderi pentru analogii, tranzitii si referinta (frecventa istorica),
    invers proportionale cu scorul Brier. Un predictor fara putere predictiva
    (mai slab decat referinta) primeste pondere mica, iar combinatia cade spre
    frecventa istorica - nu se afiseaza o incredere pe care datele n-o sustin."""
    b = {k: learn.get(f"brier_{k}") for k in ("analog", "trans", "base")}
    if not all(b.values()):
        return {"analog": 0.0, "trans": 0.0, "base": 1.0}
    inv = {k: (1 / v) ** 2 for k, v in b.items()}       # patratul accentueaza diferentele
    tot = sum(inv.values())
    return {k: round(v / tot, 3) for k, v in inv.items()}


# ---------------------------------------------------------------------------
def position(rows, cyc, live=None):
    """Pozitia de ACUM fata de istoric."""
    last = rows[-1]
    cur_cycle = cyc[-1] if cyc else None
    today = _d(R(last, "day"))
    pos = {"day": R(last, "day")}
    if cur_cycle:
        pos["days_since_top"] = (today - _d(cur_cycle["top"])).days
        if cur_cycle["low"]:
            pos["days_since_low"] = (today - _d(cur_cycle["low"])).days
            pos["up_from_low"] = round((R(last, "btc") / cur_cycle["low_price"] - 1) * 100, 1)
        past = [c for c in cyc[:-1] if c.get("low")]
        pos["past_cycles"] = []
        for c in past:
            ref = _d(c["low"]) + timedelta(days=pos.get("days_since_low", 0))
            rr = next((r for r in rows if R(r, "day") >= _s(ref)), None)
            if rr:
                pos["past_cycles"].append({"low": c["low"], "same_point": R(rr, "day"),
                                           "ai": R(rr, "ai"), "phase": R(rr, "phase"),
                                           "first_altseason_after_low": (c["altseasons"][0]["start"]
                                                                         if c["altseasons"] else None)})
    ai_hist = sorted(R(r, "ai") for r in rows)
    pos["ai_percentile"] = round(100 * sum(1 for x in ai_hist if x <= R(last, "ai")) / len(ai_hist))
    st = _zstats(rows)
    an = analogs(rows, _cur(last), upto=len(rows) - 90, st=st)
    pos["analogs"] = []
    for d, j in an:
        o = outcome(rows, j)
        if o:
            pos["analogs"].append({"day": R(rows[j], "day"), "distance": round(d, 2), "ai": R(rows[j], "ai"),
                                   "phase": R(rows[j], "phase"), **o})
    p_alt, n, trans = transition_stats(rows)
    ph = R(last, "phase")
    pos["p_alt90_by_phase"] = round(p_alt.get(ph, 0), 3)
    pos["phase_samples"] = n.get(ph, 0)
    pos["next30"] = sorted(((q, round(p, 3)) for q, p in (trans.get(ph) or {}).items()), key=lambda x: -x[1])[:3]
    # cat dureaza de obicei faza curenta, si cat a trecut din ea
    runs, run = {}, 0
    for i2 in range(1, len(rows)):
        if R(rows[i2], "phase") == R(rows[i2 - 1], "phase"):
            run += 1
        else:
            runs.setdefault(R(rows[i2 - 1], "phase"), []).append(run + 1)
            run = 0
    cur_run = 1
    for i2 in range(len(rows) - 1, 0, -1):
        if R(rows[i2 - 1], "phase") == ph:
            cur_run += 1
        else:
            break
    med = _median(runs.get(ph) or [])
    pos["phase_days"] = cur_run
    pos["phase_median_days"] = med
    return pos


# ---------------------------------------------------------------------------
def _exchange_daily(exchange, sym, since_day):
    """Lumanari zilnice inchise de la `since_day` incoace: {zi: inchidere}."""
    if exchange is None:
        return {}
    try:
        since = int(datetime.strptime(since_day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
        o = exchange.fetch_ohlcv(f"{sym.upper()}/USDT", timeframe="1d", since=since, limit=300)
    except Exception:
        return {}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = {}
    for c in o or []:
        d = datetime.fromtimestamp(c[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        if since_day <= d < today and c[4]:      # doar zile inchise, din intervalul cerut
            out[d] = float(c[4])
    return out


def _supply(cap, px, a):
    """Oferta ultimei zile cunoscute, pentru a prelungi capitalizarea."""
    c = cap.get(a) or {}
    if not c:
        return None
    d = max(c)
    p = (px.get(a) or {}).get(d)
    return c[d] / p if p else None


def extend(state, px, cap, exchange):
    """Prelungeste seriile de la ultima zi cunoscuta pana ieri, din bursa. Oferta
    se considera constanta de la ultima valoare cunoscuta (schimbari mici in luni)."""
    last = max(px.get("btc", {"": 0}))
    yesterday = _s(datetime.now(timezone.utc).date() - timedelta(days=1))
    if last >= yesterday or exchange is None:
        return 0
    start = _s(_d(last) + timedelta(days=1))
    added = 0
    markets = getattr(exchange, "markets", None) or {}
    for a in list(px):
        if a in STABLE:
            continue
        if markets and f"{a.upper()}/USDT" not in markets:
            continue
        new = _exchange_daily(exchange, a, start)
        if not new:
            continue
        # CONTINUITATE: un ticker cu acelasi nume poate fi ALT activ pe bursa (ex.
        # simboluri scurte ca GT sau ONE). Primul pret nou trebuie sa continue seria.
        prev = px[a][max(px[a])]
        first = new[min(new)]
        if not prev or not (0.4 <= first / prev <= 2.5):
            print(f"  istoric: {a.upper()} respins la prelungire ({prev:.6g} -> {first:.6g}) - alt activ sau date gresite")
            continue
        sup = _supply(cap, px, a)
        for d, v in new.items():
            px[a][d] = v
            if sup:
                cap.setdefault(a, {})[d] = v * sup
        added = max(added, len(new))
    for s in STABLE:                            # stablecoins: capitalizare constanta
        if s in cap and cap[s]:
            d0 = max(cap[s])
            for d in (px.get("btc") or {}):
                if d > d0:
                    cap[s][d] = cap[s][d0]
    return added


def _trim(px, cap):
    """Fereastra pastrata in fisier: ultimele WINDOW zile pentru fiecare activ."""
    last = max(px.get("btc", {"": 0}))
    cut = _s(_d(last) - timedelta(days=WINDOW))
    f = lambda s: {d: round(v, 8) for d, v in s.items() if d >= cut}
    return {a: f(s) for a, s in px.items() if any(d >= cut for d in s)}, \
           {a: {d: round(v, 0) for d, v in s.items() if d >= cut} for a, s in cap.items()}


def _load():
    try:
        with open(STATE_FILE) as fh:
            return json.load(fh)
    except Exception:
        return None


def _save(st):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(st, fh, separators=(",", ":"))
    os.replace(tmp, STATE_FILE)


def build(exchange=None):
    """Construieste istoricul complet: Coin Metrics (10 ani) + prelungire din bursa,
    clasificare zilnica, cicluri, evaluare walk-forward a predictorilor."""
    t0 = time.time()
    px, cap = load_cm()
    if "btc" not in px:
        raise RuntimeError("Coin Metrics indisponibil - nu am putut descarca seria BTC")
    cm_last = max(px["btc"])
    btc_full = sorted(px["btc"].items())
    extend(None, px, cap, exchange)
    rows, trk = build_timeline(px, cap)
    ath = trk["ath"]
    cyc, wins = cycles(rows, btc_full=sorted(px["btc"].items()))
    wf = walk_forward(rows)
    wpx, wcap = _trim(px, cap)
    st = {"version": VERSION, "built_at": time.time(), "cm_last_day": cm_last,
          "source": "Coin Metrics community data (CC BY-NC 4.0) + lumanari zilnice de pe bursa",
          "assets": len(px), "timeline_keys": ROW_KEYS, "timeline": rows, "btc_ath": ath, "track": trk,
          "cycles": cyc, "altseason_windows": wins, "learn": {**wf, "matured": 0},
          "predictions": [], "window_px": wpx, "window_cap": wcap,
          "build_seconds": round(time.time() - t0, 1)}
    _save(st)
    return st


def daily_update(st, exchange):
    """Adauga zilele noi inchise (din bursa), le clasifica si puncteaza predictiile
    ajunse la orizont - agentul invata unde a avansat piata."""
    px, cap = st["window_px"], st["window_cap"]
    added = extend(st, px, cap, exchange)
    if not added:
        return 0
    rows = st["timeline"]
    have = {R(r, "day") for r in rows}
    trk = dict(st.get("track") or {})
    ba = st.get("btc_ath")
    if isinstance(ba, dict):                      # stare in alta forma: o normalizez
        trk = {**ba, **trk}
        ba = ba.get("ath")
    ath = float(trk.get("ath") or ba or 0.0)
    hist_ai = [R(r, "ai") for r in rows]
    prev = None
    btc_days = sorted(px["btc"])
    doms = {R(r, "day"): R(r, "dom_proxy") for r in rows[-60:]}
    for day in btc_days:
        if day in have or day <= R(rows[-1], "day"):
            continue
        p = px["btc"][day]
        if p >= ath:
            ath = p
            trk.update(ath=p, ath_day=day, low=None, low_day=None)
        elif trk.get("low") is None or p < trk["low"]:
            trk.update(low=p, low_day=day)
        f = day_features(day, px, cap, ath)
        if not f:
            continue
        if f["dom_proxy"] is None and doms:              # o zi fara capitalizare: ultima valoare
            f["dom_proxy"] = doms[max(doms)]
        doms[day] = f["dom_proxy"]
        d30 = doms.get(_s(_d(day) - timedelta(days=30)))
        dom_d30 = (f["dom_proxy"] - d30) if (f["dom_proxy"] is not None and d30 is not None) else None
        closes = [px["btc"][d] for d in btc_days if d <= day]
        ctx = _ctx(p, ath, trk.get("ath_day") or day, trk.get("low"), trk.get("low_day"), day, closes)
        cls = classify_day(f, dom_d30, hist_ai, prev, ctx)
        prev = cls["scores"]
        hist_ai.append(f["alt_index_90d"])
        rows.append(_row(f, dom_d30, cls))
    st["btc_ath"] = ath
    st["track"] = trk
    st["cycles"], st["altseason_windows"] = cycles(rows)
    learn(st)
    st["window_px"], st["window_cap"] = _trim(px, cap)
    return added


def learn(st):
    """Inregistreaza predictia zilei si puncteaza predictiile ajunse la orizont."""
    rows = st["timeline"]
    idx = {R(r, "day"): i for i, r in enumerate(rows)}
    lr = st.setdefault("learn", {})
    for p in st.get("predictions") or []:
        if p.get("scored"):
            continue
        i = idx.get(p["day"])
        if i is None or i + 90 >= len(rows):
            continue
        y = 1.0 if any(R(r, "ai") >= ALT_SEASON for r in rows[i + 1:i + 91]) else 0.0
        n = lr.get("matured", 0)
        for k, key in (("pa", "brier_analog"), ("pt", "brier_trans"), ("pb", "brier_base")):
            if p.get(k) is not None and lr.get(key) is not None:
                # medie mobila: fiecare predictie noua conteaza cat una din evaluarea initiala
                w = 1.0 / (lr.get("evaluations", 100) + n + 1)
                lr[key] = round((1 - w) * lr[key] + w * (p[k] - y) ** 2, 5)
        p["scored"], p["outcome"] = True, y
        lr["matured"] = n + 1
    last = R(rows[-1], "day")
    if not any(p["day"] == last for p in st.get("predictions") or []):
        pa, pt = predict(rows, len(rows) - 1)
        pb = lr.get("base_rate")
        w = weights(lr)
        parts = [(w["analog"], pa), (w["trans"], pt), (w["base"], pb)]
        parts = [(wi, pi) for wi, pi in parts if pi is not None]
        tw = sum(wi for wi, _ in parts)
        comb = sum(wi * pi for wi, pi in parts) / tw if tw else None
        st.setdefault("predictions", []).append({"day": last, "pa": pa and round(pa, 3), "pt": pt and round(pt, 3),
                                                 "pb": pb, "p": comb and round(comb, 3),
                                                 "phase": R(rows[-1], "phase")})
        st["predictions"] = st["predictions"][-400:]


def cycle_context(live_price=None):
    """Contextul ciclului BTC pentru scanarea live: ATH, minimul de dupa ATH si media
    de 200 de zile din istoric, cu pretul curent. None daca istoricul lipseste."""
    st = _load()
    if not st or not st.get("track"):
        return None
    trk = dict(st["track"])
    btc = st.get("window_px", {}).get("btc") or {}
    days = sorted(btc)
    if not days:
        return None
    closes = [btc[d] for d in days]
    price = live_price or closes[-1]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if price >= trk["ath"]:
        trk.update(ath=price, ath_day=today, low=None, low_day=None)
    elif trk.get("low") is None or price < trk["low"]:
        trk.update(low=price, low_day=today)
    return _ctx(price, trk["ath"], trk["ath_day"], trk.get("low"), trk.get("low_day"), today, closes + [price])


def update(exchange=None):
    """Punctul de intrare, apelat la fiecare scanare. Nu arunca exceptii."""
    st = _load()
    try:
        if (not st or st.get("version") != VERSION
                or time.time() - st.get("built_at", 0) > REBUILD_DAYS * 86400):
            st = build(exchange)
            learn(st)
            _save(st)
        elif daily_update(st, exchange):
            _save(st)
    except Exception as e:
        print(f"[!] istoric altseason: {e}")
        if st:
            st["last_error"] = str(e)[:200]
    if not st:
        return None
    try:
        cyc = st.get("cycles") or []
        pos = position(st["timeline"], cyc)
        w = weights(st.get("learn") or {})
        pred = (st.get("predictions") or [{}])[-1]
        return {"position": pos, "cycles": cyc, "windows": st.get("altseason_windows") or [],
                "learn": {**(st.get("learn") or {}), "weights": w}, "prediction": pred,
                "cm_last_day": st.get("cm_last_day"), "last_day": R(st["timeline"][-1], "day"),
                "timeline_tail": [[R(r, "day"), R(r, "ai"), R(r, "btc"), R(r, "phase")]
                                  for r in st["timeline"][::7]],
                "source": st.get("source"), "assets": st.get("assets")}
    except Exception as e:
        print(f"[!] pozitie istorica: {e}")
        return None
