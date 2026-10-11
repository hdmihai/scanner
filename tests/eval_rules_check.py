# -*- coding: utf-8 -*-
"""tests.eval_rules_check - regulile de evaluare a planurilor (core/plans.EVAL_RULES = 2).

Uz (din radacina repo-ului): python -m tests.eval_rules_check

  1. REGULILE, pe planuri construite de mana (LONG si SHORT): tinta pe bara intrarii nenumarata (in
     afara de umplerea la deschidere), TP1 + intoarcere la intrare in aceeasi bara -> breakeven,
     TP2 prin TP1 (jumatate-jumatate), SL inaintea tintei, breakeven dupa TP1;
  2. TIMPUL PE GRILA: asteptarea intrarii (8 bare) si durata maxima (48 de bare de la intrare) sunt
     aceleasi cand barele vin cate una (backtest) si toate odata (live); planul live creat in
     mijlocul barei, cu bara partiala din lumanari de 5 minute, are aceleasi bare ca backtest-ul;
  3. PROPRIETATI pe 3000 de planuri si drumuri de pret aleatoare: o bara pe apel = toate odata =
     bucati aleatoare (scanari succesive); orice rezultat inchis trece verificarile de stare ale
     auditului; R-ul niciodata peste cel dat de niveluri;
  4. SCANAREA (core/scan.evaluate_live_plans, bursa simulata): planul live evaluat cu reguli vechi
     se reevalueaza o data (rezultatul vechi ramane pe plan, agentul se reantreneaza daca il
     invatase); fara lumanari de 5 minute planul asteapta, apoi renunta la bara partiala dupa
     24 de ore; planul orfan nu foloseste bara in formare;
  5. AUDITUL: un plan reevaluat (cu bara partiala) nu e marcat; unul cu rezultat inventat e;
     un plan cu reguli vechi nu e reevaluat (diferenta ar fi a regulilor, nu o data falsa);
  6. BACKTEST-UL: planul e creat la inchiderea barei de semnal si poarta versiunea regulilor.
Nu scrie nimic in repo si nu face retea.
"""

import copy
import math
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "fakes"))      # ccxt simulat, pentru backtest.py
os.chdir(ROOT)
os.environ["SCAN_TIMEFRAME"] = "4h"

from core import plans as P             # noqa: E402
from core import plan_audit as A        # noqa: E402

FAILS = []
H = 14400
T0 = 1790000000 // H * H                 # o deschidere de bara 4h


def ok(cond, label):
    print(f"  {'OK ' if cond else 'ERR'} {label}")
    if not cond:
        FAILS.append(label)


def plan(direction="LONG", created=T0 + H, **kw):
    """entry 100, SL 95 (risc 5), TP1 107.5 (1.5R), TP2 120 (4R); SHORT in oglinda."""
    if direction == "LONG":
        lv = dict(entry=100.0, sl=95.0, tp1=107.5, tp2=120.0)
    else:
        lv = dict(entry=100.0, sl=105.0, tp1=92.5, tp2=80.0)
    p = {"id": 1, "symbol": "TST/USDT", "direction": direction, "created_ts": created, "state": P.STATE_PENDING,
         "realized_r": None, "closed_ts": None, "bars_checked": 0, "bar_seconds": H, "er": P.EVAL_RULES,
         "risk": 5.0, **lv}
    p.update(kw)
    return p


def bar(k, o, h, l, c, start=T0 + H):
    return [int((start + k * H) * 1000), o, h, l, c, 1.0]


def mirror(b):
    """Bara oglindita fata de 100 (pentru SHORT): maximul devine minimul."""
    t, o, h, l, c, v = b
    return [t, 200 - o, 200 - l, 200 - h, 200 - c, v]


def run(p, bars, one_by_one=False):
    q = copy.deepcopy(p)
    if one_by_one:
        for b in bars:
            P.evaluate_plan(q, [b])
            if q["state"] in P.CLOSED_STATES:
                break
    else:
        P.evaluate_plan(q, bars)
    return q


COST = P.cost_in_r(100.0, 95.0)


