# -*- coding: utf-8 -*-
"""tests.analyst_check - verifica zonele de suport/rezistenta si raportul analistului pe date controlate.

Uz (din radacina repo-ului): python -m tests.analyst_check

  1. ZONELE: un minim de swing clar devine zona de suport sub pret; o zona sparta isi inverseaza
     rolul (flip); zona 1D care acopera o zona 4h o reprezinta pe grafic; seria prea scurta nu
     produce zone inventate;
  2. CONTROLUL STATISTIC: pe mers aleator (nicio structura reala) zonele NU bat benzile-placebo -
     altfel statistica afisata langa zone ar fi partinitoare;
  3. RAPORTUL: cele patru sectiuni exista, textele scurte au cel mult 10 cuvinte, cele 9 faze se
     mapeaza pe cele 4 faze ale rotatiei, directia BTC.D si regulile strategiei urmeaza pragurile
     declarate, R:R e calculat din zone, iar un proiect care nu bate BTC nu intra la filtrarea alfa;
  4. AFISAREA: dashboard-ul randeaza raportul cu etichetele formatului, fara exceptii.
Foloseste starea altseason reala din data/altseason.json ca baza; nu scrie nimic in repo.
"""

import copy
import json
import math
import os
import random
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SCAN_TIMEFRAME", "4h")

from core import analyst as AN      # noqa: E402
from core import zones as Z         # noqa: E402

DAY = 86400000


def _series(prices, t0=1_700_000_000_000, step=DAY, wick=0.004):
    out, prev = [], prices[0]
    for i, p in enumerate(prices):
        o = prev
        out.append([t0 + i * step, o, max(o, p) * (1 + wick), min(o, p) * (1 - wick), p, 100.0])
        prev = p
    return out


