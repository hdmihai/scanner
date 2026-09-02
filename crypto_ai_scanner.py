#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
crypto_ai_scanner.py
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

import ccxt
import plan_tracker
import indicators
import json
import os
import time
import requests
from datetime import datetime, timezone

# ============================== CONFIG ==============================
CONFIG = {
    "exchange_fallback": ["okx", "kucoin", "gateio", "mexc", "kraken"],
    # Incearca pe rand, primul care raspunde e folosit - vezi connect_exchange().
    # Kraken primul (serveste SUA, nu are motiv sa geo-blocheze); restul sunt
    # rezerve. NU mai modifica sursa fisierului la runtime (spre deosebire de
    # run_scanner_with_exchange_fallback.py, care trebuie sters - vezi nota
    # din raspuns).
    "quotes": ["USDT", "USDC", "USD"],
    # Mai multe monede de cotare, nu doar USDT: pe Kraken aproape totul e cotat
    # in USD (doar 44 perechi USDT din 1440 de piete), motiv pentru care prima
    # rulare reala a gasit doar 34 de simboluri. Deduplicate pe simbolul de baza.
    "min_universe": 120,       # sub atat, incerc urmatorul exchange din lista
    "universe_size": 200,      # cate simboluri intra efectiv in scanare (scor + persistenta)
    "coingecko_scope": 200,    # doar proiectele din top N CoinGecko dupa market cap sunt eligibile
    "timeframe": "1h",
    "candles": 200,
    "lookahead_hours": 24,     # dupa cate ore evaluam daca un semnal a "nimerit"
    "hit_threshold_atr": 0.5,  # miscare minima (in ATR-uri) ca sa conteze "hit"
    "top_n_per_direction": 5,
    "chart_candles": 80,       # cate lumanari pastram pentru graficul din dashboard
    "telegram_bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", "PUNE_AICI_TOKEN_DE_LA_BOTFATHER"),
    "telegram_chat_id": os.environ.get("TELEGRAM_CHAT_ID", "PUNE_AICI_CHAT_ID_UL_TAU"),
    "data_dir": "data",
}

HISTORY_FILE = os.path.join(CONFIG["data_dir"], "scan_history.json")
WEIGHTS_FILE = os.path.join(CONFIG["data_dir"], "weights.json")
WEIGHTS_HISTORY_FILE = os.path.join(CONFIG["data_dir"], "weights_history.json")
CHART_FILE = os.path.join(CONFIG["data_dir"], "latest_chart.json")

DEFAULT_WEIGHTS = {"trend": 1.0, "momentum": 1.0, "volatility": 1.0, "volume": 1.0}


# ============================ INDICATORI =============================
# Implementati simplu, in Python pur, fara pandas/numpy - ca sa mearga
# usor si pe un VPS minimal sau pe telefon (Termux).

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


# ==================== STRUCTURA, FIBONACCI, PLAN DE TRADE =================
# Adaugate ca sa acopere sectiunile "AI Plan" / "Market Structure" /
# "Fibonacci" din dashboard-ul de referinta. Sunt euristici transparente,
# nu o reconstructie a vreunui produs anume - le poti inlocui oricand cu
# propria ta logica din AI_Dashboard_v10.pine.

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
    resistance = sorted(set(round(h, 6) for h in swing_highs[-12:]), reverse=True)[:n_levels]
    support = sorted(set(round(l, 6) for l in swing_lows[-12:]), reverse=True)[-n_levels:]
    return {"resistance": resistance, "support": support}


def compute_fibonacci(highs, lows, lookback=100):
    """Retracement + extensie Fibonacci pe ultimul swing major (high/low) din fereastra de lookback."""
    swing_high = max(highs[-lookback:])
    swing_low = min(lows[-lookback:])
    diff = swing_high - swing_low
    retracement = {str(r): round(swing_high - diff * r, 6) for r in (0.236, 0.382, 0.5, 0.618, 0.786)}
    extension = {str(r): round(swing_high + diff * (r - 1), 6) for r in (1.272, 1.618, 2.0)}
    return {"swing_high": swing_high, "swing_low": swing_low, "retracement": retracement, "extension": extension}