def rules():
    print("1. regulile, pe planuri construite de mana")
    for d in ("LONG", "SHORT"):
        f = (lambda b: b) if d == "LONG" else mirror
        # B: intrare (minim 99) si TP1 (maxim 108) in aceeasi bara, deschidere 102 -> tinta nenumarata
        q = run(plan(d), [f(bar(0, 102, 108, 99, 101)), f(bar(1, 101, 103, 100.5, 102))])
        ok(q["state"] == P.STATE_OPEN and not q.get("tp1_hit_ts"),
           f"{d}: TP1 pe bara intrarii nu se numara (maximul putea veni inainte de umplere)")
        # B, exceptia: bara deschide dincolo de intrare (umplere la deschidere), apoi TP1 -> C: breakeven
        q = run(plan(d), [f(bar(0, 99.5, 108, 99, 101))])
        ok(q["state"] == P.STATE_SL and abs(q["gross_r"] - 0.5 * 1.5) < 1e-9 and q.get("tp1_hit_ts"),
           f"{d}: umplere la deschidere + TP1 in aceeasi bara -> jumatate la TP1, rest la breakeven (+0.75R)")
        # C: dupa intrare, o bara cu TP1 care atinge si intrarea -> breakeven in aceeasi bara
        q = run(plan(d), [f(bar(0, 102, 103, 99.5, 101)), f(bar(1, 101, 108, 99.8, 104))])
        ok(q["state"] == P.STATE_SL and abs(q["gross_r"] - 0.75) < 1e-9 and "aceeasi bara" in q["state_detail"],
           f"{d}: TP1 si intoarcerea la intrare in aceeasi bara -> breakeven (ambiguitate impotriva planului)")
        # D: TP2 atins direct (fara TP1 inainte) -> jumatate la TP1, jumatate la TP2
        q = run(plan(d), [f(bar(0, 102, 103, 99.5, 101)), f(bar(1, 101, 121, 100.5, 119))])
        ok(q["state"] == P.STATE_TP2 and abs(q["gross_r"] - (0.5 * 1.5 + 0.5 * 4.0)) < 1e-9
           and q.get("tp1_hit_ts") == bar(1, 0, 0, 0, 0)[0] / 1000.0,
           f"{d}: TP2 prin TP1 in aceeasi bara -> +2.75R (nu +4R), TP1 inregistrat")
        # A: SL si TP2 in aceeasi bara -> SL
        q = run(plan(d), [f(bar(0, 102, 103, 99.5, 101)), f(bar(1, 101, 121, 94, 100))])
        ok(q["state"] == P.STATE_SL and q["gross_r"] == -1.0 and abs(q["realized_r"] - round(-1 - COST, 3)) < 1e-9,
           f"{d}: SL si TP2 in aceeasi bara -> SL (-1R minus cost)")
        # breakeven dupa TP1 (pe bara urmatoare)
        q = run(plan(d), [f(bar(0, 102, 103, 99.5, 101)), f(bar(1, 101, 108, 100.5, 107)),
                          f(bar(2, 107, 107.2, 99.9, 100.5))])
        ok(q["state"] == P.STATE_SL and abs(q["gross_r"] - 0.75) < 1e-9 and q["closed_ts"] == bar(2, 0, 0, 0, 0)[0] / 1000,
           f"{d}: TP1, apoi intoarcere la intrare pe bara urmatoare -> breakeven")
        # TP1, apoi TP2 pe alta bara
        q = run(plan(d), [f(bar(0, 102, 103, 99.5, 101)), f(bar(1, 101, 108, 100.5, 107)),
                          f(bar(2, 107, 121, 106, 120))])
        ok(q["state"] == P.STATE_TP2 and abs(q["gross_r"] - 2.75) < 1e-9, f"{d}: TP1 apoi TP2 -> +2.75R")
        # SL pe bara intrarii (umplerea vine inaintea SL-ului: sigur, nu presupus)
        q = run(plan(d), [f(bar(0, 102, 103, 94, 96))])
        ok(q["state"] == P.STATE_SL and q["gross_r"] == -1.0, f"{d}: intrare si SL pe aceeasi bara -> -1R")


