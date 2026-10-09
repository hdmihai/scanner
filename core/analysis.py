# -*- coding: utf-8 -*-
"""core.analysis - analiza completa a unui token, identica pe orice bursa: detaliile pentru
dashboard (grafic, Elliott, structura, lichidari, prognoza) si evidentele fiecarui semnal,
plus propunerea de intrare. Datele de piata vin prin portul ports.market_data.
"""

from core import elliott as ew_mod
from core import evidence as ev_mod
from core import indicators
from core import liquidation as liq_mod
from core import liquidity_structure as ls_mod
from core import market as mkt
from core import market_structure as struct_mod
from core import plans as plan_tracker
from core import zones as zones_mod
from core.config import CHART_BARS, CHART_BARS_MAX, CONFIG, SPARKLINE_BARS
from core.geometry import compute_fibonacci, compute_structure_levels, compute_trade_plan
from core.scoring import ema_series_full, round_price, rsi


def fetch_liquidity_levels(exchange, symbol, depth=100, top_n=3, attempts=None):
    """BUG FIX: nu despachetez cu `for p, a in bids`. Standardul ccxt e
    [pret, cantitate], dar unele exchange-uri adauga un al treilea camp -
    Kraken pune si timestamp-ul nivelului, ceea ce arunca
    "ValueError: too many values to unpack (expected 2)". Iau explicit
    primele doua elemente si ignor restul, indiferent de exchange."""
    # KuCoin accepta doar limit=20 sau 100; alte valori sunt respinse. Incerc
    # valoarea ceruta, apoi variantele acceptate, apoi fara limit deloc.
    ob = None
    # `attempts`: adancimile acceptate de bursa (din adaptorul ei); implicit ca inainte
    for attempt in (attempts or (depth, 20, None)):
        try:
            ob = (exchange.fetch_order_book(symbol, limit=attempt) if attempt
                  else exchange.fetch_order_book(symbol))
            break
        except Exception as e:
            last_err = e
    if ob is None:
        print(f"[!] order book {symbol}: {last_err}")
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
            out.append({"price": round_price(price), "amount": round_price(amount, 6)})
        return sorted(out, key=lambda d: d["amount"], reverse=True)[:top_n]

    return {"bids": normalize(ob.get("bids")), "asks": normalize(ob.get("asks"))}


def zone_levels(ind, fib, liq_struct, liq_map):
    """Confluentele zonelor de pe timeframe-ul scanarii: nivelurile pe care scanerul le
    calculeaza deja (profil de volum, VWAP, EMA, Fibonacci, lichiditate egala, lichidari)."""
    ind = ind or {}
    vp = ind.get("volume_profile") or {}
    em = ind.get("emas") or {}
    rt = (fib or {}).get("retracement") or {}
    lv = [("POC", vp.get("poc")), ("VAL", vp.get("val")), ("VAH", vp.get("vah")), ("VWAP", ind.get("vwap")),
          ("EMA50", em.get("ema50")), ("EMA200", em.get("ema200")),
          ("FIB 0.618", rt.get("0.618")), ("FIB 0.786", rt.get("0.786"))]
    for lvl in (liq_struct or {}).get("levels") or []:
        lv.append(("LICHIDITATE " + str(lvl.get("kind") or "").strip(), lvl.get("price")))
    for k in ("above", "below"):
        c = (liq_map or {}).get(k) or {}
        if c.get("price"):
            lv.append(("LICHIDARI", c["price"]))
    return lv


