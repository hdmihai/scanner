#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core.scan - cazul de utilizare al scanarii (NUCLEU, fara I/O): scorare, analiza pe token, planuri,
invatare si modulele per bursa, prin porturi. Radacina compozitiei, care leaga adaptoarele,
e crypto_ai_scanner.py.

crypto_ai_scanner.py (documentatia originala)
=====================
Scaner de piata crypto cu scor de risc, probabilitate, "memorie" JSON
persistenta si ponderi adaptive (invata din rezultatele trecute).

PORNIRE RAPIDA
--------------
1) pip install ccxt requests
2) Editeaza sectiunea CONFIG de mai jos (mai ales telegram_bot_token si
   telegram_chat_id daca vrei notificari pe telefon).
3) Ruleaza: python3 crypto_ai_scanner.py
4) Programeaza-l sa ruleze periodic (ex: la fiecare ora) cu cron, un task
   scheduler, sau Termux:Boot + termux-job-scheduler daca il rulezi pe telefon.

Unde il tii pornit (gratuit), in ordinea recomandarii:
1. GitHub Actions (vezi .github/workflows/scan.yml alaturat) - ruleaza pe
   infrastructura GitHub, gratuit, fara server de administrat de tine
2. un mini-VPS (ex: Oracle Cloud Free Tier) - daca vrei control total
3. un PC vechi / Raspberry Pi acasa, mereu pornit
4. Termux, direct pe telefon - functioneaza, dar Android poate opri
   scripturile din fundal daca nu dezactivezi optimizarea bateriei pentru
   Termux si nu-l tii scutit de "battery saver"