def grid():
    print("2. timpul pe grila barelor")
    flat = [bar(k, 103, 104, 101, 103) for k in range(60)]           # pretul nu revine la 100
    for mode in (False, True):
        q = run(plan(), flat, one_by_one=mode)
        ok(q["state"] == P.STATE_NO_ENTRY and q["closed_ts"] == flat[7][0] / 1000 and q["bars_checked"] == 8,
           f"fara intrare: NO_ENTRY dupa exact {P.MAX_WAIT_BARS} bare ({'o bara pe apel' if mode else 'toate odata'})")
    late = flat[:7] + [bar(7, 103, 104, 99.5, 101)] + [bar(k, 101, 102, 100.2, 101) for k in range(8, 80)]
    q = run(plan(), late)
    ok(q["state"] == P.STATE_EXPIRED and q["entered_ts"] == late[7][0] / 1000,
       "intrare chiar pe a 8-a bara de asteptare -> intra (si nu e anulat)")
    drift = [bar(0, 102, 103, 99.5, 101)] + [bar(k, 101, 102, 100.2, 101.5) for k in range(1, 70)]
    a, b = run(plan(), drift), run(plan(), drift, one_by_one=True)
    ok(a["state"] == b["state"] == P.STATE_EXPIRED and a["closed_ts"] == b["closed_ts"] == drift[47][0] / 1000
       and a["bars_checked"] == b["bars_checked"] == P.MAX_BARS and a["realized_r"] == b["realized_r"],
       f"expirare dupa {P.MAX_BARS} de bare de la intrare, identica o bara pe apel si toate odata "
       "(inainte backtest-ul nu expira niciodata)")
    # live: creat la 40 de minute dupa deschiderea barei; bara partiala din 5 minute
    created = T0 + H + 2400
    lp = plan(created=created)
    four_h = [bar(k, 103, 104, 101, 103) for k in range(0, 12)]       # bara 0 = bara de creare
    ok(not P.first_bar_due(lp, four_h[:0]) and P.first_bar_due(lp, four_h[:1]),
       "bara partiala ceruta doar dupa inchiderea barei de creare")
    ok(not P.first_bar_due(plan(), four_h) and not P.first_bar_due(dict(lp, source="backtest"), four_h),
       "nu se cere pentru planul creat pe grila (backtest) sau pentru backtest")
    sub = [[int((T0 + H + m * 300) * 1000), 103, 103.5, 102.5, 103, 0.1] for m in range(48)]
    sub[20] = [sub[20][0], 103, 103.2, 99.0, 102, 0.1]                # atinge intrarea la +100 min
    sub[3] = [sub[3][0], 103, 103.2, 98.0, 102, 0.1]                  # inainte de creare: nu conteaza
    fb = P.partial_first_bar(lp, sub)
    ok(fb and fb[0] >= created * 1000 and fb[3] == 99.0 and fb[1] == sub[8][1],
       "bara partiala: doar lumanarile incepute dupa creare (minimul de dinainte ignorat)")
    lp["first_bar"] = fb
    q = run(lp, P.with_first_bar(lp, four_h))
    ok(q["state"] == P.STATE_OPEN and q["entered_ts"] == fb[0] / 1000 and not P.first_bar_due(lp, four_h),
       "intrarea din restul barei de creare se numara (inainte toata bara era sarita)")
    lp2 = plan(created=created)
    lp2["first_bar"] = P.partial_first_bar(lp2, [[int((T0 + H + m * 300) * 1000), 103, 103.5, 102.5, 103, 0.1]
                                                 for m in range(48)])
    q = run(lp2, P.with_first_bar(lp2, four_h))
    ok(q["state"] == P.STATE_NO_ENTRY and q["closed_ts"] == four_h[7][0] / 1000,
       "live fara intrare: NO_ENTRY pe aceeasi bara ca backtest-ul (bara partiala + 7 bare)")
    ok(P.partial_first_bar(plan(created=T0 + 2 * H - 120), sub) == [], "creat in ultimele minute ale barei -> []")


