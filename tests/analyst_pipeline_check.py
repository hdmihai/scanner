# -*- coding: utf-8 -*-
"""tests.analyst_pipeline_check - lantul de productie al analistului, offline, cap-coada.

Poarta de echivalenta inlocuieste altseason.update cu starea salvata, deci codul care aduce
sectoarele, subsetul de piete si seriile zilnice (BTC.D, indicele) ruleaza doar in productie.
Aici ruleaza real, pe copii temporare ale fisierelor din data/, cu CoinGecko si bursa simulate:

  altseason.update x3   - sectoarele se completeaza prin rotatie (4 pe scanare), seria BTC.D
                          porneste din istoricul orar real, perf90 pe ziua inchisa;
  analyst.update        - lumanarile zilnice ale candidatilor, raportul in 4 sectiuni;
  afisarea              - toate sectiunile, pretul curent pe graficul BTC 1D.

Datele simulate au sectoare construite sa bata BTC (AI, Meme), o moneda cu acelasi ticker dar alt
pret pe bursa (alt activ) si preturi coerente intre CoinGecko si bursa pentru restul. Nicio
cerere in retea; fisierele din data/ nu se modifica.

Rulare: python3 -m tests.analyst_pipeline_check
"""

import json
import math
import os
import random
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import altseason as A                      # noqa: E402
import altseason_history as AH             # noqa: E402
import analyst as AN                       # noqa: E402
from core import zones as Z                # noqa: E402
from dashboard.sections.analyst import render_analyst  # noqa: E402

NEED = ("altseason.json", "altseason_history.json", "altseason_cycles.json", "latest_details.json")