def compute_trade_plan(direction, price, atr_val, structure, fib):
    """Plan de tranzactionare: SL pe baza de ATR, TP1 la prima structura relevanta, TP2 la extensia Fibonacci 1.618 (echivalentul casetei
    AI PLAN: entry/SL/TP1/TP2 din poza ta)."""
    ext_1618 = fib["extension"]["1.618"]
    if direction == "LONG":
        sl = price - atr_val * 1.5
        above = [r for r in structure["resistance"] if r > price]
        tp1 = min(above) if above else price + atr_val * 2
        tp2 = ext_1618 if ext_1618 > tp1 else price + atr_val * 4
    else:
        sl = price + atr_val * 1.5
        below = [s for s in structure["support"] if s < price]
        tp1 = max(below) if below else price - atr_val * 2
        tp2 = ext_1618 if ext_1618 < tp1 else price - atr_val * 4

    risk = abs(price - sl)
    reward = abs(tp2 - price)
    expected_r = round(reward / risk, 2) if risk > 0 else None
    return {
        "entry": round(price, 6), "sl": round(sl, 6),
        "tp1": round(tp1, 6), "tp2": round(tp2, 6),
        "expected_r": expected_r,
    }


# =============================== SCOR =================================

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

    if trend_up and 45 <= r <= 75:
        direction = "LONG"
        momentum_strength = min((r - 45) / 30, 1.0)
    elif (not trend_up) and 25 <= r <= 55:
        direction = "SHORT"
        momentum_strength = min((55 - r) / 30, 1.0)
    else:
        return None  # fara semnal clar in acest moment

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
        "atr": round(a, 6),
    }


# ============================ LICHIDITATE (SMC) ===========================
# Nivelurile de lichiditate (cele mai mari cluster-e bid/ask din order book)
# sunt un concept central in Smart Money Concepts: zone unde e probabil sa
# reactioneze pretul, pentru ca acolo sta volumul mare de ordine.

def fetch_liquidity_levels(exchange, symbol, depth=50, top_n=3):
    """BUG FIX: nu despachetez cu `for p, a in bids`. Standardul ccxt e
    [pret, cantitate], dar unele exchange-uri adauga un al treilea camp -
    Kraken pune si timestamp-ul nivelului, ceea ce arunca
    "ValueError: too many values to unpack (expected 2)". Iau explicit
    primele doua elemente si ignor restul, indiferent de exchange."""
    try:
        ob = exchange.fetch_order_book(symbol, limit=depth)
    except Exception as e:
        print(f"[!] order book {symbol}: {e}")
        return None

    def normalize(levels):
        out = []
        for lvl in levels or []:
            if not lvl or len(lvl) < 2:
                continue
            try:
                price, amount = float(lvl[0]), float(lvl[1])
            except (TypeError, ValueError):
                continue
            out.append({"price": round(price, 6), "amount": round(amount, 4)})
        return sorted(out, key=lambda d: d["amount"], reverse=True)[:top_n]

    return {"bids": normalize(ob.get("bids")), "asks": normalize(ob.get("asks"))}


def fetch_coingecko_top_symbols(top_n=200):
    """Simbolurile din top N CoinGecko dupa market cap (nu dupa volum de
    schimb) - folosite ca sa restrangem universul la proiecte relevante
    fundamental, nu doar la ce are volum mare pe termen scurt."""
    symbols = set()
    per_page = 250
    pages = (top_n + per_page - 1) // per_page
    for page in range(1, pages + 1):
        url = (f"https://api.coingecko.com/api/v3/coins/markets"
               f"?vs_currency=usd&order=market_cap_desc&per_page={per_page}&page={page}")
        try:
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            for coin in resp.json():
                symbols.add(coin["symbol"].upper())
        except Exception as e:
            print(f"[!] CoinGecko top-{top_n} (pagina {page}): {e}")
            break
    return symbols