def properties():
    print("3. proprietati pe planuri si drumuri aleatoare")
    rnd = random.Random(7)
    bad_split = bad_state = bad_cap = 0
    n_closed = 0
    states = {}
    for i in range(3000):
        d = rnd.choice(("LONG", "SHORT"))
        risk = rnd.uniform(0.5, 4)
        r1, r2 = rnd.uniform(1.0, 2.5), rnd.uniform(2.5, 8)
        s = 1 if d == "LONG" else -1
        e = 100.0
        p = plan(d, created=T0 + H + (rnd.choice((0, 0, 1800, 3000)) if i % 2 else 0),
                 entry=e, sl=e - s * risk, tp1=e + s * r1 * risk, tp2=e + s * r2 * risk, risk=risk)
        px, bars = 100 + s * rnd.uniform(0, 3), []
        for k in range(rnd.randint(5, 90)):
            o = px
            c = o * math.exp(rnd.gauss(0, 0.02))
            h, l = max(o, c) * (1 + abs(rnd.gauss(0, 0.012))), min(o, c) * (1 - abs(rnd.gauss(0, 0.012)))
            bars.append(bar(k, o, h, l, c))
            px = c
        if p["created_ts"] % H:
            a0, _ = P.creation_bar(p)
            p["first_bar"] = [int(math.ceil(p["created_ts"] * 1000)), bars[0][1], bars[0][2], bars[0][3], bars[0][4], 1.0]
            bars = bars[1:]
        cs = P.with_first_bar(p, bars)
        a = run(p, cs)
        b = run(p, cs, one_by_one=True)
        c_ = copy.deepcopy(p)
        k = 0
        while k < len(cs):                                       # scanari succesive, cu tot istoricul
            k += rnd.randint(1, 6)
            P.evaluate_plan(c_, cs[:k])
        key = lambda q: (q["state"], q.get("realized_r"), q.get("closed_ts"), q.get("entered_ts"), q.get("tp1_hit_ts"))
        if not (key(a) == key(b) == key(c_)):
            bad_split += 1
        states[a["state"]] = states.get(a["state"], 0) + 1
        if a["state"] in P.CLOSED_STATES:
            n_closed += 1
            why = A.check_state(a, time.time() + 10 ** 9, P.cost_in_r, P._r_at, P.TP1_FRACTION)
            if why:
                bad_state += 1
            cap = 0.5 * r1 + 0.5 * r2
            if a.get("gross_r") is not None and a["gross_r"] > cap + 0.0005 + 1e-9:     # R rotunjit la 3 zecimale
                bad_cap += 1
    ok(bad_split == 0, f"o bara pe apel = toate odata = scanari succesive ({3000 - bad_split}/3000; stari {states})")
    ok(bad_state == 0, f"toate cele {n_closed} rezultate inchise trec verificarile de stare ale auditului")
    ok(bad_cap == 0, "R brut niciodata peste jumatate TP1 + jumatate TP2")


class FakeEx:
    def __init__(self, series, sub=None, fail_sub=False):
        self.series, self.sub, self.fail_sub, self.calls = series, sub or {}, fail_sub, []

    def fetch_ohlcv(self, symbol, timeframe="4h", since=None, limit=None, params=None):
        self.calls.append((symbol, timeframe))
        if timeframe == P.FIRST_BAR_TF:
            if self.fail_sub:
                raise RuntimeError("429 Too Many Requests")
            rows = [r for r in self.sub.get(symbol, []) if since is None or r[0] >= since]
            return rows[:limit or len(rows)]
        return [list(r) for r in self.series.get(symbol, [])][-(limit or 100):]