def _walk(seed, n, p_last, vol):
    """Lumanari zilnice aleatoare care se termina exact la `p_last`."""
    r = random.Random(seed)
    out, p, t0 = [], 1.0, int(time.time() // 86400 - n + 1) * 86400000
    for i in range(n):
        o, c = p, p * math.exp(r.gauss(0, vol))
        out.append([t0 + i * 86400000, o, max(o, c) * (1 + abs(r.gauss(0, vol / 3))),
                    min(o, c) * (1 - abs(r.gauss(0, vol / 3))), c, 1e6])
        p = c
    k = p_last / out[-1][4]
    return [[x[0]] + [v * k for v in x[1:5]] + [x[5]] for x in out]


def main():
    if not all(os.path.exists(os.path.join("data", f)) for f in NEED):
        print("LANTUL ANALISTULUI: SARIT (lipsesc fisierele din data/)")
        return 0
    tmp = tempfile.mkdtemp()
    for f in NEED:
        shutil.copy(os.path.join("data", f), os.path.join(tmp, f))
    A.STATE_FILE, A.HISTORY_FILE = os.path.join(tmp, "altseason.json"), os.path.join(tmp, "altseason_history.json")
    AH.STATE_FILE = os.path.join(tmp, "altseason_cycles.json")
    AH.daily_update = lambda st, ex: False      # fara retea: istoricul pe 10 ani ramane cel copiat
    AN.REPORT_FILE = os.path.join(tmp, "analyst.json")
    AN.CALLS_FILE = os.path.join(tmp, "analyst_calls.json")
    fails = []
    try:
        cy = json.load(open(AH.STATE_FILE))
        btc_cl = [cy["window_px"]["btc"][d] for d in sorted(cy["window_px"]["btc"])]

        def btc_daily(n):
            cl = btc_cl[-n:]
            t0 = int(time.time() // 86400 - len(cl) + 1) * 86400000
            return [[t0 + i * 86400000, p, max(p, c) * 1.01, min(p, c) * 0.99, c, 1e4]
                    for i, (p, c) in enumerate(zip([cl[0]] + cl[:-1], cl))]

        coins = [("bitcoin", "btc", 1.6e12, btc_cl[-1]), ("ethereum", "eth", 3.0e11, 2500.0),
                 ("tether", "usdt", 1.8e11, 1.0)]
        coins += [(f"coin{i}", f"c{i}", 2e11 / (i ** 1.3), 10.0 / i) for i in range(4, 251)]

        def mk(cid, sym, cap, px, bias=0.0):
            r = random.Random(cid)
            rets = {k: (r.gauss(bias * m, s) if cid != "tether" else 0.0)
                    for k, m, s in (("7d", 3, 6), ("30d", 10, 15), ("200d", 20, 40))}
            if cid == "bitcoin":
                rets = {"7d": -2.9, "30d": 4.2, "200d": 10.0}
            return {"id": cid, "symbol": sym, "name": cid.title(), "market_cap": cap, "current_price": px,
                    "total_volume": cap * 0.05, "fully_diluted_valuation": cap / 0.9, "circulating_supply": 9e8,
                    "total_supply": 1e9, "max_supply": None, "ath_change_percentage": -40.0,
                    **{f"price_change_percentage_{k}_in_currency": v for k, v in rets.items()}}

        markets = [dict(mk(*c), market_cap_rank=k + 1) for k, c in enumerate(coins)]
        calls = {"cg": 0, "sector": 0}

        def fake_get(url, retries=2):
            calls["cg"] += 1
            if url.endswith("/global"):
                return {"data": {"market_cap_percentage": {"btc": 59.1, "eth": 11.4},
                                 "total_market_cap": {"usd": 2.7e12}, "total_volume": {"usd": 9e10},
                                 "market_cap_change_percentage_24h_usd": -1.1}}
            return markets

        def fake_light(url):
            calls["sector"] += 1
            cat = url.split("category=")[1].split("&")[0]
            bias = {"artificial-intelligence": 1.4, "meme-token": 0.9}.get(cat, -0.4)
            return [dict(mk(c["id"], c["symbol"], c["market_cap"], c["current_price"], bias),
                         market_cap_rank=c["market_cap_rank"])
                    for c in random.Random(cat).sample(markets[3:120], 30)]

        A._get, A._get_light = fake_get, fake_light
        px = {c[1].upper(): c[3] for c in coins}
        odd = set()

        class FakeExchange:
            markets = {f"{c[1].upper()}/USDT": {} for c in coins}

            def fetch_ohlcv(self, sym, timeframe="1d", limit=100):
                base = sym.split("/")[0]
                if base == "BTC":
                    return btc_daily(limit)
                return _walk(base, limit, px.get(base, 10.0) * (40 if base in odd else 1), 0.04)

        ex = FakeExchange()
        details = (json.load(open(os.path.join(tmp, "latest_details.json"))).get("symbols") or {})
        results = [{"symbol": s} for s in details]
        st = None
        for k in range(3):
            st = A.update(exchange=ex, scan_results=results)
            if not st or st.get("stale"):
                fails.append(f"altseason.update #{k + 1}: stare invalida ({(st or {}).get('last_error')})")
                break
        if st and not fails:
            sec = st.get("sectors") or {}
            if len(sec.get("items") or {}) != len(A.SECTORS) or sec.get("total") != len(A.SECTORS):
                fails.append(f"sectoarele nu s-au completat in 3 scanari ({len(sec.get('items') or {})})")
            if calls["sector"] != len(A.SECTORS):
                fails.append(f"{calls['sector']} cereri de sector in 3 scanari, nu {len(A.SECTORS)} (rotatia)")
            if len(st.get("btc_d_daily") or {}) < 5:
                fails.append("seria zilnica BTC.D nu a pornit din istoricul orar")
            if not {"BTC", "ETH"} <= set(st.get("markets") or {}):
                fails.append("subsetul de piete nu contine BTC si ETH")
            cand = [c["symbol"].upper() for c in st.get("candidates") or []]
            odd.update(cand[:1])
            daily = {s: _walk(s, 300, (d.get("price") or 1.0), 0.04) for s, d in list(details.items())[:10]}
            daily["BTC/USDT"] = btc_daily(300)
            zs = {"scan": Z.hold_stats({s: d.get("candles") or [] for s, d in details.items()}),
                  "d1": Z.hold_stats(daily), "tf": "4h"}
            rep = AN.update(ex, st, details, results, zs, daily)
            if rep.get("error"):
                fails.append(f"analist: {rep['error']}")
            else:
                if rep["macro"]["btc_d"].get("d7") is None:
                    fails.append("BTC.D fara schimbare pe 7 zile, desi istoricul orar acopera peste 7 zile")
                names = [s["name"] for s in rep["sectors"]["top"]]
                if "AI" not in names:
                    fails.append(f"sectorul construit sa bata BTC (AI) lipseste ({names})")
                if not any(p.get("rr") is not None for p in rep["alpha"]):
                    fails.append("niciun proiect alfa cu R:R din zonele 1D")
                for p in rep["alpha"]:
                    if p["ticker"] in odd and (not p.get("other_asset") or p.get("rr") is not None):
                        fails.append(f"{p['ticker']}: activul diferit de pe bursa a primit R:R")
                toks = rep.get("tokens") or {}
                if len(toks) < max(1, len(details) // 2):
                    fails.append(f"pasii 1-4 per token lipsesc: {len(toks)} rapoarte pentru {len(details)} tokeni")
                logged = json.load(open(AN.CALLS_FILE)) if os.path.exists(AN.CALLS_FILE) else []
                if not logged or len(logged[-1].get("calls") or {}) < len(toks) - 1:
                    fails.append("verdictele zilei per token nu au fost inregistrate")
                html = render_analyst(json.load(open(AN.REPORT_FILE)))
                for lbl in ("1. DIAGNOSTIC MACRO", "2. SECTOARE MOMENTUM", "3. FILTRARE ALFA",
                            "4. STRATEGIE EXECUTIVĂ", "PRET ACUM"):
                    if lbl not in html:
                        fails.append(f"afisare: lipseste {lbl}")
                print(f"  altseason x3: {len(sec['items'])} sectoare, {calls['cg']} cereri CoinGecko + "
                      f"{calls['sector']} de sector; BTC.D {len(st['btc_d_daily'])} zile")
                print(f"  analist: BTC.D {rep['macro']['btc_d']['status']}, sectoare {names}, alfa "
                      f"{[(p['ticker'], p['rr']) for p in rep['alpha']]}, sentiment {rep['strategy']['sentiment']}, "
                      f"{len(toks)} tokeni cu pasii 1-4, {len(logged[-1]['calls']) if logged else 0} verdicte inregistrate")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nLANTUL ANALISTULUI: " + ("RESPINS - " + "; ".join(fails) if fails else "TRECUT"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