def daily_zones(daily, price):
    """Zonele 1D din lumanarile zilnice INCHISE, cu mediile de 50 si 200 de zile si profilul
    de volum zilnic drept confluente. {} daca seria e prea scurta. Doar afisare si analiza:
    nicio decizie de plan nu le citeste."""
    if not daily or len(daily) < 30:
        return {}
    closes = [c[4] for c in daily]
    lv = []
    if len(closes) >= 200:
        lv.append(("MA200 1D", sum(closes[-200:]) / 200))
    if len(closes) >= 50:
        lv.append(("MA50 1D", sum(closes[-50:]) / 50))
    vp = indicators.volume_profile(daily[-120:]) or {}
    lv += [("POC 1D", vp.get("poc")), ("VAL 1D", vp.get("val")), ("VAH 1D", vp.get("vah"))]
    return zones_mod.find_zones(daily, lv, price=price)


def build_symbol_details(r, candles, hist_tab, daily=None, htf=None):
    """Detaliile complete ale unui simbol pentru dashboard: indicatori, structura,
    Fibonacci, plan, prognoza, lumanari pentru grafic, Elliott, lichiditate si
    lichidari - ACELASI calcul pe orice bursa. Doar CPU, zero apeluri API.

    `r` e rezultatul scorarii (cu persistenta), `candles` seria INCHISA a simbolului,
    `hist_tab` istoricul masurat pentru prognoza (plan_tracker.history_table).
    ZONELE de suport/rezistenta: din `candles` (timeframe-ul scanarii) si din lumanarile
    zilnice `daily`; pe bursele secundare, `htf` aduce zonele 1D deja calculate pe bursa
    activa (acelasi token, practic acelasi pret)."""
    d_highs = [c[2] for c in candles]
    d_lows = [c[3] for c in candles]
    d_closes = [c[4] for c in candles]
    d_struct = compute_structure_levels(d_highs, d_lows)
    d_fib = compute_fibonacci(d_highs, d_lows)
    # Elliott calculat o singura data per token, refolosit mai jos.
    _ew_tok = ew_mod.analyze([c[2] for c in candles], [c[3] for c in candles],
                             d_closes, r["price"])
    # FEREASTRA ADAPTIVA: se largeste pana la primul punct al numaratorii
    # principale. Cu 90 de bare fixe, structura putea cadea complet in afara
    # graficului - masurat: 0 din 4 puncte vizibile, iar proiectia aparea
    # fara undele din care provine. Plafonat la CHART_BARS_MAX ca pagina sa
    # ramana usoara.
    _pri_idx = [pt["idx"] for pt in ((_ew_tok.get("primary") or {}).get("points") or [])
                if pt.get("idx") is not None]
    _nb = CHART_BARS
    if _pri_idx:
        _nb = max(CHART_BARS, min(len(candles) - min(_pri_idx) + 8, CHART_BARS_MAX))
    _nb = min(_nb, len(candles))

    _plan_tok = compute_trade_plan(r["direction"], r["price"], r["atr"], d_struct, d_fib)
    _ind = indicators.compute_all(candles)
    _liqs = ls_mod.build([c[2] for c in candles], [c[3] for c in candles], d_closes,
                         r["atr"], r["price"], CONFIG["timeframe"])
    _liqmap = liq_mod.build_map(candles, r["price"])
    _zones = zones_mod.bundle(
        zones_mod.find_zones(candles, zone_levels(_ind, d_fib, _liqs, _liqmap), price=r["price"]),
        htf if htf is not None else daily_zones(daily, r["price"]), scan_tf=CONFIG["timeframe"],
        keep_d1=htf is None)
    return {
        "direction": r["direction"],
        "score": r["risk_adjusted"],
        "probability": r["probability"],
        "components": r["components"],
        "price": r["price"],
        "atr": r["atr"],
        "persistence": r["persistence"],
        "age_minutes": r["age_minutes"],
        "indicators": _ind,
        "structure": d_struct,
        "fibonacci": d_fib,
        "plan": _plan_tok,
        # PROGNOZA: linie continua prin pretul real pana acum, apoi punctata
        # din prezent - scenariul Elliott ramas sau drumul planului, cu
        # durata si probabilitatea din istoric.
        "forecast": ew_mod.forecast_path(
            _ew_tok.get("primary"), [c[2] for c in candles], [c[3] for c in candles],
            d_closes, plan=_plan_tok,
            hist=hist_tab.get(f"{r['direction']}:{int(r['risk_adjusted'] // 20) * 20}"),
            plan_dir=r["direction"], agg_bias=ew_mod.bias(_ew_tok, r["direction"])),
        "sparkline": [round_price(c) for c in d_closes[-SPARKLINE_BARS:]],
        # LUMANARI pentru graficul bogat al fiecarui token cu semnal.
        # Pastrez CHART_BARS bare, rotunjite, doar OHLC + volum - suficient
        # pentru lumanari, EMA si panourile de sub grafic. Fisierul se
        # SUPRASCRIE la fiecare scanare, deci nu se acumuleaza; costa ~8 KB
        # per token, adica sub 200 KB pentru intreaga lista.
        "candles": [[c[0], round_price(c[1]), round_price(c[2]),
                     round_price(c[3]), round_price(c[4]), round(c[5] or 0, 2)]
                    for c in candles[-_nb:]],
        "ema20": [None if v is None else round_price(v)
                  for v in ema_series_full(d_closes, 20)[-_nb:]],
        "ema50": [None if v is None else round_price(v)
                  for v in ema_series_full(d_closes, 50)[-_nb:]],
        # Structura de piata per simbol. FARA multi-timeframe: ar insemna
        # 28 x 3 apeluri in plus la fiecare scanare. Timeframe-urile de
        # confirmare se descarca doar pentru simbolul afisat pe graficul
        # principal, unde chiar sunt privite.
        "liq_structure": _liqs,
        # `offset` aliniaza indicii punctelor (calculati pe seria completa)
        # cu cele CHART_BARS lumanari pastrate pentru grafic. Fara el,
        # punctele cele mai VECHI erau desenate peste barele cele mai NOI.
        "elliott": {**_ew_tok, "offset": max(0, len(candles) - _nb)},
        "structure_panel": struct_mod.build(
            d_closes, [c[2] for c in candles], [c[3] for c in candles],
            r["atr"], base_tf=CONFIG["timeframe"], price=r["price"]),
        # harta de lichidari si pentru simbolurile din detalii, nu doar
        # pentru cel mai bun candidat - dashboard-ul le arata pe toate
        "liquidation": {"above": _liqmap.get("above"), "below": _liqmap.get("below"),
                        "bias": liq_mod.magnet_bias(_liqmap, r["price"], r["direction"]),
                        "oi_scaled": _liqmap.get("oi_scaled")},
        # ZONELE DE SUPORT / REZISTENTA (core/zones.py): pe timeframe-ul scanarii si pe 1D,
        # plus cadrul desenat pe grafic - cele mai apropiate zone de o parte si de alta a
        # pretului. Doar afisare si analiza; deciziile de plan nu le citesc.
        "zones": _zones,
    }