def scan():
    print("4. scanarea: reevaluarea planurilor live si bara partiala")
    from core import scan as S
    created = T0 + H + 2400
    four_h = [bar(k, 103, 104, 101, 103) for k in range(0, 12)]
    four_h[2] = bar(2, 103, 104, 99.5, 101)                 # intrare pe a treia bara
    four_h[3] = bar(3, 101, 108, 100.5, 107)                # TP1
    four_h[4] = bar(4, 107, 121, 106, 120)                  # TP2
    sub = [[int((T0 + H + m * 300) * 1000), 103, 103.5, 102.5, 103, 0.1] for m in range(48)]
    now = (T0 + H) + 12 * H + 600
    # plan live evaluat cu regulile vechi: SL inregistrat (rezultat vechi), invatat de agent
    old = plan(created=created, er=None, state=P.STATE_SL, realized_r=-1.05, gross_r=-1.0, closed_ts=T0 + 3 * H,
               entered_ts=T0 + 3 * H, agent_trained=True, source="live")
    old.pop("er")
    fresh = plan(created=created, id=2, symbol="NEW/USDT", source="live")
    store = {"plans": [old, fresh, plan(source="backtest", id=3, state=P.STATE_SL, realized_r=-1.0, er=1)]}
    ex = FakeEx({}, {"TST/USDT": sub, "NEW/USDT": sub})
    cache = {"TST/USDT": four_h, "NEW/USDT": four_h}
    closed = S.evaluate_live_plans(store, ex, cache, now)
    o, f = store["plans"][0], store["plans"][1]
    ok(o["er"] == P.EVAL_RULES and o["state"] == P.STATE_TP2 and o["er_prev"]["state"] == P.STATE_SL
       and o["er_prev"]["realized_r"] == -1.05,
       f"planul live cu reguli vechi reevaluat de la creare: {o['er_prev']['state']} -> {o['state']} "
       f"{o['realized_r']:+.3f}R, rezultatul vechi pastrat pe plan")
    ok(store.get("retrain_reason") and "1 planuri" in store["retrain_reason"],
       "agentul invatase rezultatul vechi -> reantrenare ceruta (retrain_reason)")
    ok(o not in closed and f in closed, "doar planul inchis ACUM e raportat ca inchis in aceasta rulare")
    ok(isinstance(f.get("first_bar"), list) and ("NEW/USDT", P.FIRST_BAR_TF) in ex.calls,
       "bara partiala de creare ceruta o data si pastrata pe plan")
    ok(store["plans"][2]["er"] == 1, "planurile de backtest nu se ating in scanare (se regenereaza cu backtest-ul)")
    n_calls = len(ex.calls)
    S.evaluate_live_plans(store, ex, cache, now + 3600)
    ok(len(ex.calls) == n_calls, "la scanarea urmatoare: nicio cerere noua (bara partiala e deja pe plan)")
    # fara lumanari de 5 minute: planul asteapta; dupa 24 de ore renunta la bara partiala
    w = plan(created=created, id=4, source="live")
    st2 = {"plans": [w]}
    S.evaluate_live_plans(st2, FakeEx({}, fail_sub=True), {"TST/USDT": four_h[:2]}, T0 + 2 * H + 600)
    ok(w["state"] == P.STATE_PENDING and "first_bar" not in w, "5 minute indisponibile -> planul asteapta (reincearca)")
    S.evaluate_live_plans(st2, FakeEx({}, fail_sub=True), {"TST/USDT": four_h}, T0 + 2 * H + 25 * 3600)
    ok(w["state"] == P.STATE_PENDING and "first_bar" not in w,
       "o singura cerere esuata dupa 24 de ore nu ajunge: planul inca asteapta (2 incercari)")
    for k in range(P.FIRST_BAR_MAX_TRIES):
        S.evaluate_live_plans(st2, FakeEx({}, fail_sub=True), {"TST/USDT": four_h}, T0 + 2 * H + (26 + k) * 3600)
    ok(w.get("first_bar") is None and "first_bar" in w and w.get("first_bar_note") and w["state"] != P.STATE_PENDING,
       f"dupa {P.FIRST_BAR_MAX_TRIES} scanari esuate si 24 de ore: evaluat fara restul barei de creare, cu nota pe plan")
    # orfan: simbolul nu e in cache -> lumanari descarcate, fara bara in formare
    flat = [bar(k, 103, 104, 101, 103) for k in range(12)]
    orphan = plan(created=T0 + H + 9 * H, id=5, symbol="ORF/USDT", source="live")   # creat pe grila, bara 9
    forming = flat + [bar(12, 103, 125, 60, 100)]            # bara in formare: atinge intrarea si SL-ul
    st3 = {"plans": [orphan]}
    S.evaluate_live_plans(st3, FakeEx({"ORF/USDT": forming}), {}, (T0 + H) + 12 * H + 600)
    ok(orphan["state"] == P.STATE_PENDING and orphan["bars_checked"] == 3,
       "plan orfan: bara in formare eliminata (doar cele 3 bare inchise de la creare, nicio intrare)")