# =========================== PERSISTENTA JSON ==========================

def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r") as f:
        return json.load(f)


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def compute_persistence_and_age(history, symbol, direction, now_ts):
    """Cate scanari recente consecutive au avut acelasi symbol+directie si
    de cand (in minute) e activa aceasta directie pentru acest simbol."""
    streak = 0
    first_ts = now_ts
    for scan in reversed(history):
        match = next((r for r in scan["results"] if r["symbol"] == symbol), None)
        if match and match["direction"] == direction:
            streak += 1
            first_ts = scan["scan_id_ts"]
        else:
            break
    age_minutes = round((now_ts - first_ts) / 60)
    return streak, age_minutes


def evaluate_and_learn(history, weights, tickers, lookahead_hours, hit_threshold_atr):
    """'Invatare' simpla: verifica semnalele mai vechi decat lookahead_hours,
    vede daca pretul s-a miscat in directia prezisa, si ajusteaza usor
    ponderile componentelor care au dat rezultate bune/proaste."""
    now_ts = time.time()
    lookahead_sec = lookahead_hours * 3600
    feedback = {k: [] for k in weights}

    for scan in history:
        if scan.get("evaluated") or now_ts - scan["scan_id_ts"] < lookahead_sec:
            continue
        for r in scan["results"]:
            price_now = tickers.get(r["symbol"], {}).get("last")
            if price_now is None:
                continue
            move = (price_now - r["price"]) if r["direction"] == "LONG" else (r["price"] - price_now)
            hit = move >= r["atr"] * hit_threshold_atr
            r["outcome"] = "hit" if hit else "miss"
            for comp, val in r["components"].items():
                if val > 0.6:
                    feedback[comp].append(1 if hit else -1)
        scan["evaluated"] = True

    for comp, fb in feedback.items():
        if not fb:
            continue
        avg = sum(fb) / len(fb)
        weights[comp] = round(max(0.3, min(2.0, weights[comp] * (1 + avg * 0.05))), 4)

    return weights


# ============================== TELEGRAM ================================