Nu contine cod care trimite ordine de tranzactionare - doar scaneaza,
scoreaza si notifica. Nu este sfat financiar.
"""

import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

# Nucleul importa DOAR din nucleu si din porturi (garda: check_core_is_pure).
from core import agent as ai_agent
from core import elliott as ew_mod
from core import evidence as ev_mod
from core import indicators
from core import liquidation as liq_mod
from core import liquidity_structure as ls_mod
from core import market as mkt
from core import market_structure as struct_mod
from core import plans as plan_tracker
from ports.market_data import CAP_OHLCV, CAP_ORDERBOOK_LIVE
from core.analysis import (_open_plan_for_base, build_signal_context, build_symbol_details,
                           fetch_liquidity_levels, light_details, proposal_record)
from core.config import (CHART_FILE, CONFIG, DEFAULT_WEIGHTS, DETAILS_FILE, EXCHANGES_DIR,
                         EXCHANGES_FILE, EXCHANGE_HISTORY_KEEP, EXCHANGE_SCANS_FILE,
                         EXCHANGE_TIME_BUDGET, HISTORY_FILE, PER_SIGNAL_ORDER_BOOK,
                         SECONDARY_SCAN_LIMIT, WEIGHTS_FILE, WEIGHTS_HISTORY_FILE,
                         timeframe_seconds)
from core.geometry import compute_fibonacci, compute_structure_levels, compute_trade_plan
from core.learning import compute_persistence_and_age, evaluate_and_learn
from core.scoring import ema_series_full, round_price, rsi, score_symbol
from core.universe import build_eligible_pairs


# ================================ PORTURI ===================================
# Legate de radacina compozitiei (crypto_ai_scanner.py) inainte de rulare. Un port
# nelegat opreste rularea cu un mesaj clar, nu continua in tacere fara date.
class _Unbound:
    def __init__(self, name):
        self._name = name

    def _fail(self, *_a, **_k):
        raise RuntimeError(f"portul {self._name} nu e legat - ruleaza scanarea prin crypto_ai_scanner.py")

    __call__ = _fail

    def __getattr__(self, _attr):
        return self._fail


STORE = _Unbound("STORE")              # ports.store.DocumentStore (data/*.json)
PLAN_STORE = _Unbound("PLAN_STORE")    # ports.store.PlanStore (plans.json + arhiva)
AGENT_STORE = _Unbound("AGENT_STORE")  # ports.store.AgentStore (agent_model.json)
VENUES = _Unbound("VENUES")            # ports.market_data.Venues (bursele, prin ccxt)
NOTIFIER = _Unbound("NOTIFIER")        # ports.notifier.Notifier (Telegram)
ALTSEASON = _Unbound("ALTSEASON")      # (conexiune, rezultate) -> starea altseason
TOP_SYMBOLS = _Unbound("TOP_SYMBOLS")  # (n) -> simbolurile din top-n CoinGecko


def load_json(path, default):
    return STORE.load(path, default)


def save_json(path, data):
    STORE.save(path, data)


def save_json_compact(path, data):
    STORE.save_compact(path, data)


# ============================ INDICATORI =============================
# Implementati simplu, in Python pur, fara pandas/numpy - ca sa mearga
# usor si pe un VPS minimal sau pe telefon (Termux).


# ==================== STRUCTURA, FIBONACCI, PLAN DE TRADE =================
# Adaugate ca sa acopere sectiunile "AI Plan" / "Market Structure" /
# "Fibonacci" din dashboard-ul de referinta. Sunt euristici transparente,
# nu o reconstructie a vreunui produs anume - le poti inlocui oricand cu
# propria ta logica din AI_Dashboard_v10.pine.


# =============================== SCOR =================================


# ============================ LICHIDITATE (SMC) ===========================
# Nivelurile de lichiditate (cele mai mari cluster-e bid/ask din order book)
# sunt un concept central in Smart Money Concepts: zone unde e probabil sa
# reactioneze pretul, pentru ca acolo sta volumul mare de ordine.


# =========================== PERSISTENTA JSON ==========================


# ============================== TELEGRAM ================================


def format_message(scan):
    """Construieste mesajul Telegram ca un mini-dashboard (tabel monospace),
    vizual apropiat de layout-ul din poza cu ENO AI CORE."""

    def table(rows):
        header = f"{'SYMBOL':<14}{'SCORE':>6}{'PROB':>8}{'PERS':>6}"
        body = [
            f"{r['symbol']:<14}{r['risk_adjusted']:>6}{r['probability']:>7}%{r['persistence']:>6}"
            for r in rows
        ]
        return "```\n" + "\n".join([header] + body) + "\n```" if rows else "_(niciun semnal)_"

    # ASCII pur intentionat: emoji-urile s-au corupt de trei ori la transferul
    # fisierului (UTF-8 citit ca Latin-1), producand caractere ilizibile in mesajele Telegram.
    # Textul simplu nu poate fi corupt de nicio conversie de encoding.
    parts = [f"*SCAN {scan['scan_time']}* - universe {scan['universe_size']}"]
    parts.append("\n*TOP LONG*")
    parts.append(table(scan["top_long"]))
    parts.append("\n*TOP SHORT*")
    parts.append(table(scan["top_short"]))
    if scan.get("best_candidate"):
        b = scan["best_candidate"]
        parts.append(
            f"\n*BEST CANDIDATE:* {b['symbol']} {b['direction']} - "
            f"conf {b['probability']}% - exp {b['expected_r']}R"
        )
    return "\n".join(parts)


# ================================ MAIN ===================================


def analyze_exchange(adapter, handle, card, ctx):
    """ANALIZA COMPLETA pe o bursa secundara, cu acelasi nucleu ca pe bursa activa:
    scor, detalii pe token (grafic, Elliott, structura, lichidari), evidente si
    decizia pe care ar lua-o agentul pentru semnalele de top.

    NU creeaza planuri si NU atinge memoria agentului (vezi nota de la
    SECONDARY_SCAN_LIMIT). Orice esec e o stare raportata, nu o exceptie, iar
    bugetul de timp se verifica intre apeluri: o bursa lenta se trunchiaza, nu
    intinde scanarea."""
    t0 = time.monotonic()
    deadline = t0 + ctx["budget"]
    caps = card.get("available") or [CAP_OHLCV]
    log = []
    out = {"id": adapter.id, "label": adapter.label, "resolved": [], "missing": [],
           "results": [], "details": {}, "proposals": {}, "truncated": False,
           "error": None, "history_entry": None, "log": log, "duration_s": 0.0}
    markets = adapter.markets(handle)
    if not markets:
        out["error"] = "pietele bursei nu au putut fi incarcate"
        return out
    tickers = adapter.tickers(handle)
    # Aceeasi selectie ca pe bursa activa: perechea cu volumul cel mai mare pentru
    # fiecare token (pe Kraken, USD bate de departe USDT).
    aliases = CONFIG.get("aliases", {})
    # O SINGURA pereche per token din watchlist: un token listat sub doua nume
    # (POL si MATIC) ar ocupa doua locuri si ar putea impinge alt token din lista de 30.
    canon = {al: b for b in (CONFIG.get("watchlist") or []) for al in aliases.get(b, [b])}
    seen, resolved = set(), []
    for s_ in build_eligible_pairs(markets, tickers, ctx["scope"]):
        c_ = canon.get(s_.split("/")[0], s_.split("/")[0])
        if c_ not in seen:
            seen.add(c_)
            resolved.append(s_)
    resolved = resolved[: ctx["limit"]]
    out["resolved"] = sorted(resolved)
    out["missing"] = [b for b in (CONFIG.get("watchlist") or [])
                      if not any(s_.split("/")[0] in aliases.get(b, [b]) for s_ in resolved)]
    cache, results = {}, []
    for sym in resolved:
        if time.monotonic() > deadline:
            out["truncated"] = True
            log.append(f"buget de timp atins dupa {len(cache)} din {len(resolved)} simboluri")
            break
        try:
            ohlcv = handle.fetch_ohlcv(sym, timeframe=CONFIG["timeframe"], limit=CONFIG["candles"])
        except Exception as e:
            log.append(f"{sym}: {str(e)[:120]}")
            continue
        if len(ohlcv) > 1:
            ohlcv = ohlcv[:-1]          # aceeasi regula: fara lumanarea neinchisa
        cache[sym] = ohlcv
        scored = score_symbol(ohlcv, ctx["weights"])
        if not scored:
            continue
        persistence, age_minutes = compute_persistence_and_age(
            ctx["history"], sym, scored["direction"], ctx["now_ts"])
        results.append({"symbol": sym, "persistence": persistence, "age_minutes": age_minutes, **scored})
    if resolved and not cache:
        # nicio serie descarcata (bursa nu raspunde la lumanari sau bugetul s-a terminat
        # inainte de primul simbol): modulul nu are voie sa se goleasca - datele
        # anterioare raman, marcate vechi, cu motivul
        out["error"] = ("bugetul de timp s-a terminat inainte de primul simbol" if out["truncated"]
                        else f"nicio serie de lumanari descarcata din {len(resolved)} simboluri")
        out["duration_s"] = round(time.monotonic() - t0, 1)
        return out
    for r in results:
        out["details"][r["symbol"]] = build_symbol_details(r, cache[r["symbol"]], ctx["hist_tab"])
    results.sort(key=lambda r: -r["risk_adjusted"])
    out["results"] = results
    top = ([r for r in results if r["direction"] == "LONG"][: CONFIG["top_n_per_direction"]]
           + [r for r in results if r["direction"] == "SHORT"][: CONFIG["top_n_per_direction"]])
    for sig in top:
        if time.monotonic() > deadline:
            out["truncated"] = True
            log.append(f"buget de timp atins la propuneri ({len(out['proposals'])} din {len(top)})")
            break
        book = (fetch_liquidity_levels(handle, sig["symbol"], attempts=adapter.book_limits)
                if CAP_ORDERBOOK_LIVE in caps else None)
        levels, sig_e = build_signal_context(sig, cache[sig["symbol"]], handle, caps, book,
                                             ctx["alt_state"])
        if not levels:
            continue
        sig_e["neighbors"] = ai_agent.comparable_entries(ai_agent.extract_features(sig_e), ctx["closed"])
        agent_pred = ai_agent.predict_for_signal(ctx["agent_model"], ctx["agent_state"], sig_e)
        sig_e = {**sig_e, "bar_seconds": timeframe_seconds()}
        decision = plan_tracker.decide(ctx["calibration"], sig_e, agent_pred)
        rec = proposal_record(sig_e, levels, decision, agent_pred, trained=False)
        rec["learning_open_plan"] = _open_plan_for_base(ctx["plan_store"], sig["symbol"], sig["direction"])
        out["proposals"][sig["symbol"]] = rec
    out["history_entry"] = {"scan_id_ts": ctx["now_ts"],
                            "results": [{"symbol": r["symbol"], "direction": r["direction"]} for r in results]}
    out["duration_s"] = round(time.monotonic() - t0, 1)
    return out


def _analyze_safe(adapter, handle, card, ctx):
    try:
        return analyze_exchange(adapter, handle, card, ctx)
    except Exception as e:
        return {"id": adapter.id, "label": adapter.label, "error": f"{type(e).__name__}: {str(e)[:160]}",
                "resolved": [], "missing": [], "results": [], "details": {}, "proposals": {},
                "truncated": False, "history_entry": None, "log": [], "duration_s": 0.0}


def run_exchange_modules(exchange_cards, exchange_handles, primary_id, ctx):
    """Analiza completa pe fiecare bursa secundara conectata, IN PARALEL (fiecare cu
    bugetul ei, pe conexiunea deja deschisa la sondare), apoi datele fiecarui modul
    in data/exchanges/<id>/. Intoarce {id: rezumat} pentru exchange_scans.json.

    La un esec total al unei burse, detaliile anterioare raman pe disc, iar
    rezumatul poarta eroarea: dashboard-ul afiseaza atunci datele ca vechi, cu
    motivul - nu le prezinta drept actuale si nici nu goleste modulul."""
    jobs = []
    for card in exchange_cards:
        eid = card["id"]
        if eid == primary_id or not card.get("connected") or eid not in exchange_handles:
            continue
        hist = load_json(os.path.join(EXCHANGES_DIR, eid, "history.json"), [])
        jobs.append((VENUES.adapter(eid), exchange_handles[eid], card, {**ctx, "history": hist}))
    if not jobs:
        return {}
    print(f"\nModule per bursa: analiza completa pe {len(jobs)} burse, in paralel "
          f"(buget {ctx['budget']}s fiecare)")
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = [pool.submit(_analyze_safe, *job) for job in jobs]
        outs = [f.result() for f in futures]
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    summaries = {}
    for (adapter, _h, card, jctx), res in zip(jobs, outs):
        eid = adapter.id
        folder = os.path.join(EXCHANGES_DIR, eid)
        ok = not res.get("error")
        if ok:
            save_json_compact(os.path.join(folder, "details.json"),
                              {"scan_time": stamp, "timeframe": CONFIG["timeframe"], "exchange": eid,
                               "label": adapter.label, "trained": False, "symbols": res["details"]})
            save_json_compact(os.path.join(folder, "proposals.json"),
                              {"scan_time": stamp, "exchange": eid, "trained": False,
                               "learning_exchange": primary_id, "proposals": res["proposals"]})
            if res.get("history_entry"):
                save_json_compact(os.path.join(folder, "history.json"),
                                  (jctx["history"] + [res["history_entry"]])[-EXCHANGE_HISTORY_KEEP:])
        summaries[eid] = {
            "resolved": res["resolved"], "missing": res["missing"],
            "results": [{"symbol": r["symbol"], "direction": r["direction"], "score": r["risk_adjusted"],
                         "price": r["price"], "atr": r["atr"], "components": r["components"]}
                        for r in res["results"]],
            "details": light_details(res["details"]),
            "truncated": res["truncated"], "error": res["error"], "primary": False,
            "analysis": "completa", "proposals": len(res["proposals"]),
            "duration_s": res["duration_s"], "scan_time": stamp if ok else None,
        }
        print(f"  [{eid}] {len(res['results'])} semnale din {len(res['resolved'])} simboluri, "
              f"{len(res['proposals'])} propuneri, {res['duration_s']}s"
              + ("  (trunchiat)" if res["truncated"] else "")
              + (f"  EROARE: {res['error']}" if res["error"] else ""))
        for line in res.get("log", [])[:6]:
            print(f"      {line}")
    return summaries


def probe_all_exchanges(scope):
    """Sondeaza TOATE bursele din lista, nu doar pana la prima care merge.

    Costa cateva secunde in plus, dar dashboard-ul are nevoie de starea fiecareia
    ca sa poata arata un tab per bursa, inclusiv pentru cele care nu raspund.
    Fisele se salveaza si se folosesc si la alegerea bursei de lucru.
    """
    cards = []
    # Sondez tot registrul, nu doar lantul de fallback: dashboard-ul trebuie sa
    # arate un tab si pentru bursele care nu sunt candidate de lucru dar au
    # capabilitati relevante (ex. Bybit, singura cu arhive de order book).
    # Ordinea de fallback ramane prima, ca alegerea sa fie determinista.
    order = CONFIG["exchange_fallback"] + [e for e in VENUES.order()
                                           if e not in CONFIG["exchange_fallback"]]
    handles = {}
    for eid in order:
        card, ex = VENUES.probe(eid)
        cards.append(card)
        if ex is not None and card["connected"]:
            # pastrez conexiunea: scanarea de afisare o refoloseste, ca sa nu
            # platesc inca o data load_markets pentru fiecare bursa
            handles[eid] = ex
        status = "conectat" if card["connected"] else f"esuat: {card['error']}"
        caps = ",".join(card["available"]) or "-"
        print(f"  [{eid}] {status} | capabilitati: {caps}")
    return cards, handles


def connect_exchange(scope):
    """Alege exchange-ul dupa ACOPERIRE, nu doar dupa conectivitate.

    Varianta veche lua primul exchange care raspundea - a nimerit Kraken, care
    a mers, dar acopera putin din top-200. Acum, pentru fiecare exchange care
    raspunde, calculez cate simboluri din scope gaseste efectiv si ma opresc la
    primul care trece pragul; daca niciunul nu-l trece, folosesc cel mai bun
    gasit (tot mai bine decat sa pic)."""
    best = None
    last_error = None

    for exchange_id in CONFIG["exchange_fallback"]:
        exchange = VENUES.open(exchange_id)
        if exchange is None:
            print(f"[!] '{exchange_id}' nu exista in ccxt, sar peste.")
            continue
        try:
            markets = exchange.load_markets()
            tickers = exchange.fetch_tickers()
        except Exception as e:
            print(f"[!] {exchange_id} indisponibil din acest runner: {e}")
            last_error = e
            continue

        pairs = build_eligible_pairs(markets, tickers, scope)
        print(f"[OK] {exchange_id}: {len(markets)} piete -> {len(pairs)} simboluri eligibile din scope.")

        if best is None or len(pairs) > len(best[3]):
            best = (exchange, markets, tickers, pairs, exchange_id)

        if len(pairs) >= CONFIG["min_universe"]:
            print(f"=> Folosesc {exchange_id} (acoperire suficienta).")
            return best

    if best is None:
        raise SystemExit(
            f"[EROARE FATALA] Niciun exchange din {CONFIG['exchange_fallback']} nu "
            f"a raspuns din acest runner. Ultima eroare: {last_error}\n"
            "Adauga alt exchange in CONFIG['exchange_fallback'], sau ruleaza "
            "scriptul de pe un server/PC/telefon cu IP rezidential (nu de cloud)."
        )

    print(f"[!] Niciun exchange nu atinge pragul de {CONFIG['min_universe']} simboluri. "
          f"Folosesc cel mai bun gasit: {best[4]} cu {len(best[3])} simboluri.")
    return best


def main():
    watchlist = CONFIG.get("watchlist") or []
    if watchlist:
        # pragul de acoperire nu poate depasi cate simboluri exista in watchlist
        CONFIG["min_universe"] = max(1, int(len(watchlist) * 0.7))
        # Extind fiecare simbol cu aliasurile lui, ca sa nu ratez perechea din
        # cauza unei redenumiri (ex. TON -> GRAM).
        coingecko_scope = set()
        for sym in watchlist:
            coingecko_scope.update(CONFIG.get("aliases", {}).get(sym, [sym]))
        print(f"Watchlist activa: {len(watchlist)} simboluri "
              f"({len(coingecko_scope)} incluzand aliasuri) - ignor top-200 CoinGecko.")
    else:
        coingecko_scope = TOP_SYMBOLS(CONFIG["coingecko_scope"])
        if not coingecko_scope:
            print("[!] Nu am putut lua lista CoinGecko - continui fara filtrul de scope.")

    # Sondez TOATE bursele: dashboard-ul are nevoie de starea fiecareia pentru
    # tab-uri, inclusiv pentru cele care nu raspund.
    exchange_cards, exchange_handles = probe_all_exchanges(coingecko_scope)

    exchange, markets, all_tickers, eligible, exchange_id = connect_exchange(coingecko_scope)

    # Fixez semnatura de capabilitati INAINTE de a crea orice plan. Geometria
    # trebuie sa reflecte ce a oferit efectiv bursa, nu implicitul din mediu.
    active_card = next((c for c in exchange_cards if c["id"] == exchange_id), None)
    active_caps = (active_card or {}).get("available") or [CAP_OHLCV]
    caps_sig = mkt.capability_signature(active_caps)
    plan_tracker.set_capabilities(caps_sig)
    print(f"Capabilitati active pe {exchange_id}: {','.join(active_caps)} "
          f"-> geometria {plan_tracker.GEOMETRY_VERSION}")

    # Capabilitatile bursei ALESE decid ce evidente se pot calcula si intra in
    # semnatura geometriei. Fara pasul asta, order flow-ul nu ar aparea niciodata
    # chiar daca bursa il suporta, iar semnatura ar ramane blocata pe "o".
    active_caps = next((c["available"] for c in exchange_cards
                        if c["id"] == exchange_id and c["connected"]), [CAP_OHLCV])
    active_sig = mkt.capability_signature(active_caps)
    if os.environ.get("SCAN_CAPS") != active_sig:
        print(f"[i] Capabilitati active pe {exchange_id}: {','.join(active_caps)} "
              f"-> semnatura '{active_sig}'")
        print(f"    Geometria e {plan_tracker.GEOMETRY_VERSION}. Daca semnatura difera, "
              f"seteaza SCAN_CAPS={active_sig} in workflow ca planurile sa fie "
              f"grupate corect la invatare.")
    save_json(EXCHANGES_FILE, {"exchanges": exchange_cards, "used": exchange_id,
                               "active_signature": active_sig,
                               "geometry": plan_tracker.GEOMETRY_VERSION})
    universe = eligible[: CONFIG["universe_size"]]
    print(f"Universe final: {len(universe)} simboluri pe {exchange_id}.")

    weights = load_json(WEIGHTS_FILE, dict(DEFAULT_WEIGHTS))
    history = load_json(HISTORY_FILE, [])

    # 1) evalueaza semnalele vechi si "invata" din ele
    weights = evaluate_and_learn(
        history, weights, all_tickers,
        CONFIG["lookahead_hours"], CONFIG["hit_threshold_atr"],
    )

    # 2) scaneaza piata curenta
    now_ts = time.time()
    results = []
    ohlcv_cache = {}
    for symbol in universe:
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=CONFIG["timeframe"], limit=CONFIG["candles"])
        except Exception as e:
            print(f"[!] {symbol}: {e}")
            continue
        # Renunt la ULTIMA lumanare: e cea curenta, inca neinchisa.
        # De ce conteaza: scanarea porneste la minute imprevizibile (cron e :07,
        # dar am masurat rulari intre :09 si :59 din cauza intarzierilor GitHub),
        # deci bara curenta e prinsa oriunde intre 8% si 92% formata. EMA, RSI si
        # ATR calculate pe ea se schimba pana la inchidere - acelasi setup da
        # scoruri diferite doar in functie de cat de tarziu a pornit jobul.
        # In plus, backtest.py foloseste doar bare inchise; fara acest fix cele
        # doua nu testeaza acelasi lucru si rezultatele nu sunt comparabile.
        if len(ohlcv) > 1:
            ohlcv = ohlcv[:-1]
        ohlcv_cache[symbol] = ohlcv
        scored = score_symbol(ohlcv, weights)
        if not scored:
            continue
        persistence, age_minutes = compute_persistence_and_age(history, symbol, scored["direction"], now_ts)
        results.append({"symbol": symbol, "persistence": persistence, "age_minutes": age_minutes, **scored})

    # FAZA CICLULUI ALTCOIN SEASON, evaluata la fiecare scanare pe date reale
    # (CoinGecko + lumanari zilnice de pe exchange). Nu opreste niciodata
    # scanarea: la o problema de date pastreaza ultima evaluare.
    try:
        alt_state = ALTSEASON(exchange, results)
    except Exception as _e:
        print(f"[!] altseason: {_e}")
        alt_state = None

    longs = sorted([r for r in results if r["direction"] == "LONG"], key=lambda r: r["risk_adjusted"], reverse=True)
    shorts = sorted([r for r in results if r["direction"] == "SHORT"], key=lambda r: r["risk_adjusted"], reverse=True)
    best = max(results, key=lambda r: r["risk_adjusted"]) if results else None

    # 3) analiza detaliata (structura + fibonacci + plan SL/TP) pentru
    # cel mai bun candidat, plus datele de grafic pentru dashboard
    deep_analysis = None
    # ---- DETALII PER SIMBOL, pentru dashboard.
    # Se calculeaza din ohlcv_cache, deci ZERO apeluri API in plus - doar CPU.
    # Fisierul se SUPRASCRIE la fiecare rulare, nu se acumuleaza: un instantaneu
    # al starii curente. Daca as fi salvat lumanarile complete pentru toate
    # simbolurile ar fi insemnat ~326 KB pe scanare, comise orar - peste 200 MB
    # pe luna in git. Pastrez in schimb doar indicatorii (cateva numere) si o
    # linie de pret scurta pentru graficul mic.
    details = {}
    # ISTORICUL MASURAT pentru prognoza de pe grafic: rata de castig si durata
    # mediana pe (directie, interval de scor), din planurile inchise ale
    # familiei. Citire separata - planurile se incarca in flux abia mai jos.
    try:
        _hist_tab = plan_tracker.history_table(PLAN_STORE.load_plans())
    except Exception as _e:
        print(f"[!] istoric indisponibil pentru prognoza: {_e}")
        _hist_tab = {}

    for r in results:
        candles = ohlcv_cache.get(r["symbol"])
        if not candles:
            continue
        details[r["symbol"]] = build_symbol_details(r, candles, _hist_tab)
    save_json(DETAILS_FILE, {"scan_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                              "timeframe": CONFIG["timeframe"],
                             "exchange": exchange_id, "symbols": details})

    # SCANARE PER BURSA, doar pentru afisare. Bursa activa refoloseste datele
    # deja descarcate, deci nu costa nimic in plus; celelalte se scaneaza in
    # limita de timp, ca sa nu intinda rularea.
    scans = {}
    if exchange_id:
        scans[exchange_id] = {
            "resolved": sorted(ohlcv_cache.keys()),
            "missing": [b for b in (CONFIG.get("watchlist") or [])
                        if not any(s_.split("/")[0] in
                                   CONFIG.get("aliases", {}).get(b, [b])
                                   for s_ in ohlcv_cache)],
            "results": [{"symbol": r["symbol"], "direction": r["direction"],
                         "score": r["risk_adjusted"], "price": r["price"],
                         "atr": r["atr"], "components": r["components"]}
                        for r in sorted(results, key=lambda r: -r["risk_adjusted"])],
            "details": {k: {"supertrend": (v.get("indicators") or {}).get("supertrend", {}).get("direction"),
                            "vwap": (v.get("indicators") or {}).get("vwap"),
                            "poc": ((v.get("indicators") or {}).get("volume_profile") or {}).get("poc"),
                            "vah": ((v.get("indicators") or {}).get("volume_profile") or {}).get("vah"),
                            "val": ((v.get("indicators") or {}).get("volume_profile") or {}).get("val"),
                            "macd_hist": ((v.get("indicators") or {}).get("macd") or {}).get("histogram"),
                            "position": (v.get("indicators") or {}).get("price_vs_value_area")}
                        for k, v in details.items()},
            "truncated": False, "error": None, "primary": True,
        }

    # ---- PLANURI: creez pentru toate semnalele din top, nu doar pentru cel
    # mai bun. Reutilizez ohlcv_cache, deci in mod normal nu costa apeluri
    # API in plus.
    plan_store = PLAN_STORE.load_plans()

    # 0) migrare automata: inchide planurile ramase din geometrii vechi, ca sa
    # nu blocheze combinatiile simbol+directie. Ruleaza o singura data efectiv.
    migrated = plan_tracker.auto_migrate_geometry(plan_store)
    if migrated:
        print(f"\nMigrare geometrie: am inchis {len(migrated)} planuri vechi "
              f"(deblocheaza simbolurile pentru planuri noi):")
        for pid, sym, direction, prev in migrated[:8]:
            print(f"  PLAN #{pid} {sym} {direction} (era {prev})")
        if len(migrated) > 8:
            print(f"  ... si inca {len(migrated) - 8}")

    if best:
        best_ohlcv = ohlcv_cache[best["symbol"]]
        highs = [c[2] for c in best_ohlcv]
        lows = [c[3] for c in best_ohlcv]
        closes = [c[4] for c in best_ohlcv]

        structure = compute_structure_levels(highs, lows)
        fib = compute_fibonacci(highs, lows)
        plan = compute_trade_plan(best["direction"], best["price"], best["atr"], structure, fib)
        liquidity = fetch_liquidity_levels(exchange, best["symbol"])
        # indicatorii din poze - calculati din OHLCV deja descarcat, zero apeluri API in plus
        inds = indicators.compute_all(best_ohlcv)
        deep_analysis = {"structure": structure, "fibonacci": fib, "plan": plan,
                         "liquidity": liquidity, "indicators": inds}

        n = CONFIG["chart_candles"]
        # GRAFIC IMBOGATIT: tot ce se desena in sistemul de referinta exista deja
        # calculat aici - nivelurile planului, VWAP, POC/VAH/VAL, SuperTrend,
        # MACD, RSI. Pana acum ajungeau doar in carduri de text. Le salvez ca sa
        # poata fi desenate ca linii etichetate peste lumanari.
        best_ind = indicators.compute_all(best_ohlcv)
        best_struct = compute_structure_levels(
            [c[2] for c in best_ohlcv], [c[3] for c in best_ohlcv])
        best_plan = compute_trade_plan(best["direction"], best["price"], best["atr"],
                                       best_struct, fib)
        # Planul INGHETAT pentru acest simbol, daca exista unul deschis. Asta e
        # distinctia "LOCKED vs CURRENT" din poze: ce s-a decis atunci, langa
        # ce ar rezulta acum.
        locked = None
        for pl_ in reversed(plan_store.get("plans") or []):
            if (pl_.get("symbol") == best["symbol"]
                    and pl_.get("state") in (plan_tracker.STATE_PENDING,
                                             plan_tracker.STATE_OPEN,
                                             plan_tracker.STATE_TP1)):
                locked = {"id": pl_["id"], "direction": pl_["direction"],
                          "entry": pl_["entry"], "sl": pl_["sl"],
                          "tp1": pl_["tp1"], "tp2": pl_["tp2"],
                          "state": pl_.get("state_detail") or pl_.get("state")}
                break

        def _tail(series):
            return [None if v is None else round_price(v) for v in series[-n:]]

        rsi_series = []
        for i in range(len(closes)):
            rsi_series.append(rsi(closes[:i + 1], 14) if i >= 14 else None)

        # Largesc fereastra pana la primul punct al numaratorii principale,
        # plus o marja, marginit la cate bare exista si la un maxim rezonabil.
        _ew_full = ew_mod.analyze([c[2] for c in best_ohlcv],
                                  [c[3] for c in best_ohlcv], closes, best["price"])
        _pri = _ew_full.get("primary") or {}
        _idxs = [pt.get("idx") for pt in (_pri.get("points") or [])
                 if pt.get("idx") is not None]
        if _idxs:
            _need = len(best_ohlcv) - min(_idxs) + 12
            n = max(n, min(_need, len(best_ohlcv), 260))

        save_json(CHART_FILE, {
            "symbol": best["symbol"],
            "direction": best["direction"],
            "candles": best_ohlcv[-n:],
            "ema9": _tail(ema_series_full(closes, 9)),
            "ema20": _tail(ema_series_full(closes, 20)),
            "ema50": _tail(ema_series_full(closes, 50)),
            "ema200": _tail(ema_series_full(closes, 200)),
            "rsi": _tail(rsi_series),
            "macd": best_ind.get("macd"),
            "indicators": {
                "vwap": best_ind.get("vwap"),
                "poc": (best_ind.get("volume_profile") or {}).get("poc"),
                "vah": (best_ind.get("volume_profile") or {}).get("vah"),
                "val": (best_ind.get("volume_profile") or {}).get("val"),
                "supertrend": best_ind.get("supertrend"),
            },
            "current": best_plan,
            "locked": locked,
            "forecast": ew_mod.forecast_path(
                (_ew_full or {}).get("primary"), [c[2] for c in best_ohlcv],
                [c[3] for c in best_ohlcv], closes, plan=best_plan,
                hist=_hist_tab.get(f"{best['direction']}:{int(best['risk_adjusted'] // 20) * 20}"),
                plan_dir=best["direction"], agg_bias=ew_mod.bias(_ew_full, best["direction"])),
            # ELLIOTT pentru grafic: punctele undelor cu indicii lor de bara,
            # ca sa poata fi desenate exact peste lumanarile corespunzatoare.
            # `idx` e pozitia in seria COMPLETA, iar graficul afiseaza doar
            # ultimele n bare - deci offset-ul se aplica la desenare.
            # Fereastra graficului se LARGESTE ca sa cuprinda structura Elliott
            # principala. Altfel primele unde (MAJOR START, W1, W2) cad in afara
            # ferestrei si se vad doar ultimele doua puncte - inutil, fiindca
            # tocmai relatia dintre unde e informatia.
            "elliott": (lambda r: {
                "counts": r.get("counts", [])[:3],
                "primary": r.get("primary"),
                "alive": r.get("alive"), "total": r.get("total"),
                "offset": max(0, len(best_ohlcv) - n)})(_ew_full),
            # Clusterele de lichidare pentru graficul principal: se deseneaza ca
            # benzi orizontale cu intensitate, echivalentul vizual al heatmap-ului.
            # Lichiditatea structurala lipsea din graficul principal - ajungea
            # doar in detaliile per token, deci componenta "Lichiditate" a
            # graficului principal era mereu goala.
            "liq_structure": ls_mod.build([c[2] for c in best_ohlcv],
                                          [c[3] for c in best_ohlcv], closes,
                                          best["atr"], best["price"],
                                          CONFIG["timeframe"]),
            "liquidation": (lambda mp: {
                "clusters": mp.get("clusters", [])[:16],
                "above": mp.get("above"), "below": mp.get("below"),
                "bias": liq_mod.magnet_bias(mp, best["price"], best["direction"]),
                "oi_scaled": mp.get("oi_scaled")})(
                    liq_mod.build_map(best_ohlcv, best["price"])),
            "structure": best_struct,
            "timeframe": CONFIG["timeframe"],
        })

    # 1) evaluez planurile deschise pe lumanarile proaspete
    closed_now = []
    for p in plan_store["plans"]:
        if p["state"] in plan_tracker.CLOSED_STATES:
            continue
        candles = ohlcv_cache.get(p["symbol"])
        if not candles:
            # Planul e "orfan": simbolul a iesit din top-200 sau din universul
            # scanat. Fara asta ar ramane OPEN la nesfarsit si nu s-ar invata
            # niciodata din el. Descarc explicit - sunt putine cazuri.
            try:
                candles = exchange.fetch_ohlcv(
                    p["symbol"], timeframe=CONFIG["timeframe"], limit=CONFIG["candles"])
                ohlcv_cache[p["symbol"]] = candles
                print(f"  (plan orfan #{p['id']} {p['symbol']}: descarcat separat)")
            except Exception as e:
                print(f"  [!] plan orfan #{p['id']} {p['symbol']}: {e}")
                continue
        if plan_tracker.evaluate_plan(p, candles) and p["state"] in plan_tracker.CLOSED_STATES:
            closed_now.append(p)

    # 2) recalibrez ACUM, dupa evaluare - deciziile de mai jos trebuie sa
    # foloseasca si rezultatele inchise chiar in aceasta rulare, nu date vechi
    calibration = plan_tracker.build_calibration(plan_store)

    # 3) decid daca deschid planuri noi - cu contributia agentului daca e ACTIVE
    agent_model, agent_state = AGENT_STORE.load_agent()
    closed_for_neighbors = [p for p in plan_store["plans"]
                            if p.get("realized_r") is not None
                            and plan_tracker.same_family(p.get("geometry"))
                            and p.get("state") != plan_tracker.STATE_NO_ENTRY]
    issued, skipped = [], []
    decisions_out = {}          # decizia pentru fiecare semnal - citita de dashboard
    proposals_out = {}          # propunerea completa (evidente, vecini) - modulul bursei
    for sig in (longs[: CONFIG["top_n_per_direction"]] + shorts[: CONFIG["top_n_per_direction"]]):
        if plan_tracker.has_open_plan(plan_store, sig["symbol"], sig["direction"]):
            proposals_out[sig["symbol"]] = {"symbol": sig["symbol"], "direction": sig["direction"],
                                            "score": sig.get("risk_adjusted"), "open_plan": True,
                                            "trained": True}
            continue
        candles = ohlcv_cache.get(sig["symbol"])
        if not candles:
            continue
        # order book-ul ACESTUI simbol (vezi PER_SIGNAL_ORDER_BOOK)
        book_levels = liquidity
        if PER_SIGNAL_ORDER_BOOK and sig["symbol"] != best["symbol"]:
            book_levels = fetch_liquidity_levels(exchange, sig["symbol"])
        levels, sig = build_signal_context(sig, candles, exchange, active_caps, book_levels, alt_state)
        if not levels:
            # geometrie degenerata (ex. ATR efectiv zero) - sar peste simbol,
            # nu opresc scanarea din cauza unuia singur
            print(f"  [!] {sig['symbol']}: niveluri invalide, sar peste")
            continue

        # Verdictul planurilor comparabile: se calculeaza din memoria acumulata
        # si se salveaza pe plan, ca sa apara in dashboard exact asa cum a fost
        # la momentul deciziei - nu recalculat mai tarziu, cu alte date.
        sig["neighbors"] = ai_agent.comparable_entries(
            ai_agent.extract_features(sig), closed_for_neighbors)
        agent_pred = ai_agent.predict_for_signal(agent_model, agent_state, sig)
        sig = {**sig, "bar_seconds": timeframe_seconds()}
        decision = plan_tracker.decide(calibration, sig, agent_pred)
        decisions_out[sig["symbol"]] = {
            "action": decision["action"], "mode": decision.get("mode"),
            "reason": decision.get("reason"),
            "calibrated_prob": decision.get("calibrated_prob"),
            "agent_prob": decision.get("agent_prob"),
            "elliott_bias": (sig.get("elliott_conflict") or {}).get("bias"),
            "next_entry": (sig.get("elliott_conflict") or {}).get("next_entry")}
        proposals_out[sig["symbol"]] = proposal_record(sig, levels, decision, agent_pred, trained=True)
        if decision["action"] == "SKIP":
            skipped.append((sig["symbol"], decision["reason"]))
            continue
        new_plan = plan_tracker.create_plan(plan_store, sig, levels, decision)
        if new_plan:
            issued.append(new_plan)

    save_json(os.path.join(os.path.dirname(DETAILS_FILE), "decisions.json"),
              {"scan_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
               "decisions": decisions_out})

    save_json_compact(os.path.join(EXCHANGES_DIR, exchange_id, "proposals.json"),
                      {"scan_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                       "exchange": exchange_id, "trained": True, "learning_exchange": exchange_id,
                       "proposals": proposals_out})

    # 4) salvez calibrarea finala si rezumatul
    plan_store["calibration"] = plan_tracker.build_calibration(plan_store)
    plan_store["summary"] = plan_tracker.summarize(plan_store)
    PLAN_STORE.save_plans(plan_store)

    print(f"\nPlanuri: {len(issued)} deschise, {len(skipped)} refuzate, "
          f"{len(closed_now)} inchise in aceasta rulare")
    for p in closed_now:
        print(f"  PLAN #{p['id']} {p['symbol']} {p['direction']}: "
              f"{p['state_detail']} -> {p['realized_r']:+.2f}R")
    for sym, reason in skipped:
        print(f"  REFUZAT {sym}: {reason}")
    plan_tracker.print_summary(plan_store)

    scan_record = {
        "scan_id_ts": now_ts,
        "scan_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "universe_size": len(universe),
        "results": results,
        "top_long": longs[: CONFIG["top_n_per_direction"]],
        "top_short": shorts[: CONFIG["top_n_per_direction"]],
        "best_candidate": best,
        "deep_analysis": deep_analysis,
        "evaluated": False,
    }
    history.append(scan_record)

    save_json(WEIGHTS_FILE, weights)
    save_json(HISTORY_FILE, history)

    weights_history = load_json(WEIGHTS_HISTORY_FILE, [])
    weights_history.append({"ts": now_ts, "time": scan_record["scan_time"], **weights})
    save_json(WEIGHTS_HISTORY_FILE, weights_history)

    msg = format_message(scan_record)
    print(msg)
    NOTIFIER(CONFIG["telegram_bot_token"], CONFIG["telegram_chat_id"], msg)

    # 5) MODULELE PER BURSA: analiza completa pe fiecare bursa secundara, in paralel,
    # DUPA ce tot ce tine de bursa activa e salvat - o bursa lenta sau cazuta nu
    # poate afecta planurile, invatarea sau datele bursei active.
    try:
        mods = run_exchange_modules(exchange_cards, exchange_handles, exchange_id, {
            "scope": coingecko_scope, "weights": weights, "hist_tab": _hist_tab,
            "alt_state": alt_state, "calibration": calibration, "agent_model": agent_model,
            "agent_state": agent_state, "closed": closed_for_neighbors, "plan_store": plan_store,
            "now_ts": now_ts, "budget": EXCHANGE_TIME_BUDGET,
            "limit": max(SECONDARY_SCAN_LIMIT, len(CONFIG.get("watchlist") or []))})
    except Exception as _e:
        print(f"[!] modulele per bursa: {_e}")
        mods = {}
    scans.update(mods)
    save_json(EXCHANGE_SCANS_FILE, {"scans": scans, "primary": exchange_id,
                                    "timeframe": CONFIG["timeframe"]})
    print("Ruleaza si generate_dashboard.py ca sa actualizezi docs/index.html")