def check_zones(fails):
    # coborare la 100, raliu la 130, retragere la 112: suportul de la 100 ramane sub pret
    path = [120 - i for i in range(20)] + [100 + 1.5 * i for i in range(21)] + [130 - i for i in range(19)]
    c = _series(path)
    r = Z.find_zones(c)
    sup = r.get("support")
    if not sup or not (sup["lo"] <= 100.5 <= sup["hi"] + 1) or sup["side"] != "S":
        fails.append(f"zone: suportul de la minimul 100 nu a fost gasit (gasit: {sup})")
    if any(z["side"] == "S" and z["hi"] > c[-1][4] for z in r.get("zones") or []):
        fails.append("zone: un suport deasupra pretului")
    # pretul de acum sub zona (fara inchidere sub ea pe timeframe-ul zonei): zona lucreaza ca rezistenta
    below = Z.find_zones(c, price=98.0)
    hit = [z for z in below.get("zones") or [] if z["lo"] <= 100.5 <= z["hi"] + 1]
    if not hit or hit[0]["side"] != "R" or not hit[0].get("pending"):
        fails.append(f"zone: suportul aflat deasupra pretului curent nu a devenit rezistenta ({hit})")
    # inchidere sub suport -> rolul se inverseaza: zona devine rezistenta deasupra pretului
    path2 = path + [111 - 2 * i for i in range(12)]
    r2 = Z.find_zones(_series(path2))
    flips = [z for z in r2.get("zones") or [] if z["kind"] == "flip" and z["side"] == "R"]
    if not flips:
        fails.append("zone: suportul spart nu a devenit rezistenta (flip)")
    # zona 1D peste zona mica de acelasi tip: pe grafic ramane doar cea 1D, marcata confirmata
    small = {"zones": [{"side": "S", "lo": 99.0, "hi": 101.0, "strength": 50}]}
    big = {"zones": [{"side": "S", "lo": 98.0, "hi": 102.0, "strength": 60}]}
    b = Z.bundle(small, big, "4h")
    if len(b["frame"]) != 1 or b["frame"][0]["tf"] != "1d" or not b["frame"][0].get("confirmed"):
        fails.append(f"zone: cadrul nu reprezinta zona 4h prin zona 1D care o acopera ({b['frame']})")
    if Z.find_zones(_series([100, 101, 102])) != {}:
        fails.append("zone: o serie de 3 bare a produs zone")
    # suport 1D in care e pretul + rezistenta 4h care il suprapune: 1D isi pastreaza aria, 4h pierde
    # partea comuna si ramane deasupra lui (pe grafic, benzile nu se mai acopera una pe alta)
    d1 = {"price": 10.075, "atr": 0.4, "zones": [{"side": "S", "lo": 9.978, "hi": 10.237, "strength": 40,
                                                   "dist_pct": 0.0}]}
    sc = {"price": 10.075, "atr": 0.1, "zones": [{"side": "R", "lo": 10.0718, "hi": 10.5, "strength": 50,
                                                   "dist_pct": 0.0}]}
    fr = Z.bundle(sc, d1, "4h")["frame"]
    rz = [z for z in fr if z["side"] == "R"]
    if (len(fr) != 2 or not rz or rz[0]["lo"] != 10.237 or not rz[0].get("clipped")
            or [z for z in fr if z["side"] == "S"][0]["hi"] != 10.237):
        fails.append(f"zone: suprapunerea suport 1D / rezistenta 4h nu a fost rezolvata ({fr})")
    # proprietatea, pe 40 de serii aleatoare (4h si aceleasi lumanari stranse in zile): nicio
    # suprapunere, suportul nu e deasupra pretului, rezistenta nu e sub el
    rnd, bad, n_fr = random.Random(11), [], 0
    for k in range(40):
        p, rows = 100.0, []
        for i in range(720):
            o = p
            cl = o * math.exp(rnd.gauss(0, 0.012))
            rows.append([i * 14400000, o, max(o, cl) * (1 + abs(rnd.gauss(0, 0.005))),
                         min(o, cl) * (1 - abs(rnd.gauss(0, 0.005))), cl, 1.0])
            p = cl
        day = [[g[0][0], g[0][1], max(x[2] for x in g), min(x[3] for x in g), g[-1][4], 6.0]
               for g in (rows[j:j + 6] for j in range(0, 720, 6))]
        px = rows[-1][4] * (1 + rnd.uniform(-0.03, 0.03))
        a, b1 = Z.find_zones(rows[-300:], price=px), Z.find_zones(day[:-1], price=px)
        for name, zs in (("4h", a.get("zones") or []), ("1d", b1.get("zones") or []),
                         ("cadru", Z.bundle(a, b1, "4h")["frame"])):
            n_fr += name == "cadru" and len(zs)
            for i, z in enumerate(zs):
                if (z["side"] == "S" and z["lo"] > px) or (z["side"] == "R" and z["hi"] < px) or z["hi"] <= z["lo"]:
                    bad.append((k, name, "parte", z["side"], z["lo"], z["hi"], round(px, 4)))
                for y in zs[i + 1:]:
                    if z["lo"] < y["hi"] and y["lo"] < z["hi"]:
                        bad.append((k, name, "suprapunere", (z["lo"], z["hi"]), (y["lo"], y["hi"])))
    if bad:
        fails.append(f"zone: {len(bad)} zone suprapuse sau de partea gresita a pretului, ex. {bad[:3]}")
    print(f"  1. zone: suport {sup and (sup['lo'], sup['hi'])}, flip-uri dupa spargere {len(flips)}, "
          f"cadru 1D peste 4h OK, 40 de serii aleatoare: {n_fr} zone in cadre, "
          + ("fara suprapuneri" if not bad else f"{len(bad)} probleme"))