def audit():
    print("5. auditul")
    from core import scan as S
    created = T0 + H + 2400
    four_h = [bar(k, 103, 104, 101, 103) for k in range(0, 12)]
    four_h[2] = bar(2, 103, 104, 99.5, 101)
    four_h[3] = bar(3, 101, 102, 94, 95)                    # SL
    sub = [[int((T0 + H + m * 300) * 1000), 103, 103.5, 102.5, 103, 0.1] for m in range(48)]
    p = plan(created=created, source="live")
    st = {"plans": [p]}
    S.evaluate_live_plans(st, FakeEx({}, {"TST/USDT": sub}), {"TST/USDT": four_h}, (T0 + H) + 12 * H + 600)
    from core import evidence as EV, agent as CA
    args = (EV.FEATURE_KEYS, CA.BASE_FEATURES, CA.PENDING_DEFAULT, P.evaluate_plan, P.cost_in_r, P._r_at,
            P.TP1_FRACTION)
    rep = A.audit([p], time.time(), {"TST/USDT": four_h}, *args, eval_rules=P.EVAL_RULES)
    ok(p["state"] == P.STATE_SL and rep["reevaluated"] == 1 and not rep["flagged"],
       "planul evaluat de scanare (cu bara partiala) e confirmat de reevaluarea auditului")
    fake = copy.deepcopy(p)
    fake.update(id=9, state=P.STATE_TP2, tp1_hit_ts=fake["closed_ts"], gross_r=2.75,
                realized_r=round(2.75 - P.cost_in_r(100.0, 95.0), 3), created_ts=created + 0.5)
    rep = A.audit([fake], time.time(), {"TST/USDT": four_h}, *args, eval_rules=P.EVAL_RULES)
    ok(rep["flagged"] and "contrazis" in rep["flagged"][0]["reason"], "rezultat inventat (TP2 in loc de SL) -> marcat")
    old = copy.deepcopy(fake)
    old.pop("er")
    rep = A.audit([old], time.time(), {"TST/USDT": four_h}, *args, eval_rules=P.EVAL_RULES)
    ok(rep["reevaluated"] == 0 and not rep["flagged"],
       "plan cu reguli vechi: nereevaluat de audit (se reevalueaza in scanare, nu se sterge)")


def backtest():
    print("6. backtest-ul")
    import json
    import backtest as BT
    det = json.load(open(os.path.join("data", "latest_details.json"))).get("symbols") or {}
    sym, d = next(((s, v) for s, v in det.items() if len(v.get("candles") or []) >= 60), (None, None))
    if not sym:
        ok(False, "latest_details.json fara lumanari pentru test")
        return
    real = [list(map(float, c[:6])) for c in d["candles"]]
    rnd = random.Random(3)
    back, px = [], real[0][1]
    for k in range(400):                                            # extensie determinista inapoi
        c = px
        o = c / math.exp(rnd.gauss(0, 0.015))
        back.append([0, o, max(o, c) * 1.006, min(o, c) * 0.994, c, real[0][5]])
        px = o
    series = back[::-1] + real
    t0 = int(real[-1][0]) - (len(series) - 1) * H * 1000
    for i, c in enumerate(series):
        c[0] = t0 + i * H * 1000
    w = json.load(open(os.path.join("data", "scoring_policy.json"))).get("weights") if os.path.exists(
        os.path.join("data", "scoring_policy.json")) else {"trend": 1, "momentum": 1, "volatility": 1, "volume": 1}
    plans, _ = BT.replay_symbol(sym, series, w, 1)
    ok(plans and all(abs((p["created_ts"] * 1000 - t0) / (H * 1000) - round((p["created_ts"] * 1000 - t0) / (H * 1000))) < 1e-9
                     for p in plans),
       f"{len(plans)} planuri de backtest create pe grila barelor")
    first = plans[0] if plans else {}
    ok(plans and all(p.get("er") == P.EVAL_RULES for p in plans), "fiecare plan de backtest poarta versiunea regulilor")
    ok(plans and all(p["state"] in P.CLOSED_STATES for p in plans)
       and all(p.get("entered_ts") is None or p["entered_ts"] >= p["created_ts"] for p in plans),
       "intrarea niciodata inaintea crearii (crearea = inchiderea barei de semnal)")
    durs = [round((p["closed_ts"] - p["entered_ts"]) / H) + 1 for p in plans if p.get("entered_ts")]
    ok(not durs or max(durs) <= P.MAX_BARS, f"nicio pozitie de backtest peste {P.MAX_BARS} de bare (max {max(durs or [0])})")


def main():
    rules()
    grid()
    properties()
    scan()
    audit()
    backtest()
    print(f"\nREGULILE DE EVALUARE: {'OK' if not FAILS else str(len(FAILS)) + ' ESECURI'}")
    for f in FAILS:
        print("  -", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