def send_telegram(token, chat_id, text):
    if "PUNE_AICI" in token or "PUNE_AICI" in chat_id:
        print("[!] Configureaza telegram_bot_token si telegram_chat_id in CONFIG "
              "ca sa primesti rezultatele pe telefon.")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        requests.post(
            url,
            data={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        print(f"[!] Eroare trimitere Telegram: {e}")


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

    parts = [f"ðŸ“Š *Scan {scan['scan_time']}* â€” universe {scan['universe_size']}"]
    parts.append("\nðŸŸ¢ *TOP LONG*")
    parts.append(table(scan["top_long"]))
    parts.append("\nðŸ”´ *TOP SHORT*")
    parts.append(table(scan["top_short"]))
    if scan.get("best_candidate"):
        b = scan["best_candidate"]
        parts.append(
            f"\nâ­ *Best candidate:* {b['symbol']} {b['direction']} Â· "
            f"conf {b['probability']}% Â· exp {b['expected_r']}R"
        )
    return "\n".join(parts)


# ================================ MAIN ===================================

def build_eligible_pairs(markets, tickers, scope):
    """Construieste lista de perechi eligibile, cu doua imbunatatiri fata de
    varianta initiala:

    1. MAI MULTE MONEDE DE COTARE. Prima rulare reala a gasit doar 34 de
       simboluri pe Kraken, pentru ca acolo aproape totul e cotat in USD, nu
       USDT (44 perechi USDT din 1440 de piete). Accept acum USDT/USDC/USD.
    2. DEDUPLICARE PE SIMBOL DE BAZA. BTC/USDT si BTC/USD sunt acelasi
       proiect - pastrez varianta cu volum mai mare, ca sa nu apara de doua
       ori in universul scanat si sa umfle artificial numararea."""
    by_base = {}
    for symbol, m in markets.items():
        if not m.get("active", True):
            continue
        parts = symbol.split("/")
        if len(parts) != 2:
            continue
        base, quote = parts[0].upper(), parts[1].upper()
        if quote not in CONFIG["quotes"]:
            continue
        if scope and base not in scope:
            continue
        vol = tickers.get(symbol, {}).get("quoteVolume", 0) or 0
        prev = by_base.get(base)
        if prev is None or vol > prev[1]:
            by_base[base] = (symbol, vol)

    pairs = sorted(by_base.values(), key=lambda t: t[1], reverse=True)
    return [symbol for symbol, _ in pairs]


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
        exchange_class = getattr(ccxt, exchange_id, None)
        if exchange_class is None:
            print(f"[!] '{exchange_id}' nu exista in ccxt, sar peste.")
            continue
        exchange = exchange_class({"enableRateLimit": True})
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
    coingecko_scope = fetch_coingecko_top_symbols(CONFIG["coingecko_scope"])
    if not coingecko_scope:
        print("[!] Nu am putut lua lista CoinGecko - continui fara filtrul de scope.")

    exchange, markets, all_tickers, eligible, exchange_id = connect_exchange(coingecko_scope)
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
        ohlcv_cache[symbol] = ohlcv
        scored = score_symbol(ohlcv, weights)
        if not scored:
            continue
        persistence, age_minutes = compute_persistence_and_age(history, symbol, scored["direction"], now_ts)
        results.append({"symbol": symbol, "persistence": persistence, "age_minutes": age_minutes, **scored})

    longs = sorted([r for r in results if r["direction"] == "LONG"], key=lambda r: r["risk_adjusted"], reverse=True)
    shorts = sorted([r for r in results if r["direction"] == "SHORT"], key=lambda r: r["risk_adjusted"], reverse=True)
    best = max(results, key=lambda r: r["risk_adjusted"]) if results else None

    # 3) analiza detaliata (structura + fibonacci + plan SL/TP) pentru
    # cel mai bun candidat, plus datele de grafic pentru dashboard
    deep_analysis = None
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
        save_json(CHART_FILE, {
            "symbol": best["symbol"],
            "direction": best["direction"],
            "candles": best_ohlcv[-n:],
            "ema20": ema_series_full(closes, 20)[-n:],
            "ema50": ema_series_full(closes, 50)[-n:],
        })

    # ---- PLANURI: creez pentru toate semnalele din top, nu doar pentru cel
    # mai bun. Reutilizez ohlcv_cache, deci in mod normal nu costa apeluri
    # API in plus.
    plan_store = plan_tracker.load_plans()

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

    # 3) decid daca deschid planuri noi
    issued, skipped = [], []
    for sig in (longs[: CONFIG["top_n_per_direction"]] + shorts[: CONFIG["top_n_per_direction"]]):
        if plan_tracker.has_open_plan(plan_store, sig["symbol"], sig["direction"]):
            continue
        candles = ohlcv_cache.get(sig["symbol"])
        if not candles:
            continue
        highs_s = [c[2] for c in candles]
        lows_s = [c[3] for c in candles]
        struct_s = compute_structure_levels(highs_s, lows_s)
        fib_s = compute_fibonacci(highs_s, lows_s)
        levels = compute_trade_plan(sig["direction"], sig["price"], sig["atr"], struct_s, fib_s)

        decision = plan_tracker.decide(calibration, sig)
        if decision["action"] == "SKIP":
            skipped.append((sig["symbol"], decision["reason"]))
            continue
        new_plan = plan_tracker.create_plan(plan_store, sig, levels, decision)
        if new_plan:
            issued.append(new_plan)

    # 4) salvez calibrarea finala si rezumatul
    plan_store["calibration"] = plan_tracker.build_calibration(plan_store)
    plan_store["summary"] = plan_tracker.summarize(plan_store)
    plan_tracker.save_plans(plan_store)

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
    send_telegram(CONFIG["telegram_bot_token"], CONFIG["telegram_chat_id"], msg)
    print("Ruleaza si generate_dashboard.py ca sa actualizezi docs/index.html")


if __name__ == "__main__":
    main()