def check_placebo(fails):
    rnd = random.Random(5)
    ser = {}
    for k in range(25):
        p, rows = 100.0, []
        for i in range(500):
            o = p
            cl = o * math.exp(rnd.gauss(0, 0.02))
            rows.append([i * 14400000, o, max(o, cl) * (1 + abs(rnd.gauss(0, 0.007))),
                         min(o, cl) * (1 - abs(rnd.gauss(0, 0.007))), cl, 1.0])
            p = cl
        ser[f"RW{k}"] = rows
    st = Z.hold_stats(ser)
    if st["verdict"] == "peste_placebo":
        fails.append(f"zone: pe mers aleator zonele bat placebo ({st['zones']} vs {st['placebo']}) - statistica e partinitoare")
    if st["zones"]["n"] < 200:
        fails.append(f"zone: prea putine retestari pe mers aleator ({st['zones']['n']})")
    print(f"  2. control pe mers aleator: zone {st['zones']['rate']}% (n={st['zones']['n']}) vs placebo "
          f"{st['placebo']['rate']}% (n={st['placebo']['n']}) -> {st['verdict']}")


def _alt_fixture():
    with open(os.path.join(ROOT, "data", "altseason.json")) as f:
        alt = json.load(f)
    ind = alt["indicators"]
    rb7, rb30, rb200 = ind["btc_r7"], ind["btc_r30"], ind.get("btc_r200") or 0

    def coin(sym, rs7, rs30, rs200, circ=0.9, fdv=1.1, vol=5e7, mcap=1e9):
        back = lambda rs, rb: ((1 + rs / 100) * (1 + rb / 100) - 1) * 100
        return {"id": sym.lower(), "symbol": sym, "name": sym.title(), "rank": 50, "price": 1.0, "mcap": mcap,
                "fdv": mcap * fdv, "vol": vol, "circ": circ * 1000, "total": 1000, "max": None,
                "r7": back(rs7, rb7), "r30": back(rs30, rb30), "r200": back(rs200, rb200)}
    strong = [coin(f"AI{i}", 4 + i, 10 + i, 30, circ=0.4 if i == 0 else 0.9) for i in range(8)]
    hype = [coin(f"MM{i}", 25, 6 if i < 5 else -3, -40) for i in range(8)]
    weak = [coin(f"L1{i}", -2, -5, 10) for i in range(8)]
    alt["sectors"] = {"items": {"artificial-intelligence": {"name": "AI", "coins": strong, "ts": 1, "when": "t"},
                                "meme-token": {"name": "Meme", "coins": hype, "ts": 1, "when": "t"},
                                "layer-1": {"name": "Layer 1", "coins": weak, "ts": 1, "when": "t"}}}
    alt["markets"] = {"BTC": coin("BTC", 0, 0, 0), "AI0": strong[0], "L10": weak[0]}
    alt["candidates"] = [{"symbol": "AI0", "name": "Ai0", "rank": 50, "rs7": 4.0, "rs30": 10.0, "rs90": None},
                         {"symbol": "L10", "name": "L10", "rank": 60, "rs7": -2.0, "rs30": -5.0, "rs90": None}]
    alt["btc_d_daily"] = {f"2026-09-{d:02d}": 58.0 + 0.04 * d for d in range(1, 31)}
    alt["btc_d_daily"].update({f"2026-10-{d:02d}": 59.3 + 0.01 * d for d in range(1, 9)})
    return alt


