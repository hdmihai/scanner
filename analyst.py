#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyst.py - adaptorul analistului de rotatie a capitalului.

La fiecare scanare (portul ANALYST din core/scan.py, legat in crypto_ai_scanner.py): aduce
lumanarile zilnice care lipsesc - BTC si candidatii din afara listei scanate (cel mult
MAX_FETCH) -, cere raportul nucleului (core/analyst.py: piata in 4 pasi si aceiasi 4 pasi pentru
fiecare token scanat), il salveaza in data/analyst.json, de unde il afiseaza dashboard-ul, si
inregistreaza verdictele per token ale zilei in data/analyst_calls.json, pentru verificarea lor.

Datele vin din ce scanarea are deja: starea altseason (CoinGecko + istoricul pe 10 ani),
detaliile tokenilor (cu zonele de suport/rezistenta), statisticile zonelor. Singurele apeluri
noi sunt lumanarile zilnice ale candidatilor. Nu arunca exceptii: la o problema pastreaza
raportul anterior, marcat cu eroarea - dashboard-ul il arata ca vechi, nu ca actual.
"""

import json
import os
import time

from core import analyst as _core

REPORT_FILE = os.path.join("data", "analyst.json")
# VERDICTELE PER TOKEN, cate unul pe zi UTC (sentiment, pret, R:R): verificate la 7 si 30 de
# zile pe lumanarile zilnice - asa se vede, masurat, daca pasii 1-4 per token au valoare.
CALLS_FILE = os.path.join("data", "analyst_calls.json")
MAX_FETCH = 16         # candidati din afara listei scanate (piata + liderii sectoarelor), cu lumanari zilnice aduse la fiecare scanare
DAILY_BARS = 300


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
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _daily(exchange, base):
    """Lumanarile zilnice INCHISE ale unui ticker, pe prima pereche listata (USDT, USDC, USD)."""
    markets = getattr(exchange, "markets", None) or {}
    for quote in ("USDT", "USDC", "USD"):
        sym = f"{base}/{quote}"
        if markets and sym not in markets:
            continue
        try:
            o = exchange.fetch_ohlcv(sym, timeframe="1d", limit=DAILY_BARS)
        except Exception:
            continue
        if o and len(o) > 31:
            return o[:-1]                      # fara ziua curenta, neinchisa
    return None


def update(exchange, alt_state, details, results, zone_stats, daily_cache):
    """Construieste si salveaza raportul. `daily_cache` = lumanarile zilnice deja aduse de
    scanare pentru tokenii din lista ({simbol: lumanari})."""
    try:
        daily = {s.split("/")[0].upper(): c for s, c in (daily_cache or {}).items() if c}
        if "BTC" not in daily and exchange is not None:
            c = _daily(exchange, "BTC")
            if c:
                daily["BTC"] = c
        top = _core.sector_momentum((alt_state or {}).get("sectors"),
                                    (alt_state or {}).get("indicators")).get("top")
        fetched = 0
        for base in _core.alpha_symbols(alt_state, details, top):
            if base in daily or exchange is None or fetched >= MAX_FETCH:
                continue
            fetched += 1
            c = _daily(exchange, base)
            if c:
                daily[base] = c
        calls = _load(CALLS_FILE, []) or []
        report = _core.build_report(alt_state, details, daily, zone_stats, time.time(), calls)
        report["error"] = None
        report["fetched_daily"] = fetched
        btc_px = (report.get("btc") or {}).get("price") or ((alt_state or {}).get("indicators") or {}).get("btc_price")
        _save(CALLS_FILE, _core.record_calls(calls, time.strftime("%Y-%m-%d", time.gmtime()),
                                             report.get("tokens"), btc_px))
    except Exception as e:
        prev = _load(REPORT_FILE, {}) or {}
        prev.update(error=f"{type(e).__name__}: {str(e)[:200]}",
                    error_at=time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()))
        _save(REPORT_FILE, prev)
        print(f"[!] analist: {prev['error']} - pastrez raportul anterior")
        return prev
    _save(REPORT_FILE, report)
    m, st = report["macro"], report["strategy"]
    sents = [t["strategy"]["sentiment"] for t in (report.get("tokens") or {}).values()]
    print(f"Analist: BTC.D {m['btc_d'].get('status')} | {m['phase'].get('label')} | indice "
          f"{m['alt_index'].get('value')} | sectoare {len(report['sectors']['top'])} | alfa {len(report['alpha'])} "
          f"| sentiment {st['sentiment']} | tokeni {len(sents)} (Bullish {sents.count('Bullish')}, "
          f"Neutru {sents.count('Neutru')}, Bearish {sents.count('Bearish')})")
    return report
