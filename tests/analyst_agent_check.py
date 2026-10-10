# -*- coding: utf-8 -*-
"""tests.analyst_agent_check - legatura Analist Web3 -> agent (evidenta `analyst`, ev_analyst).

Uz (din radacina repo-ului): python -m tests.analyst_agent_check

  1. REGULI: core/relative.verdict da Bullish / Bearish / Neutru exact dupa regulile pasului 4
     (MA200 1D, forta relativa fata de BTC pe 7 si 30 de zile), cu aceeasi formula ca analistul;
  2. O SINGURA DEFINITIE: cardul analistului (token_report) afiseaza verdictul din aceeasi functie
     din care agentul primeste evidenta, pe aceleasi lumanari zilnice;
  3. EVIDENTA: Bullish sustine LONG, Bearish sustine SHORT, Neutru nimic; lipseste pentru BTC si
     fara date; NU schimba scorul de fuziune (ev_fusion);
  4. FARA EFECT PANA LA BACKTEST: pe modelul real, cu planurile reale (fara ev_analyst in
     backtest), ev_analyst e in asteptare - predictia si vecinii raman identici, cu sau fara
     evidenta; masca de caracteristici nu se schimba (deci nici reantrenare);
  5. BACKTEST: verdictul la fiecare bara se calculeaza din zilele inchise ale tokenului si ale BTC
     pentru aceeasi zi (fara privire in viitor) si e identic cu cel calculat independent.
Nu scrie nimic in repo.
"""

import calendar
import json
import math
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "fakes"))      # ccxt simulat, pentru backtest.py
os.chdir(ROOT)
os.environ.setdefault("SCAN_TIMEFRAME", "4h")

from core import relative as REL           # noqa: E402
from core import evidence as EV            # noqa: E402

FAILS = []


def ok(cond, label):
    print(f"  {'OK ' if cond else 'ERR'} {label}")
    if not cond:
        FAILS.append(label)


def series(n, drift, seed, start=100.0, vol=0.01):
    r = random.Random(seed)
    out, p = [], start
    for _ in range(n):
        p *= math.exp(drift + r.gauss(0, vol))
        out.append(p)
    return out


def check_rules():
    print("1. reguli")
    btc = series(260, 0.0, 1)
    up = series(260, 0.004, 2)                       # peste MA200, bate BTC
    down = series(260, -0.004, 3)                    # sub MA200, pierde fata de BTC
    v_up, v_dn = REL.verdict(up, btc), REL.verdict(down, btc)
    ok(v_up and v_up["sentiment"] == "Bullish" and v_up["above_ma200"], f"trend + forta relativa -> Bullish ({v_up})")
    ok(v_dn and v_dn["sentiment"] == "Bearish" and v_dn["above_ma200"] is False, "sub MA200 si pierde -> Bearish")
    # peste MA200 dar pierde pe 7 zile -> Neutru
    mixed = up[:-7] + [up[-8] * (1 - 0.01 * k) for k in range(1, 8)]
    v_mx = REL.verdict(mixed, btc)
    ok(v_mx and v_mx["sentiment"] == "Neutru", f"peste MA200, pierde pe 7 zile -> Neutru ({v_mx and v_mx['rs7']})")
    ok(REL.verdict(up[:100], btc[:100]) is None, "fara 150 de zile pentru MA200 -> fara verdict")
    # formula fortei relative = cea a analistului
    from core import analyst as AN
    c = [[i * 86400000, 0, 0, 0, x, 0] for i, x in enumerate(up)]
    b = [[i * 86400000, 0, 0, 0, x, 0] for i, x in enumerate(btc)]
    ok(abs(REL.rs(up, btc, 30) - AN._rs_candles(c, b, 30)) < 1e-9, "forta relativa identica cu formula analistului")
    ok(REL.from_candles(c[:-1], b) is None, "serii care nu se termina in aceeasi zi -> fara verdict")
    return c, b