def build_signal_context(sig, candles, exchange, caps, book_levels, alt_state):
    """Evidentele complete ale unui semnal - aceleasi pe orice bursa - si nivelurile
    planului. Intoarce (niveluri, semnal imbogatit).

    `exchange` e conexiunea bursei semnalului (order flow, open interest, timeframe-
    urile de confirmare), `caps` capabilitatile ei, `book_levels` order book-ul
    ACESTUI simbol (fetch_liquidity_levels) sau None.
    REGULA (garda: check_integrity): totul se calculeaza DOAR din seria proprie -
    highs_s / lows_s / closes_s - niciodata din seria altui token."""
    highs_s = [c[2] for c in candles]
    lows_s = [c[3] for c in candles]
    closes_s = [c[4] for c in candles]
    struct_s = compute_structure_levels(highs_s, lows_s)
    fib_s = compute_fibonacci(highs_s, lows_s)
    levels = compute_trade_plan(sig["direction"], sig["price"], sig["atr"], struct_s, fib_s)

    # EVIDENTE: din OHLCV-ul deja descarcat, deci zero apeluri API in plus.
    # Acelasi obiect alimenteaza si agentul (ca vector orientat) si
    # dashboard-ul (ca lista citibila) - o singura sursa de adevar.
    # ATENTIE: in acest bloc se folosesc DOAR highs_s / lows_s / closes_s.
    # Seria de inchideri din exterior (a candidatului principal) calcula Ichimoku,
    # regimul, Elliott si lichiditatea cu maximele unui token si inchiderile
    # altuia - pe dashboard, NEAR aparea "SUB NOR" desi era peste, iar
    # agentul invata din caracteristici corupte. Garda: check_integrity.
    sig_ind = indicators.compute_all(candles)
    sig_rsi = rsi(closes_s, 14)
    # Order flow si dezechilibrul cartii: exista doar daca bursa le suporta.
    # Daca lipsesc, evidentele corespunzatoare sunt pur si simplu absente -
    # nu inlocuite cu valori neutre, care ar minti modelul.
    sig_flow = mkt.order_flow(exchange, sig["symbol"], caps)
    sig_book = None
    if book_levels and book_levels.get("bids") and book_levels.get("asks"):
        sig_book = {"bid_volume": sum(b["amount"] for b in book_levels["bids"]),
                    "ask_volume": sum(a["amount"] for a in book_levels["asks"])}
    # HARTA DE LICHIDARI: construita din OHLCV, deci exista si in backtest.
    # Open interest o scaleaza daca bursa il ofera, dar nu e obligatoriu -
    # deciziile se iau pe densitate relativa, nu absoluta.
    sig_oi = mkt.open_interest(exchange, sig["symbol"], caps)
    sig_liq = liq_mod.build_map(candles, sig["price"], oi_weight=sig_oi)
    sig_liq_bias = liq_mod.magnet_bias(sig_liq, sig["price"], sig["direction"])
    # STRUCTURA DE PIATA. Timeframe-urile de confirmare se descarca o
    # singura data pentru simbolul afisat, nu pentru toate - altfel ar
    # insemna 28 x 3 apeluri in plus la fiecare scanare.
    def _confirm_closes(tf):
        if tf == CONFIG["timeframe"]:
            return closes_s
        try:
            o = exchange.fetch_ohlcv(sig["symbol"], timeframe=tf, limit=120)
        except Exception:
            return None
        if not o or len(o) < 60:
            return None
        return [c[4] for c in o[:-1]]

    sig_struct = struct_mod.build(
        closes_s, [c[2] for c in candles], [c[3] for c in candles],
        sig["atr"], base_tf=CONFIG["timeframe"],
        fetch_closes=_confirm_closes, order_book=book_levels,
        price=sig["price"])

    sig_ew = ew_mod.analyze([c[2] for c in candles], [c[3] for c in candles],
                            closes_s, sig["price"])
    sig_ew_bias = ew_mod.bias(sig_ew, sig["direction"])

    sig_liqs = ls_mod.build([c[2] for c in candles], [c[3] for c in candles],
                            closes_s, sig["atr"], sig["price"], CONFIG["timeframe"])
    sig_liqs_bias = ls_mod.bias(sig_liqs, sig["direction"])

    sig_evidence = ev_mod.build_evidence(sig_ind, sig["price"], sig["atr"],
                                         sig_rsi, sig.get("components"),
                                         flow=sig_flow, book=sig_book,
                                         caps=caps,
                                         liq=sig_liq, liq_bias=sig_liq_bias,
                                         struct=sig_struct,
                                         ew=sig_ew, ew_bias=sig_ew_bias,
                                         liqs=sig_liqs, liqs_bias=sig_liqs_bias,
                                         alt=alt_state, symbol=sig["symbol"],
                                         direction=sig["direction"])
    # CONFLICT PLAN - ELLIOTT. `sig_ew_bias` e exact caracteristica ev_elliott
    # a agentului (directia asteptata a structurii, fata de directia planului).
    # Masurat pe 16.318 planuri reale: cu ev_elliott <= -0.1 (Elliott
    # contrazice planul) R mediu +0.042, fara avantaj demonstrat (IC95 include
    # zero); fara conflict +0.112R. Diferenta +0.070R, IC95 +0.006..+0.137.
    _pr = (sig_ew or {}).get("primary") or {}
    _next = None
    if _pr.get("expected") and _pr["expected"] != sig["direction"]:
        _pp = [q["price"] for q in ((_pr.get("projection") or {}).get("path") or [])
               if q.get("projected")]
        if _pp:
            _next = min(_pp) if sig["direction"] == "LONG" else max(_pp)
    _conflict = {"bias": sig_ew_bias, "text": _pr.get("stage_text"),
                 "next_entry": _next, "headline": _pr.get("headline")}
    sig = {**sig,
           "elliott_conflict": _conflict,
           "evidence": sig_evidence,
           "fusion": ev_mod.fusion(sig_evidence, sig["direction"]),
           "indicators": sig_ind,
           "structure_panel": sig_struct,
           "elliott": sig_ew,
           "liq_structure": sig_liqs,
           "liquidation": {"above": sig_liq.get("above"),
                           "below": sig_liq.get("below"),
                           "bias": sig_liq_bias,
                           "oi_scaled": sig_liq.get("oi_scaled")},
           "components": {**(sig.get("components") or {}),
                          **ev_mod.evidence_features(sig_evidence, sig["direction"])}}
    return levels, sig