def check_report(fails):
    alt = _alt_fixture()
    up = [100 + 0.4 * i for i in range(150)] + [160 - 0.5 * i for i in range(40)] + [140 + 0.8 * i for i in range(60)]
    btc = [60000 + 120 * math.sin(i / 9) * 30 + 40 * i for i in range(260)]
    daily = {"BTC": _series(btc), "AI0": _series(up), "L10": _series(list(reversed(up)))}
    rep = AN.build_report(alt, {}, daily, None, 1_791_510_000)
    for k in ("macro", "sectors", "alpha", "strategy"):
        if k not in rep:
            fails.append(f"raport: lipseste sectiunea {k}")
    for s in rep["sectors"]["top"]:
        if len(s["catalyst"].split()) > AN.WORDS:
            fails.append(f"raport: catalizatorul {s['name']} are peste 10 cuvinte")
    names = [s["name"] for s in rep["sectors"]["top"]]
    if "AI" not in names or "Layer 1" in names:
        fails.append(f"raport: selectia sectoarelor nu urmeaza forta relativa si latimea ({names})")
    sus = {s["name"]: s["sustain"] for s in rep["sectors"]["top"]}
    if sus.get("AI") != "Trend structural" or sus.get("Meme", "Hype pe termen scurt") != "Hype pe termen scurt":
        fails.append(f"raport: sustenabilitatea sectoarelor gresita ({sus})")
    tick = [p["ticker"] for p in rep["alpha"]]
    if "L10" in tick:
        fails.append("raport: un proiect care nu bate BTC a intrat la filtrarea alfa")
    for p in rep["alpha"]:
        if len(p["argument"].split()) > AN.WORDS or len(p["risk"].split()) > AN.WORDS:
            fails.append(f"raport: textele lui {p['ticker']} au peste 10 cuvinte")
        if p["rr"] is not None and p["stop"] and p["target"]:
            exp = round((p["target"] - p["price"]) / (p["price"] - p["stop"]), 2)
            if abs(exp - p["rr"]) > 0.011:
                fails.append(f"raport: R:R gresit pentru {p['ticker']} ({p['rr']} vs {exp})")
    a0 = next((p for p in rep["alpha"] if p["ticker"] == "AI0"), None)
    if not a0 or not a0["risk"].startswith("Diluare"):
        fails.append(f"raport: riscul de diluare (40% in circulatie) nu e riscul critic al AI0 ({a0 and a0['risk']})")
    # cele 9 faze -> cele 4 faze ale rotatiei
    exp4 = {0: 0, 1: 1, 2: 1, 3: 2, 4: 3, 5: 4, 6: 4, 7: 4, 8: 5}
    for p9, p4 in exp4.items():
        got = AN.rotation_phase({"phase": p9, "name": "x", "confidence": 0.5}, {})["n"]
        if got != p4:
            fails.append(f"raport: faza {p9} mapata pe {got}, nu pe {p4}")
    # directia BTC.D: +0.6 pp pe 30 de zile = in crestere; -0.6 = scadere; +0.6 dar -0.4 pe 7 zile = consolidare
    days = [f"2026-09-{d:02d}" for d in range(1, 31)] + [f"2026-10-{d:02d}" for d in range(1, 9)]
    lin = lambda a, b: {d: a + (b - a) * i / (len(days) - 1) for i, d in enumerate(days)}
    for ser, want in ((lin(58.0, 58.8), "În creștere"), (lin(58.8, 58.0), "Scădere"), (lin(58.4, 58.5), "Consolidare")):
        got = AN.btc_dominance({"btc_d": ser[days[-1]]}, ser)["status"]
        if got != want:
            fails.append(f"raport: BTC.D {ser[days[0]]:.1f}->{ser[days[-1]]:.1f} clasificat {got}, nu {want}")
    turn = lin(58.0, 59.0)
    turn[days[-1]] = 58.5            # urcare pe 30 de zile, dar -0.4 pp in ultima saptamana
    if AN.btc_dominance({"btc_d": 58.5}, turn)["status"] != "Consolidare":
        fails.append("raport: o inflexiune pe 7 zile nu e tratata ca consolidare")
    # strategia: regim bear -> Bearish; piata bull -> Bullish; DCA doar in fazele 3-4 cu sectoare structurale
    btcx = {"price": 80000, "ma200": 70000, "support": {"lo": 76000, "hi": 78000}, "resistance": {"lo": 85000, "hi": 87000}}
    sect = {"top": [{"name": "AI", "sustain": "Trend structural"}]}
    bd = {"status": "Scădere", "lo30": 58.0, "hi30": 59.0}
    if AN.strategy({"regime": "bear"}, {}, bd, {}, sect, btcx, {"n": 3})["sentiment"] != "Bearish":
        fails.append("raport: regimul bear nu da sentiment Bearish")
    s_bull = AN.strategy({"regime": "bull"}, {}, bd, {"value": 60, "trend": "în creștere"}, sect, btcx, {"n": 3})
    if s_bull["sentiment"] != "Bullish" or s_bull["guide_kind"] != "DCA":
        fails.append(f"raport: piata bull in faza 3 cu sector structural nu da Bullish + DCA ({s_bull})")
    s_wait = AN.strategy({"regime": "accumulation"}, {}, {"status": "În creștere", "lo30": 58.0, "hi30": 59.1},
                         {"value": 52, "trend": "lateral"}, sect, btcx, {"n": 1})
    if s_wait["sentiment"] != "Neutru" or s_wait["guide_kind"] != "WAIT" or "87,000" not in s_wait["guide"]:
        fails.append(f"raport: faza 1 in recuperare nu da Neutru + asteptare breakout peste rezistenta ({s_wait})")
    if "76,000" not in s_wait["invalidation"] or "59.30%" not in s_wait["invalidation"]:
        fails.append(f"raport: invalidarea nu foloseste suportul 1D si maximul BTC.D ({s_wait['invalidation']})")
    # BTC.D fara 30 de zile masurate: directia din seria masurata pe 7 zile, estimarea doar context
    short = {f"2026-09-{d:02d}": 59.0 + 0.01 * d for d in range(27, 31)}
    short.update({f"2026-10-{d:02d}": 59.3 - 0.01 * d for d in range(1, 9)})
    b7 = AN.btc_dominance({"btc_d": 59.2, "btc_d_delta30": 0.9}, short)
    if b7.get("basis") != "7z" or b7["status"] != "Consolidare" or "doar context" not in b7["text"]:
        fails.append(f"raport: cu serie scurta, directia BTC.D nu vine din seria masurata ({b7.get('basis')}, {b7['status']})")
    # calendarul ciclului: fereastra istorica a minimului, din ciclurile proprii (fara corectia scurta din 2021)
    cyc = [{"top": "2017-12-16", "days_top_to_low": 364}, {"top": "2021-04-13", "days_top_to_low": 98},
           {"top": "2021-11-08", "days_top_to_low": 366}, {"top": "2025-10-06", "days_top_to_low": 267}]
    cal = AN.cycle_calendar(cyc, 368)
    if not cal or not cal["active"] or cal["until"] != "2026-11-21" or cal["past_days"] != [364, 366]:
        fails.append(f"raport: calendarul ciclului e gresit ({cal})")
    if AN.cycle_calendar(cyc, 420)["active"]:
        fails.append("raport: fereastra calendarului ramane deschisa dupa marja ei")
    ph_def = AN.rotation_phase({"phase": 1, "name": "x", "confidence": 0.8}, {"btc_r7": -2.8, "btc_r30": 4.6})
    if "defensivă" not in ph_def["text"]:
        fails.append("raport: faza 1 cu BTC in scadere pe 7 zile nu e marcata defensiva")
    # trendul indicelui: din seria live cand acopera 90 de zile, altfel din indicele istoric (marcat)
    hist = {"position": {"ai_90d_ago": 30.0, "ai_now_hist": 58.0, "ai_trend90": [["2026-07-10", 30.0]], "ai_percentile": 80}}
    short = AN.alt_index({"alt_index_90d": 52.0}, hist, {"2026-10-01": 50.0, "2026-10-08": 52.0})
    t0 = datetime(2026, 7, 1)
    full = {(t0 + timedelta(days=i)).strftime("%Y-%m-%d"): 20.0 + 0.35 * i for i in range(100)}
    live = AN.alt_index({"alt_index_90d": 54.7}, hist, full)
    if short["trend_source"] != "istoric" or "istoric comparabil" not in short["text"]:
        fails.append(f"raport: trendul indicelui fara serie live de 90 de zile nu e marcat istoric ({short['text']})")
    if live["trend_source"] != "live" or not (30.0 <= live["delta"] <= 32.0) or live["trend"] != "în creștere":
        fails.append(f"raport: trendul indicelui nu foloseste seria live de 90 de zile ({live})")
    # acelasi ticker, alt activ: pret pe bursa de 40x fata de CoinGecko -> fara zone, R:R si beta
    odd = copy.deepcopy(alt)
    odd["markets"]["AI0"]["price"] = up[-1] / 40
    o = next((p for p in AN.alpha(odd, {}, daily, [], daily["BTC"]) if p["ticker"] == "AI0"), None)
    if not o or not o.get("other_asset") or o.get("rr") is not None or o.get("beta") is not None:
        fails.append(f"raport: un activ diferit cu acelasi ticker a primit zone/R:R ({o})")
    print(f"  3. raport: sectoare {names} {sus}, alfa {tick}, faze 9->4 OK, BTC.D si strategie OK, "
          f"trend indice live/istoric OK, ticker cu alt activ exclus de la R:R")
    return rep


