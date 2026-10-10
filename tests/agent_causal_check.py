# -*- coding: utf-8 -*-
"""tests.agent_causal_check - evaluarea cauzala a agentului, ritmul de invatare si briefing-ul pe doua sectiuni.

Uz (din radacina repo-ului): python -m tests.agent_causal_check

  1. FARA PRIVIRE IN VIITOR: predictia pentru un plan e cea a modelului de la CREAREA lui - nu invata
     din planurile inchise cat timp el era deschis (inainte, ordinea inchiderii umfla AUC-ul);
  2. planurile live folosesc predictia inregistrata la decizie;
  3. ritmul de invatare scade cu memoria si are un minim;
  4. pe planurile reale: evaluarea cauzala da o ordonare mai slaba decat cea veche (dovada ca vechea
     era umflata), iar reantrenarea se declanseaza o singura data;
  5. briefing: sectiunea "date reale" contine doar planuri live, "istoric simulat" doar backtest.
Nu scrie nimic in repo.
"""

import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("SCAN_TIMEFRAME", "4h")

import ai_agent  # noqa: E402,F401  (leaga sursele de excludere)
import briefing as BR  # noqa: E402
import plan_tracker as PT  # noqa: E402
from core import agent as A  # noqa: E402

FAILS = []


def ok(cond, label):
    print(f"  {'OK ' if cond else 'ERR'} {label}")
    if not cond:
        FAILS.append(label)


def _plan(i, created, closed, r, comp, source="backtest", agent_prob=None):
    p = {"id": i, "symbol": "X/USDT", "direction": "LONG", "created_ts": created, "closed_ts": closed,
         "realized_r": r, "state": "TP2_HIT" if r > 0 else "SL_HIT", "geometry": PT.GEOMETRY_VERSION,
         "components": comp, "score_at_entry": 50, "source": source}
    if agent_prob is not None:
        p["decision"] = {"agent_prob": agent_prob}
    return p


def main():
    print("1-2. fara privire in viitor")
    comp = {"trend": 1.0, "ev_macd": 1.0}
    # A: deschis la t=0, inchis la t=100. B..K: create si inchise castigatoare CAT TIMP A e deschis.
    plans = [_plan(1, 0, 100, -1.0, comp)] + [_plan(10 + k, 1 + k, 2 + k, 3.0, comp) for k in range(30)]
    st, m = A.default_state(), A.OnlineLogisticRegression()
    A.train_from_plans(plans, m, st)
    pa = [pp for pp in st["pairs"] if pp[2] == -1.0][0][0]
    ok(abs(pa - 0.5) < 1e-9, f"planul A e prezis cu modelul de la crearea lui (0.5), nu dupa 30 de castiguri ({pa:.3f})")
    plans = [_plan(1, 0, 100, -1.0, comp, source="live", agent_prob=0.123)] + \
        [_plan(10 + k, 1 + k, 2 + k, 3.0, comp) for k in range(5)]
    st, m = A.default_state(), A.OnlineLogisticRegression()
    A.train_from_plans(plans, m, st)
    ok(any(abs(pp[0] - 0.123) < 1e-9 for pp in st["pairs"]), "planul live: predictia inregistrata la decizie")

    print("3. ritmul de invatare")
    lrs = [A.learning_rate(n) for n in (0, 1000, 16000, 10 ** 7)]
    ok(lrs[0] == A.LR0 and lrs[0] > lrs[1] > lrs[2] and lrs[3] == A.LR_MIN,
       f"scade cu memoria si are minim: {[round(x, 5) for x in lrs]}")

    print("4. pe planurile reale")
    store = json.load(open(os.path.join("data", "plans.json")))
    real = [p for p in store["plans"] if p.get("realized_r") is not None][-6000:]
    for p in real:
        p.pop("agent_trained", None)
    st, m = A.default_state(), A.OnlineLogisticRegression()
    A.train_from_plans(copy.deepcopy(real), m, st)
    auc_c = A.auc_score(st["pairs"])
    # vechea evaluare: in ordinea inchiderii, lr fix 0.05
    st2, m2 = A.default_state(), A.OnlineLogisticRegression()
    pairs = []
    for p in sorted([p for p in real if p.get("state") != "NO_ENTRY" and PT.same_family(p.get("geometry"))],
                    key=lambda p: p.get("closed_ts") or 0):
        x = A.extract_features(p)
        y = 1.0 if p["realized_r"] > 0 else 0.0
        pairs.append([m2.predict_proba(x), y])
        m2.learn_one(x, y)
    auc_o = A.auc_score(pairs)
    ok(auc_c is not None and auc_o is not None and auc_c < auc_o,
       f"evaluarea cauzala ({auc_c:.3f}) e sub cea in ordinea inchiderii ({auc_o:.3f}) - vechea era umflata")
    ok(st.get("eval_version") == A.EVAL_VERSION and st.get("learning_rate") is not None,
       f"starea poarta versiunea evaluarii si ritmul curent ({st.get('learning_rate')})")

    print("5. briefing in doua sectiuni")
    f = BR.gather_facts()
    sec = BR.sections(f)
    lv = [p for p in store["plans"] if p.get("source") != "backtest" and p.get("realized_r") is not None
          and p.get("state") != "NO_ENTRY" and PT.same_family(p.get("geometry"))]
    bt = [p for p in store["plans"] if p.get("source") == "backtest" and p.get("realized_r") is not None
          and p.get("state") != "NO_ENTRY" and PT.same_family(p.get("geometry"))]
    ok((f["real"]["all"] or {}).get("n", 0) == len(lv) and (f["simulated"]["all"] or {}).get("n", 0) == len(bt),
       f"date reale = doar live ({len(lv)}), istoric simulat = doar backtest ({len(bt)})")
    ok(sec["real"] and sec["simulated"] and sec["agent"], "ambele sectiuni si starea agentului sunt generate")
    print("\nEVALUARE CAUZALA: " + ("RESPINS - " + "; ".join(FAILS) if FAILS else "TRECUT"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