def check_display(c, b):
    print("2. o singura definitie (card = agent)")
    from core import analyst as AN
    rv = REL.from_candles(c, b, c[-1][4])
    t = AN.token_report("TST", {"price": c[-1][4]}, {"r7": -50.0, "r30": -50.0, "rank": 50},
                        {"btc_r7": 0.0, "btc_r30": 0.0}, [], set(), c, b, {}, {"sentiment": "Neutru"})
    ok(t["strategy"]["sentiment"] == rv["sentiment"],
       f"sentimentul afisat ({t['strategy']['sentiment']}) = verdictul agentului ({rv['sentiment']}), "
       "desi CoinGecko spune altceva")
    ok(abs(t["diag"]["rs30"] - rv["rs30"]) < 0.06 and "închideri zilnice" in t["diag"]["source"],
       f"forta relativa afisata vine din lumanarile zilnice ({t['diag']['source']})")
    t2 = AN.token_report("TST", {"price": c[-1][4]}, {"r7": 5.0, "r30": 5.0, "rank": 50},
                         {"btc_r7": 0.0, "btc_r30": 0.0}, [], set(), None, None, {}, {"sentiment": "Neutru"})
    ok(t2["diag"]["source"] == "CoinGecko", "fara lumanari zilnice, afisarea ramane pe CoinGecko")


def _ind(price):
    return {"emas": {"ema9": price * 1.01, "ema20": price, "ema50": price * 0.99, "ema100": price * 0.98,
                     "ema200": price * 0.97}}


def check_evidence():
    print("3. evidenta")
    rel_b = {"sentiment": "Bullish", "rs7": 3.0, "rs30": 12.0, "above_ma200": True}
    rel_s = {"sentiment": "Bearish", "rs7": -3.0, "rs30": -12.0, "above_ma200": False}
    base = EV.build_evidence(_ind(10.0), 10.0, 0.2, symbol="ETH/USDT", direction="LONG")
    withb = EV.build_evidence(_ind(10.0), 10.0, 0.2, symbol="ETH/USDT", direction="LONG", rel=rel_b)
    an = [e for e in withb if e["key"] == "analyst"]
    ok(len(an) == 1 and an[0]["direction"] == "LONG", "Bullish -> sustine LONG")
    fl = EV.evidence_features(withb, "LONG")["ev_analyst"]
    fs = EV.evidence_features(EV.build_evidence(_ind(10.0), 10.0, 0.2, symbol="ETH/USDT", direction="SHORT",
                                                rel=rel_s), "SHORT")["ev_analyst"]
    fx = EV.evidence_features(EV.build_evidence(_ind(10.0), 10.0, 0.2, symbol="ETH/USDT", direction="SHORT",
                                                rel=rel_b), "SHORT")["ev_analyst"]
    ok(fl == 1.0 and fs == 1.0 and fx == -1.0, f"orientare dupa directie: {fl}, {fs}, {fx}")
    neu = EV.build_evidence(_ind(10.0), 10.0, 0.2, symbol="ETH/USDT", direction="LONG",
                            rel={"sentiment": "Neutru", "rs7": 1.0, "rs30": -2.0, "above_ma200": True})
    ok(EV.evidence_features(neu, "LONG")["ev_analyst"] == 0.0, "Neutru -> 0")
    btc = EV.build_evidence(_ind(10.0), 10.0, 0.2, symbol="BTC/USDT", direction="LONG", rel=rel_b)
    ok(not any(e["key"] == "analyst" for e in btc), "fara evidenta pentru BTC")
    ok(EV.fusion(base, "LONG") == EV.fusion(withb, "LONG"), "scorul de fuziune nu se schimba")
    ok(EV.evidence_features(base, "LONG")["ev_fusion"] == EV.evidence_features(withb, "LONG")["ev_fusion"],
       "ev_fusion identic")