def check_render(fails, rep):
    from dashboard.sections.analyst import render_analyst
    html = render_analyst(rep)
    for label in ("1. DIAGNOSTIC MACRO (ALT SEASON CHECK)", "Status BTC Dominance (BTC.D)", "Faza Rotației de Capital",
                  "Altcoin Index", "2. SECTOARE MOMENTUM", "Catalizator", "Sustenabilitate", "3. FILTRARE ALFA",
                  "Argument", "Risc Critic", "4. STRATEGIE EXECUTIVĂ", "Sentiment General", "Ghid de Intrare",
                  "Trigger de Invalidare"):
        if label not in html:
            fails.append(f"afisare: lipseste eticheta '{label}'")
    if "<svg" not in html:
        fails.append("afisare: graficul BTC 1D lipseste")
    empty = render_analyst(None)
    if "apare după prima scanare" not in empty:
        fails.append("afisare: raportul lipsa nu e explicat")
    broken = copy.deepcopy(rep)
    broken["macro"]["btc_d"] = {}
    broken["alpha"] = []
    render_analyst(broken)
    # fara date pe sectoare (primele scanari): nicio concluzie despre capital, doar starea incarcarii
    nosec = copy.deepcopy(rep)
    nosec["sectors"] = {"top": [], "all": [], "covered": 0, "of": 12}
    h = render_analyst(nosec)
    if "capitalul rămâne în BTC" in h or "se încarcă" not in h:
        fails.append("afisare: fara date pe sectoare, raportul trage o concluzie despre capital")
    # graficul BTC 1D: pretul de acum ca linie proprie si nicio pastila de pret dublata
    if "PRET ACUM" not in html:
        fails.append("afisare: pretul curent lipseste de pe graficul BTC 1D")
    import re
    import chart_render
    vals = re.findall(rf'<text class="pill-t" x="{chart_render.W - chart_render.PAD_R + 6}" y="[\d.-]+">([^<]+)</text>',
                      html)
    if len(vals) != len(set(vals)):
        fails.append(f"afisare: pastile de pret dublate pe graficul BTC 1D ({sorted(vals)})")
    print(f"  4. afisare: {len(html) // 1024} KB, toate etichetele formatului prezente, "
          f"{len(vals)} pastile de pret distincte pe graficul BTC 1D")


