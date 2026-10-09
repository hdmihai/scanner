#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
altseason.py
=============
ADAPTORUL fazei altcoin season: aduce datele reale de piata la fiecare scanare, tine starea pe
disc si cere evaluarea nucleului (core/altseason.py) - logica fazelor nu mai e aici.

DATELE
------
- CoinGecko /global: dominanta BTC si ETH, capitalizare si volum total.
- CoinGecko /coins/markets (top 250): randamente 7z/30z/200z, distanta fata de ATH, volum,
  oferta si FDV - pentru latimea pietei pe niveluri si pentru analist.
- CoinGecko /coins/markets?category=...: sectoarele (naratiunile), prin rotatie.
- Lumanari zilnice de pe exchange: randamentul EXACT pe 90 de zile, pentru indicele Altcoin
  Season in definitia CoinMarketCap.
- Istoricul pe 10 ani si regimul ciclului BTC: altseason_history.py (adaptor) + core/cycles.py.

STAREA
------
data/altseason.json (evaluarea curenta) si data/altseason_history.json (istoricul ORAR al
evaluarilor, ~30 de zile). Numele vechi (altseason.PHASES, altseason.classify, ...) raman
disponibile de aici: sunt cele din nucleu.
"""

import json
import os
import time
import urllib.error
import urllib.request

from core.altseason import (  # noqa: F401  - numele nucleului, disponibile si prin adaptor
    ALT_BIAS, BEAR_DD, BTC_BIAS, BULL_DD, DAILY_KEEP, HOLD_BAND, LATE_DAYS, MIN_PERF90, PHASE_TIER, PHASES,
    RECOVERY, REGIME_ALLOWED, REGIME_NAME, SECTOR_COINS, SECTOR_PER_SCAN, SECTOR_RETRY, SECTOR_TTL, SECTORS,
    STABLE, WRAPPED, _compact, _cycle_triggers, _daily_series, _fmt, _median, _reasons, _rel, _triggers,
    alt_universe, assemble_state, band, classify, cycle_regime, due_sectors, evaluate, growth_candidates,
    hourly_entry, indicators, market_subset, perf90_accept, ramp, season_label, sector_entry, sector_failed)

CG = "https://api.coingecko.com/api/v3"
CG_KEY = os.environ.get("COINGECKO_API_KEY", "")
STATE_FILE = os.path.join("data", "altseason.json")
HISTORY_FILE = os.path.join("data", "altseason_history.json")
HISTORY_KEEP = 720             # ~30 de zile la o scanare pe ora


# ---------------------------------------------------------------------------
KEY_NOTE = []      # de ce a fost refuzata cheia Demo (raportat de diagnostic)


def _get_once(url, retries, use_key):
    headers = {"accept": "application/json"}
    if use_key and CG_KEY:
        headers["x-cg-demo-api-key"] = CG_KEY
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:
                time.sleep(15 * (attempt + 1))
                continue
            raise


def _get(url, retries=2):
    """Cu cheia Demo; daca e REFUZATA (invalida, sau cota lunara epuizata),
    o singura reincercare pe API-ul public gratuit, fara cheie - suficient pentru
    cele doua apeluri pe scanare. In productie, altseason a ramas fara date peste
    15 ore, in timp ce apelul scanerului, facut fara cheie, nu depindea de ea."""
    try:
        return _get_once(url, retries, use_key=True)
    except urllib.error.HTTPError as e:
        if not CG_KEY or e.code not in (400, 401, 403, 429):
            raise
        note = f"HTTP {e.code}"
        try:
            note += " " + e.read().decode()[:120]
        except Exception:
            pass
        KEY_NOTE.append(note.strip())
        return _get_once(url, 1, use_key=False)


def _get_light(url):
    """O singura incercare (cu cheia, apoi fara, daca e refuzata), FARA asteptare la 429: pentru
    datele de sector, care pot astepta scanarea urmatoare - nu au voie sa intinda scanarea."""
    try:
        return _get_once(url, 0, use_key=True)
    except urllib.error.HTTPError as e:
        if not CG_KEY or e.code not in (400, 401, 403):
            raise
        return _get_once(url, 0, use_key=False)


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
def fetch_market():
    glob = _get(f"{CG}/global").get("data") or {}
    markets = _get(f"{CG}/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250"
                   f"&page=1&price_change_percentage=7d,30d,200d")
    return glob, markets


def perf_90d(exchange, bases, cache):
    """Randamentul exact pe 90 de zile, din lumanari zilnice INCHISE de pe exchange.

    Se schimba o singura data pe zi (la inchiderea zilei UTC), deci se recalculeaza la prima
    scanare dupa inchidere; o recalculare incompleta nu inlocuieste una completa
    (core.altseason.perf90_accept) - se reincearca la scanarea urmatoare."""
    today = time.strftime("%Y-%m-%d", time.gmtime())
    if cache and cache.get("day") == today and cache.get("perf"):
        return cache
    perf = {}
    if exchange is None:
        return cache or {"ts": 0, "day": None, "perf": {}}
    markets = getattr(exchange, "markets", None) or {}
    for b in bases:
        sym = f"{b.upper()}/USDT"
        if markets and sym not in markets:
            continue
        try:
            o = exchange.fetch_ohlcv(sym, timeframe="1d", limit=92)
        except Exception:
            continue
        if not o or len(o) < 91:
            continue
        closes = [c[4] for c in o[:-1]]           # fara bara zilnica neinchisa
        if len(closes) >= 91 and closes[-91] > 0:
            perf[b.lower()] = round((closes[-1] / closes[-91] - 1) * 100, 3)
    out, ok = perf90_accept(perf, cache, today, time.time())
    if not ok:
        print(f"[!] altseason: doar {len(perf)} randamente pe 90 de zile (anterior "
              f"{len((cache or {}).get('perf') or {})}) - pastrez calculul precedent si reincerc la scanarea urmatoare")
    return out


def fetch_sectors(cache, now=None):
    """Monedele sectoarelor programate la aceasta scanare (core.altseason.due_sectors): cel mult
    SECTOR_PER_SCAN cereri, fiecare sector la cel mult SECTOR_TTL. Un sector care esueaza isi
    pastreaza datele anterioare si se reincearca dupa o ora."""
    now = now or time.time()
    items = dict((cache or {}).get("items") or {})
    for cid, name in due_sectors(items, now):
        try:
            rows = _get_light(f"{CG}/coins/markets?vs_currency=usd&category={cid}&order=market_cap_desc"
                              f"&per_page={SECTOR_COINS}&page=1&price_change_percentage=7d,30d,200d")
        except Exception as e:
            items[cid] = sector_failed(items.get(cid), name, e, now)
            continue
        items[cid] = sector_entry(name, rows, now, time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(now)))
    return {"items": items, "total": len(SECTORS)}


def update(exchange=None, scan_results=None):
    """Punctul de intrare, apelat de scaner la fiecare rulare. Nu arunca exceptii:
    la o problema de date pastreaza ultima evaluare si o marcheaza ca veche."""
    state = _load(STATE_FILE, {})
    try:
        glob, markets = fetch_market()
    except Exception as e:
        # CAUZA EXACTA, salvata: in productie datele au ramas vechi peste 10 ore,
        # iar motivul aparea doar in jurnalul Actions. Acum il vede diagnosticul.
        err = str(e)[:200]
        try:
            err += " " + e.read().decode()[:120]
        except Exception:
            pass
        print(f"[!] altseason: CoinGecko indisponibil ({err}) - pastrez evaluarea anterioara.")
        state = state or {}
        state.update(stale=True, last_error=err.strip(),
                     last_error_at=time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()))
        _save(STATE_FILE, state)
        return state if state.get("classification") else None

    alts = alt_universe(markets)
    bases = ["btc"] + [(c.get("symbol") or "") for c in alts[:100]]
    perf90 = perf_90d(exchange, bases, state.get("perf90"))
    ind = indicators(glob, markets, perf90)
    history = _load(HISTORY_FILE, [])
    # ISTORICUL SI REGIMUL CICLULUI, INAINTE de clasificare: regimul BTC (ATH,
    # minimul pietei bear, media de 200 de zile) e primul strat al deciziei.
    hist_ctx, ctx = None, None
    try:
        import altseason_history as _AH
        hist_ctx = _AH.update(exchange)
        ctx = _AH.cycle_context(ind.get("btc_price"))
    except Exception as _e:
        print(f"[!] context istoric altseason: {_e}")
    if ctx is None:
        print("[!] altseason: regimul ciclului indisponibil (istoric lipsa) - clasificare doar pe metrici relative")
    cls, cands = evaluate(ind, history, state, ctx, perf90, markets, scan_results)
    # PENTRU ANALIST: sectoarele (rotatie de cereri, vezi fetch_sectors), monedele de interes
    # cu oferta/FDV/volum (candidatii, tokenii scanati) si seriile zilnice ale dominantei BTC si
    # ale indicelui - din care se masoara trendul si nivelurile cheie.
    try:
        sectors = fetch_sectors(state.get("sectors"))
    except Exception as _e:
        print(f"[!] sectoare: {_e}")
        sectors = state.get("sectors")
    now = time.time()
    new = assemble_state(now, time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                         time.strftime("%Y-%m-%d", time.gmtime()),
                         (f"cheia CoinGecko a fost refuzata ({KEY_NOTE[-1]}); datele vin din API-ul "
                          f"public gratuit" if KEY_NOTE else None),
                         ind, cls, cands, perf90, sectors, market_subset(markets, cands, scan_results), state, history)
    # CONTEXTUL ISTORIC PE 10 ANI (altseason_history.py): ciclurile, pozitia de
    # acum fata de ele, analogiile si predictiile invatate. Actualizat zilnic
    # (zilele noi inchise) si reconstruit saptamanal; nu opreste niciodata evaluarea.
    new["history"] = hist_ctx
    _save(STATE_FILE, new)
    history.append(hourly_entry(new["ts"], cls, ind))
    _save(HISTORY_FILE, history[-HISTORY_KEEP:])
    print(f"Altseason: regim {cls.get('regime_name') or 'n/d'} | faza {cls['phase']} - {cls['name']} "
          f"(incredere {cls['confidence']:.0%}) | metrici relative: faza {cls.get('relative_phase')} | "
          f"BTC.D {ind.get('btc_d')}% | ETH/BTC 30z {ind.get('eth_btc_30d')}% | "
          f"indice 90z {ind.get('alt_index_90d')} ({cls['season']})")
    return new
