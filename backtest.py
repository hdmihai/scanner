#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest.py
============
Reia istoricul bara cu bara si genereaza planuri inchise cu R real, ca sa nu
mai astepti saptamani ca agentul sa adune date.

DE CE E ASTA CEL MAI IMPORTANT PAS
----------------------------------
Agentul are nevoie de 300 de planuri inchise SI de 21 de zile calendaristice
ca sa devina ACTIVE. Pragul de zile exista pentru ca datele stranse intr-o
fereastra scurta memoreaza un singur regim de piata - am vazut concret asta:
634 de exemple din 41 de ore aratau 76% acuratete, dar era doar "piata a
scazut, shorturile au castigat".

Backtest-ul satisface pragul LEGITIM, nu il ocoleste: 6 luni de lumanari
istorice contin efectiv 6 luni calendaristice, cu cresteri, scaderi si lateral.

DOUA REGULI DE CORECTITUDINE
----------------------------
1. FARA LOOK-AHEAD. La bara i, folosesc strict candles[:i+1]. Niciun calcul nu
   vede vreodata viitorul. Asta e greseala care face backtest-urile sa arate
   spectaculos si sa esueze in realitate.
2. ACELEASI FUNCTII CA LIVE. Import score_symbol, compute_trade_plan si
   evaluate_plan din modulele reale. Daca as rescrie logica aici, as testa alt
   cod decat cel care ruleaza - iar rezultatele n-ar insemna nimic.

CE NU MODELEAZA (limite oneste)
-------------------------------
- fara slippage si fara comisioane: R-ul real ar fi ceva mai mic
- fara order book, deci fara nivelurile de lichiditate
- presupune ca poti intra exact la pretul de inchidere al barei de semnal
- supravietuire: lista de simboluri e cea de azi, nu cea de acum 6 luni
- in live se deschid planuri doar pentru top 5 long + top 5 short dintr-o
  scanare; aici se deschide pentru orice semnal valid. Cu o watchlist de 7
  simboluri diferenta e neglijabila (aproape tot ar intra oricum in top 5),
  dar pe un univers de 200 backtest-ul ar fi mai permisiv decat realitatea.

RULARE
------
    python3 backtest.py                  # 180 de zile, watchlist-ul din CONFIG
    python3 backtest.py --days 90
    python3 backtest.py --sweep          # compara variante de intrare pe aceleasi date
    python3 backtest.py --merge          # adauga rezultatele in data/plans.json
                                         # ca agentul sa invete din ele
