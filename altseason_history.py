#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
altseason_history.py
=====================
ADAPTORUL istoricului altseason pe 10 ani: descarca seriile, le prelungeste din bursa, tine
starea pe disc (data/altseason_cycles.json) si cere calculul nucleului (core/cycles.py) -
linia de timp, ciclurile, pozitia de acum, analogiile si invatarea nu mai sunt aici.

SURSA ISTORICA (gratuita, pe GitHub): Coin Metrics community data
(github.com/coinmetrics/data, licenta CC BY-NC 4.0): serii zilnice de pret si capitalizare,
BTC din 2010. Descarcata doar la construirea istoricului (prima rulare, la o versiune noua a
regulilor si saptamanal); apoi istoricul continua din lumanarile zilnice ale bursei pe care
scanerul le foloseste deja.

Numele vechi (altseason_history.position, .cycles, .R, ...) raman disponibile de aici: sunt cele
din nucleu.
"""

import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from core.cycles import (  # noqa: F401  - numele nucleului, disponibile si prin adaptor
    ALT_SEASON, FEATS, ROW_KEYS, STABLE, START, WINDOW, R, _ctx, _cur, _d, _median, _num, _row, _s, _supply,
    _trim, _zstats, analogs, build_state, build_timeline, classify_day, cycles, day_features, extend_rows,
    extend_stables, learn, live_context, merge_asset, outcome, parse_cm_csv, position, predict, summary,
    transition_stats, walk_forward, weights)

# 3: histerezisul regimului de acumulare (core/altseason.HOLD_BAND) - istoricul se reconstruieste
#    cu regula noua; 2: decizia in doua straturi (regimul ciclului BTC)
VERSION = 3
# NU altseason_history.json: acela e istoricul ORAR al evaluarii live (altseason.py)
STATE_FILE = os.path.join("data", "altseason_cycles.json")
CM_RAW = "https://raw.githubusercontent.com/coinmetrics/data/master/csv/{}.csv"
CM_LOCAL_DIR = os.environ.get("CM_LOCAL_DIR")          # doar pentru teste offline
REBUILD_DAYS = 7

# Activele care au contat istoric in top 100 si exista in Coin Metrics.
CM_ASSETS = """btc eth xrp bnb sol ada doge trx ton avax shib dot link bch ltc near pol uni icp etc apt xlm fil hbar
atom vet arb op imx mnt okb cro inj rndr render grt stx algo xmr qnt aave mkr sand mana axs chz eos xtz theta ftm egld
flow neo miota xem zec dash bsv waves omg zrx bat enj lsk qtum icx ont zil nano dgb sc steem rep glm ht leo ftt luna
kava rune gala ape gmt ksm celo ar ens lrc 1inch ankr bal band ocean rsr storj zen crv snx comp yfi sushi ldo sei sui
tia pepe wif bonk fet kas wld pyth jup ena ondo strk hype trump xdc mina bgb nexo gt kcs tfuel rvn dcr xvg ardr ark bnt
pax usdt usdc busd dai tusd usdp fdusd usde pyusd usds gusd""".split()


# ---------------------------------------------------------------------------
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
        p, c = parse_cm_csv(txt)
        if p:
            px[a] = p
        if c:
            cap[a] = c
    return px, cap


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


def extend(state, px, cap, exchange):
    """Prelungeste seriile de la ultima zi cunoscuta pana ieri, din bursa (continuitatea si
    oferta: core.cycles.merge_asset)."""
    last = max(px.get("btc", {"": 0}))
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).strftime("%Y-%m-%d")
    if last >= yesterday or exchange is None:
        return 0
    start = (datetime.strptime(last, "%Y-%m-%d").date() + timedelta(days=1)).strftime("%Y-%m-%d")
    added = 0
    markets = getattr(exchange, "markets", None) or {}
    for a in list(px):
        if a in STABLE:
            continue
        if markets and f"{a.upper()}/USDT" not in markets:
            continue
        n, why = merge_asset(px, cap, a, _exchange_daily(exchange, a, start))
        if why:
            print(f"  istoric: {why}")
        added = max(added, n)
    extend_stables(px, cap)
    return added


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
    extend(None, px, cap, exchange)
    st = build_state(px, cap, VERSION, time.time(),
                     "Coin Metrics community data (CC BY-NC 4.0) + lumanari zilnice de pe bursa")
    st["build_seconds"] = round(time.time() - t0, 1)
    _save(st)
    return st


def daily_update(st, exchange):
    """Adauga zilele noi inchise (din bursa), le clasifica si puncteaza predictiile
    ajunse la orizont - agentul invata unde a avansat piata."""
    px, cap = st["window_px"], st["window_cap"]
    added = extend(st, px, cap, exchange)
    if not added:
        return 0
    extend_rows(st, px, cap)
    return added


def cycle_context(live_price=None):
    """Contextul ciclului BTC pentru scanarea live (core.cycles.live_context)."""
    return live_context(_load(), live_price, datetime.now(timezone.utc).strftime("%Y-%m-%d"))


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
        return summary(st)
    except Exception as e:
        print(f"[!] pozitie istorica: {e}")
        return None