def light_details(details):
    """Indicatorii rezumati per simbol pentru exchange_scans.json (tab-ul bursei)."""
    out = {}
    for k, v in (details or {}).items():
        ind = v.get("indicators") or {}
        vp = ind.get("volume_profile") or {}
        out[k] = {"supertrend": (ind.get("supertrend") or {}).get("direction"),
                  "vwap": ind.get("vwap"), "poc": vp.get("poc"), "vah": vp.get("vah"),
                  "val": vp.get("val"), "macd_hist": (ind.get("macd") or {}).get("histogram"),
                  "position": ind.get("price_vs_value_area")}
    return out


def proposal_record(sig, levels, decision, agent_pred, trained):
    """Propunerea de intrare, in forma afisata de modulul bursei - identica pe
    bursa activa (unde devine plan) si pe celelalte (unde e doar simulata)."""
    return {
        "symbol": sig["symbol"], "direction": sig["direction"],
        "score": sig.get("risk_adjusted"), "price": sig.get("price"), "atr": sig.get("atr"),
        "levels": levels,
        "decision": {k: (decision or {}).get(k) for k in
                     ("action", "mode", "reason", "calibrated_prob", "agent_prob", "expected_value_r")},
        "agent": {"probability": (agent_pred or {}).get("probability"),
                  "active": (agent_pred or {}).get("active"),
                  "superior": (agent_pred or {}).get("superior")},
        "neighbors": sig.get("neighbors"),
        "fusion": sig.get("fusion"),
        "evidence": sig.get("evidence") or [],
        "elliott_conflict": sig.get("elliott_conflict"),
        "trained": bool(trained),
    }


def _open_plan_for_base(plan_store, symbol, direction):
    """Planul deschis pe bursa activa pentru ACELASI token si aceeasi directie
    (perechile pot diferi intre burse: BTC/USD pe Kraken, BTC/USDT pe OKX)."""
    base = symbol.split("/")[0]
    for p in reversed((plan_store or {}).get("plans") or []):
        if (p.get("state") in (plan_tracker.STATE_PENDING, plan_tracker.STATE_OPEN, plan_tracker.STATE_TP1)
                and p.get("direction") == direction and str(p.get("symbol", "")).split("/")[0] == base):
            return {"id": p.get("id"), "symbol": p.get("symbol"), "state": p.get("state")}
    return None