def check_agent():
    print("4. fara efect pana la backtest (model si planuri reale)")
    import ai_agent as AG
    from core import agent as CA
    plans = json.load(open(os.path.join("data", "plans.json")))["plans"]
    pend = CA.backtest_pending(plans)
    has_bt = any(p.get("source") == "backtest" for p in plans)
    bt_has = any("ev_analyst" in (p.get("components") or {}) for p in plans if p.get("source") == "backtest")
    if has_bt and not bt_has:
        ok(pend == ["ev_analyst"], f"ev_analyst in asteptare ({pend})")
    ok(CA.backtest_pending([{"source": "backtest", "components": {k: 0.0 for k in CA.ev_mod.FEATURE_KEYS}}]) == [],
       "backtest re-rulat (cu cheia) -> nimic in asteptare")
    ok(CA.backtest_pending([{"source": "live", "components": {}}]) == [], "fara backtest -> nimic de comparat")
    if has_bt and bt_has:
        # dupa reconstructia memoriei backtest-ul contine ev_analyst: caracteristica e ACTIVA
        _, st = AG.load_agent()
        ok("ev_analyst" not in (st.get("pending_features") or []) and not pend,
           "backtest-ul regenerat contine ev_analyst -> caracteristica activa, invatata pe tot istoricul")
        return
    ok("ev_analyst" in AG.excluded_features(), "excluderea activa inca de la prima scanare (PENDING_DEFAULT)")
    model, state = AG.load_agent()
    skew = CA.feature_skew(plans)
    mask = sorted(set(CA.skew_excluded(skew)) | set(AG._quarantined()))
    ok(mask == (state.get("feature_mask") or mask) or "ev_analyst" not in mask,
       f"masca de caracteristici nu include ev_analyst ({mask})")
    live = [p for p in plans if p.get("source") != "backtest" and p.get("evidence")][-40:]
    closed = [p for p in plans if p.get("realized_r") is not None][-3000:]
    same_p, same_n = 0, 0
    for p in live:
        sig = {**p, "evidence": p["evidence"], "components": {k: v for k, v in (p.get("components") or {}).items()
                                                              if not k.startswith("ev_")}}
        a = CA.predict_for_signal(model, state, sig)
        ev2 = p["evidence"] + [{"key": "analyst", "label": "x", "direction": p["direction"], "strength": 1.0,
                                "value": 10.0}]
        b = CA.predict_for_signal(model, state, {**sig, "evidence": ev2})
        same_p += (a or {}).get("probability") == (b or {}).get("probability")
    for p in live[:5]:
        f1 = CA.extract_features({**p, "components": {}})
        f2 = CA.extract_features({**p, "components": {}, "evidence": p["evidence"] + [
            {"key": "analyst", "label": "x", "direction": p["direction"], "strength": 1.0, "value": 1}]})
        same_n += CA.comparable_entries(f1, closed) == CA.comparable_entries(f2, closed)
    ok(live and same_p == len(live), f"predictia agentului identica cu/fara evidenta ({same_p}/{len(live)})")
    ok(same_n == min(5, len(live)), f"intrarile comparabile identice ({same_n}/{min(5, len(live))})")


def check_backtest():
    print("5. backtest")
    import backtest as BT
    cy = json.load(open(os.path.join("data", "altseason_cycles.json")))
    K = cy["timeline_keys"]
    tl = [dict(zip(K, r)) for r in cy["timeline"]]
    days = [r["day"] for r in tl][-420:]
    d0 = calendar.timegm(time.strptime(days[0], "%Y-%m-%d")) * 1000
    bar_ms = 4 * 3600000
    closes = series(len(days) * 6, 0.0008, 11, start=5.0, vol=0.006)
    candles = [[d0 + k * bar_ms, x, x * 1.002, x * 0.998, x, 1.0] for k, x in enumerate(closes)]
    htf = BT.htf_closes_index(candles, bar_ms)
    btc_by_day = {r["day"]: r["btc"] for r in tl}
    checked, bad, nonnull = 0, 0, 0
    for i in range(300 * 6, len(candles), 23):
        rv = BT.rel_for_bar("TST/USDT", htf, i, candles[i][0], bar_ms, candles[i][4])
        # independent: zilele complet inchise la inchiderea barei i
        end = candles[i][0] + bar_ms
        last_day = time.strftime("%Y-%m-%d", time.gmtime(end / 1000 - 86400))
        tok = [candles[k][4] for k in range(len(candles)) if candles[k][0] + bar_ms <= end
               and (candles[k][0] + bar_ms) % 86400000 == 0]
        tok = tok[-201:]
        bdays = [d for d in btc_by_day if d <= last_day][-len(tok):]
        exp = REL.verdict(tok, [btc_by_day[d] for d in bdays], candles[i][4])
        checked += 1
        nonnull += rv is not None
        if (rv or {}).get("sentiment") != (exp or {}).get("sentiment") or (
                rv and abs(rv["rs30"] - exp["rs30"]) > 1e-9):
            bad += 1
    ok(checked > 20 and bad == 0 and nonnull == checked,
       f"verdictul din backtest = calculul independent pe zile inchise ({checked} bare, {bad} diferente)")
    ok(BT.rel_for_bar("BTC/USDT", htf, len(candles) - 1, candles[-1][0], bar_ms, 1.0) is None, "BTC -> fara verdict")


def main():
    c, b = check_rules()
    check_display(c, b)
    check_evidence()
    check_agent()
    check_backtest()
    print("\nANALIST -> AGENT: " + ("RESPINS - " + "; ".join(FAILS) if FAILS else "TRECUT"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