def check_tokens(fails):
    """Pasii 1-4 per token: regulile de sentiment, limita de 10 cuvinte, invalidarea la baza
    suportului 1D, BTC ca referinta, si verificarea masurata a verdictelor."""
    from dashboard.sections.analyst import render_analyst, render_token_steps
    from dashboard.sections.tokens import render_token_details
    alt = _alt_fixture()
    t0 = 1_766_000_000_000
    btc_px = [60000 + 60 * i for i in range(260)]
    # lumanarile zilnice sunt acum sursa fortei relative (aceeasi functie ca evidenta agentului),
    # deci seria bate BTC si pe ele, nu doar in datele CoinGecko: +5% pe 30 de zile, +7% pe 7 zile
    up = [1.0 + 0.004 * i for i in range(230)] + [1.92 - 0.004 * i for i in range(20)] + [1.84 + 0.02 * i for i in range(10)]
    down = [3.0 - 0.006 * i for i in range(260)]
    daily = {"BTC": _series(btc_px, t0), "UPC": _series(up, t0), "DNC": _series(down, t0)}
    ind = alt["indicators"]
    back = lambda rs, rb: ((1 + rs / 100) * (1 + (rb or 0) / 100) - 1) * 100
    alt["markets"].update({
        "UPC": {"symbol": "UPC", "name": "Up", "rank": 40, "price": up[-1], "mcap": 1e9, "fdv": 1.1e9, "vol": 5e7,
                "circ": 900, "total": 1000, "r7": back(4, ind["btc_r7"]), "r30": back(12, ind["btc_r30"]),
                "r200": back(30, ind.get("btc_r200"))},
        "DNC": {"symbol": "DNC", "name": "Down", "rank": 150, "price": down[-1], "mcap": 2e8, "fdv": 2.2e8, "vol": 1e6,
                "circ": 900, "total": 1000, "r7": back(-3, ind["btc_r7"]), "r30": back(-15, ind["btc_r30"]),
                "r200": back(-40, ind.get("btc_r200"))}})
    alt["sectors"]["items"]["artificial-intelligence"]["coins"].append(dict(alt["markets"]["UPC"], symbol="UPC"))
    det = {"UPC/USDT": {"price": up[-1], "direction": "LONG", "score": 70,
                        "zones": {"d1": AN.daily_zones(daily["UPC"], up[-1])}},
           "DNC/USDT": {"price": down[-1], "direction": "SHORT", "score": 55,
                        "zones": {"d1": AN.daily_zones(daily["DNC"], down[-1])}},
           "BTC/USDT": {"price": btc_px[-1], "direction": "LONG", "score": 50, "zones": {}}}
    rep = AN.build_report(alt, det, daily, None, 1_791_510_000, calls=[])
    toks = rep.get("tokens") or {}
    if set(toks) != {"UPC", "DNC", "BTC"}:
        fails.append(f"tokeni: lipsesc rapoarte per token ({sorted(toks)})")
        return
    up_t, dn_t, bt = toks["UPC"], toks["DNC"], toks["BTC"]
    macro_bear = rep["strategy"]["sentiment"] == "Bearish"
    if not macro_bear and up_t["strategy"]["sentiment"] != "Bullish":
        fails.append(f"tokeni: peste MA200 si bate BTC pe 7 si 30 de zile, dar nu e Bullish ({up_t['strategy']})")
    if dn_t["strategy"]["sentiment"] != "Bearish":
        fails.append(f"tokeni: sub MA200 si pierde fata de BTC, dar nu e Bearish ({dn_t['strategy']})")
    for b, t in toks.items():
        for txt in (t["sector"]["catalyst"], t["alpha"]["argument"], t["alpha"]["risk"]):
            if len(str(txt).split()) > AN.WORDS:
                fails.append(f"tokeni: {b} are un text de peste 10 cuvinte: {txt}")
    sup = up_t["alpha"].get("support")
    if sup and up_t["strategy"].get("inv_price") != sup["lo"]:
        fails.append("tokeni: invalidarea nu e baza suportului 1D")
    if up_t["sector"]["name"] != "AI" or "AI" not in up_t["sector"]["catalyst"]:
        fails.append(f"tokeni: sectorul cu momentum al tokenului nu e recunoscut ({up_t['sector']})")
    if "aliniat" not in (up_t["strategy"].get("alignment") or ""):
        fails.append(f"tokeni: alinierea cu semnalul scanerului lipseste ({up_t['strategy'].get('alignment')})")
    if bt["sector"]["text"].find("referința") < 0 or bt["strategy"]["sentiment"] != rep["strategy"]["sentiment"]:
        fails.append("tokeni: BTC nu e tratat ca referinta pietei")
    # piata Bearish -> toti tokenii Bearish
    bear_strat = dict(rep["strategy"], sentiment="Bearish")
    t_bear = AN.token_report("UPC", det["UPC/USDT"], alt["markets"]["UPC"], ind, rep["sectors"]["all"], {"artificial-intelligence"},
                             daily["UPC"], daily["BTC"], rep["macro"], bear_strat)
    if t_bear["strategy"]["sentiment"] != "Bearish":
        fails.append("tokeni: cu piata Bearish, un token a primit alt sentiment")
    # verificarea masurata: un verdict Bullish urmat de +10% fata de BTC se puncteaza corect
    day0 = "2026-01-01"
    t_ms = lambda d: int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    days = [(datetime(2026, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(40)]
    btc_c = [[t_ms(d), 100, 100, 100, 100.0, 1] for d in days]
    tok_c = [[t_ms(d), 10, 10, 10, 10.0 * (1 + 0.1 * min(i, 30) / 30), 1] for i, d in enumerate(days)]
    calls = AN.record_calls([], day0, {"UPC": {"price": 10.0, "strategy": {"sentiment": "Bullish"}, "alpha": {"rr": 2.0}}}, 100.0)
    sc = AN.score_calls(calls, {"BTC": btc_c, "UPC": tok_c})
    h30 = (sc["horizons"].get("30") or {}).get("Bullish") or {}
    if h30.get("n") != 1 or abs((h30.get("mean_rel") or 0) - 10.0) > 0.01 or h30.get("hit") != 100.0:
        fails.append(f"tokeni: verificarea verdictelor la 30 de zile e gresita ({sc})")
    again = AN.record_calls(calls, day0, {"UPC": {"price": 11.0, "strategy": {"sentiment": "Neutru"}, "alpha": {}}}, 101.0)
    if len(again) != 1 or again[0]["calls"]["UPC"][0] != "Neutru":
        fails.append("tokeni: verdictul zilei nu e inlocuit la scanarile din aceeasi zi")
    # afisare: pasii 1-4 pe card, lista in raport, invalidarea pe grafic
    html = render_token_steps(up_t)
    for lbl in ("Diagnostic", "Sector", "Catalizator", "Sustenabilitate", "Filtrare alfa", "Argument", "Risc Critic",
                "Strategie", "Ghid de Intrare", "Trigger de Invalidare"):
        if lbl not in html:
            fails.append(f"tokeni: lipseste eticheta '{lbl}' pe cardul tokenului")
    full = render_analyst(rep, {"UPC": "exchanges/okx.html#tok-UPC-USDT"})
    if "PAȘII 1–4 PENTRU FIECARE TOKEN" not in full or 'href="exchanges/okx.html#tok-UPC-USDT"' not in full:
        fails.append("tokeni: lista tokenilor sau legatura spre card lipseste din raport")
    cands = {"symbols": {"UPC/USDT": dict(det["UPC/USDT"], candles=daily["UPC"][-80:], score=70)}}
    page = render_token_details(cands, {}, None, {}, True, "OKX", analyst_tokens=toks)
    if "Analist &middot; pașii 1–4" not in page or "analist Bullish" not in page:
        fails.append("tokeni: pasii 1-4 lipsesc de pe cardul tokenului din pagina bursei")
    print(f"  5. tokeni: {len(toks)} rapoarte (UPC {up_t['strategy']['sentiment']}, DNC {dn_t['strategy']['sentiment']}, "
          f"BTC referinta), invalidare la baza suportului, verdicte verificate la 30 de zile, afisare pe card")


def main():
    fails = []
    check_zones(fails)
    check_placebo(fails)
    rep = check_report(fails)
    check_render(fails, rep)
    check_tokens(fails)
    print("\nANALIST: " + ("RESPINS - " + "; ".join(fails) if fails else "TRECUT"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