"""

import json
import os
import sys
import time

import ccxt

import crypto_ai_scanner as scanner
import plan_tracker

DATA_DIR = "data"
BACKTEST_FILE = os.path.join(DATA_DIR, "backtest_plans.json")
PLANS_FILE = os.path.join(DATA_DIR, "plans.json")

DEFAULT_DAYS = 180
WARMUP_BARS = 200      # cate bare are nevoie score_symbol ca sa fie valid


def save_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


# ========================== DESCARCARE ISTORIC ==============================

def fetch_history(exchange, symbol, timeframe, days):
    """Descarca istoricul paginat. Un singur apel ccxt intoarce cel mult
    ~500-1500 de lumanari, deci pentru luni intregi trebuie paginat cu `since`."""
    ms_per_bar = exchange.parse_timeframe(timeframe) * 1000
    since = exchange.milliseconds() - days * 86400 * 1000
    out = []
    while True:
        try:
            batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=1000)
        except Exception as e:
            print(f"  [!] {symbol}: {e}")
            break
        if not batch:
            break
        out.extend(batch)
        if len(batch) < 2:
            break
        next_since = batch[-1][0] + ms_per_bar
        if next_since <= since:
            break
        since = next_since
        if batch[-1][0] >= exchange.milliseconds() - ms_per_bar:
            break
        time.sleep(exchange.rateLimit / 1000)

    # deduplic dupa timestamp si sortez, ca paginarea poate suprapune batch-uri
    seen = {}
    for c in out:
        seen[c[0]] = c
    return [seen[k] for k in sorted(seen)]


def resolve_symbols(exchange, markets, tickers):
    """Traduce watchlist-ul in perechi reale de pe bursa, tinand cont de aliasuri."""
    watchlist = scanner.CONFIG.get("watchlist") or []
    if not watchlist:
        print("[!] CONFIG['watchlist'] e goala - backtest-ul are nevoie de o lista explicita.")
        return {}

    resolved = {}
    for base in watchlist:
        candidates = scanner.CONFIG.get("aliases", {}).get(base, [base])
        best, best_vol = None, -1
        for alias in candidates:
            for quote in scanner.CONFIG["quotes"]:
                sym = f"{alias}/{quote}"
                if sym in markets and markets[sym].get("active", True):
                    vol = tickers.get(sym, {}).get("quoteVolume", 0) or 0
                    if vol > best_vol:
                        best, best_vol = sym, vol
        if best:
            resolved[base] = best
        else:
            print(f"  [!] {base}: nicio pereche gasita (aliasuri incercate: {candidates})")
    return resolved


# ============================== REPLAY ======================================

def replay_symbol(symbol, candles, weights, start_id):
    """Parcurge istoricul bara cu bara. La fiecare bara vede STRICT trecutul."""
    plans = []
    open_plan = None
    next_id = start_id

    for i in range(WARMUP_BARS, len(candles)):
        window = candles[:i + 1]          # <- fara look-ahead: nimic dupa bara i
        bar = candles[i]
        bar_ts = bar[0] / 1000.0

        # 1) evaluez planul deschis pe bara curenta
        if open_plan is not None:
            plan_tracker.evaluate_plan(open_plan, [bar])
            if open_plan["state"] in plan_tracker.CLOSED_STATES:
                plans.append(open_plan)
                open_plan = None

        if open_plan is not None:
            continue  # un singur plan activ per simbol, ca in live (has_open_plan)

        # 2) caut semnal cu exact aceeasi functie ca in live
        scored = scanner.score_symbol(window, weights)
        if not scored:
            continue

        highs = [c[2] for c in window]
        lows = [c[3] for c in window]
        structure = scanner.compute_structure_levels(highs, lows)
        fib = scanner.compute_fibonacci(highs, lows)
        levels = scanner.compute_trade_plan(
            scored["direction"], scored["price"], scored["atr"], structure, fib)
        if not levels:
            continue

        risk = abs(levels["entry"] - levels["sl"])
        if risk <= 0:
            continue

        open_plan = {
            "id": next_id,
            "symbol": symbol,
            "direction": scored["direction"],
            "created_ts": bar_ts,
            "created_time": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(bar_ts)),
            "entry": levels["entry"], "sl": levels["sl"],
            "tp1": levels["tp1"], "tp2": levels["tp2"],
            "risk": risk,
            "planned_r_tp2": round(abs(levels["tp2"] - levels["entry"]) / risk, 2),
            "state": plan_tracker.STATE_OPEN,
            "state_detail": "OPEN",
            "realized_r": None, "closed_ts": None, "bars_checked": 0,
            "score_at_entry": scored["risk_adjusted"],
            "components": scored["components"],
            "persistence_at_entry": 0,
            "decision": {"action": "ISSUE", "mode": "BACKTEST",
                         "reason": "replay istoric, fara poarta de decizie"},
            "geometry": plan_tracker.GEOMETRY_VERSION,
            "source": "backtest",
        }
        next_id += 1

    return plans, next_id


# ============================ VARIANTE DE INTRARE ===========================
# Diagnostic pe planurile reale: LONG castiga 15.4%, SHORT 14.3% - ambele
# directii pierdeau simetric, deci problema nu e regimul de piata ci intrarea.
# Simularea a aratat ca managementul pozitiei (50% la TP1 vs 100% la TP2)
# schimba rezultatul cu 0.003R - neglijabil. Semnalul de intrare e tot.
# Variantele de mai jos se testeaza pe date REALE, ca sa decida evidenta.

VARIANTS = {
    "actual": {},
    "rsi_strans": {"rsi_long": (50, 65), "rsi_short": (35, 50)},
    "rsi_larg": {"rsi_long": (40, 80), "rsi_short": (20, 60)},
    "contra_trend": {"reverse": True},          # diagnostic: are semnalul avantaj invers?
    "sl_larg": {"sl_atr": 2.5},
    "sl_strans": {"sl_atr": 1.0},
    "fara_pullback": {"pullback": 0.0},
    "pullback_mare": {"pullback": 1.0},
}


# Cost pe tranzactie, exprimat ca procent din pret (dus-intors: taxe + spread).
# ESENTIAL pentru o comparatie corecta: fara el, sweep-ul favorizeaza mereu
# stopurile minuscule, pentru ca R se calculeaza impartind la risc - un risc mai
# mic umfla R fara niciun avantaj real. Costul in R = cost_pct / risc_pct, deci
# penalizeaza exact configuratiile care par bune doar prin micsorarea numitorului.
ROUNDTRIP_COST_PCT = 0.001   # 0.1% dus-intors, tipic pe spot


def cost_in_r(plan):
    risk_pct = abs(plan["entry"] - plan["sl"]) / plan["entry"] if plan.get("entry") else 0
    return ROUNDTRIP_COST_PCT / risk_pct if risk_pct > 0 else 0


def apply_variant(cfg):
    """Aplica temporar o varianta peste parametrii globali."""
    saved = {
        "pullback": scanner.PULLBACK_ATR, "sl_atr": scanner.SL_ATR,
        "rsi_long": scanner.RSI_LONG, "rsi_short": scanner.RSI_SHORT,
        "reverse": scanner.REVERSE_SIGNAL, "tp1_frac": plan_tracker.TP1_FRACTION,
    }
    scanner.PULLBACK_ATR = cfg.get("pullback", saved["pullback"])
    scanner.SL_ATR = cfg.get("sl_atr", saved["sl_atr"])
    scanner.RSI_LONG = cfg.get("rsi_long", saved["rsi_long"])
    scanner.RSI_SHORT = cfg.get("rsi_short", saved["rsi_short"])
    scanner.REVERSE_SIGNAL = cfg.get("reverse", saved["reverse"])
    plan_tracker.TP1_FRACTION = cfg.get("tp1_frac", saved["tp1_frac"])
    return saved


def restore_variant(saved):
    scanner.PULLBACK_ATR = saved["pullback"]
    scanner.SL_ATR = saved["sl_atr"]
    scanner.RSI_LONG = saved["rsi_long"]
    scanner.RSI_SHORT = saved["rsi_short"]
    scanner.REVERSE_SIGNAL = saved["reverse"]
    plan_tracker.TP1_FRACTION = saved["tp1_frac"]


def run_sweep(symbols, histories, weights):
    """Ruleaza fiecare varianta pe aceleasi date si compara. Aceleasi lumanari
    pentru toate variantele, deci diferentele vin doar din configuratie."""
    print("\n" + "=" * 74)
    print("SWEEP DE VARIANTE - aceleasi date, configuratii diferite")
    print("=" * 74)
    print(f"{'varianta':18s} {'inchise':>8s} {'castig%':>9s} {'R brut':>9s} {'R net':>9s} {'PF net':>7s}")
    print("-" * 74)

    rows = []
    for name, cfg in VARIANTS.items():
        saved = apply_variant(cfg)
        allp, nid = [], 1
        for sym, candles in histories.items():
            pl, nid = replay_symbol(sym, candles, weights, nid)
            allp.extend(pl)
        restore_variant(saved)

        closed = [x for x in allp if x.get("realized_r") is not None]
        if not closed:
            print(f"{name:18s} {'0':>8s} {'-':>9s} {'-':>9s} {'-':>9s} {'-':>6s}")
            continue
        gross = [x["realized_r"] for x in closed]
        # planurile NO_ENTRY nu au costat nimic - nu s-a intrat in piata
        net = [g - (cost_in_r(x) if x.get("state") != plan_tracker.STATE_NO_ENTRY else 0)
               for g, x in zip(gross, closed)]
        wins = [r for r in net if r > 0]
        losses = [abs(r) for r in net if r < 0]
        pf = (sum(wins) / sum(losses)) if losses else float("inf")
        rows.append((name, len(closed), 100 * len(wins) / len(closed),
                     sum(gross) / len(gross), sum(net) / len(net), pf))
        print(f"{name:18s} {len(closed):8d} {100*len(wins)/len(closed):8.1f}% "
              f"{sum(gross)/len(gross):+9.3f} {sum(net)/len(net):+9.3f} {pf:7.2f}")

    if rows:
        best = max(rows, key=lambda r: r[4])
        print("-" * 74)
        print(f"R net = R brut minus {100*ROUNDTRIP_COST_PCT:.2f}% cost dus-intors, "
              f"convertit in R. Compara dupa R NET.")
        print(f"Cea mai buna: {best[0]} ({best[4]:+.3f}R net pe plan, {best[1]} planuri)")
        if best[4] <= 0:
            print("\nATENTIE: nicio varianta nu e profitabila pe aceste date.")
            print("Asta inseamna ca logica de semnal actuala nu are avantaj pe "
                  "aceste simboluri/perioada - nu ca trebuie reglati parametrii.")
    return rows


def main():
    days = DEFAULT_DAYS
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])
    merge = "--merge" in sys.argv
    sweep = "--sweep" in sys.argv

    exchange, markets, tickers, _, exchange_id = scanner.connect_exchange(scope=None)
    symbols = resolve_symbols(exchange, markets, tickers)
    if not symbols:
        raise SystemExit("Niciun simbol de testat.")

    print(f"\nBacktest pe {exchange_id}: {len(symbols)} simboluri, {days} zile, "
          f"timeframe {scanner.CONFIG['timeframe']}")
    for base, sym in symbols.items():
        print(f"  {base:6s} -> {sym}")

    weights = load_json(os.path.join(DATA_DIR, "weights.json"),
                        dict(scanner.DEFAULT_WEIGHTS))

    histories = {}
    all_plans = []
    next_id = 1
    for base, sym in symbols.items():
        print(f"\nDescarc {sym}...", end=" ", flush=True)
        candles = fetch_history(exchange, sym, scanner.CONFIG["timeframe"], days)
        if len(candles) < WARMUP_BARS + 50:
            print(f"prea putine date ({len(candles)} bare) - sar peste")
            continue
        span = (candles[-1][0] - candles[0][0]) / 86400000
        print(f"{len(candles)} bare ({span:.0f} zile). Rulez replay...", end=" ", flush=True)
        histories[sym] = candles
        plans, next_id = replay_symbol(sym, candles, weights, next_id)
        closed = [p for p in plans if p.get("realized_r") is not None]
        total_r = sum(p["realized_r"] for p in closed)
        print(f"{len(closed)} planuri inchise, {total_r:+.1f}R")
        all_plans.extend(plans)

    if sweep:
        run_sweep(symbols, histories, weights)
        return

    closed = [p for p in all_plans if p.get("realized_r") is not None]
    if not closed:
        print("\nNiciun plan inchis - verifica datele sau parametrii.")
        return

    store = {"next_id": next_id, "plans": all_plans}
    store["calibration"] = plan_tracker.build_calibration(store)
    store["summary"] = plan_tracker.summarize(store)
    save_json(BACKTEST_FILE, store)

    print("\n" + "=" * 60)
    plan_tracker.print_summary(store)
    print("\nCALIBRARE MASURATA (rata reala pe interval de scor):")
    for b in sorted(store["calibration"], key=int):
        e = store["calibration"][b]
        flag = "" if e["reliable"] else "  (prea putine date)"
        print(f"  scor {b}-{int(b)+19}: {e['win_rate']:5.1f}% "
              f"(IC {e['ci_low']:.0f}-{e['ci_high']:.0f}%) "
              f"R mediu {e['avg_r']:+.3f}  n={e['total']}{flag}")

    span_days = (max(p["closed_ts"] for p in closed) -
                 min(p["created_ts"] for p in all_plans)) / 86400
    print(f"\nAcoperire calendaristica: {span_days:.0f} zile "
          f"(pragul agentului: {21} zile)")
    print(f"Salvat in {BACKTEST_FILE}")

    if merge:
        live = load_json(PLANS_FILE, {"next_id": 1, "plans": []})
        offset = live.get("next_id", 1)
        for p in all_plans:
            p["id"] = p["id"] + offset - 1
        live["plans"].extend(all_plans)
        live["next_id"] = offset + len(all_plans)
        live["calibration"] = plan_tracker.build_calibration(live)
        live["summary"] = plan_tracker.summarize(live)
        save_json(PLANS_FILE, live)
        print(f"\nAdaugate {len(all_plans)} planuri in {PLANS_FILE} (marcate source=backtest).")
        print("Ruleaza acum `python3 ai_agent.py` ca agentul sa invete din ele.")
    else:
        print("\nRuleaza cu --merge daca vrei ca agentul sa invete din aceste rezultate.")


if __name__ == "__main__":
    main()
