# -*- coding: utf-8 -*-
"""tests.plan_audit_check - auditul memoriei de planuri si reconstructia ei de la zero.

Uz (din radacina repo-ului): python -m tests.plan_audit_check

  1. DATELE REALE: pe data/plans.json niciun plan live nu e marcat (toate trec, inclusiv
     reevaluarea pe lumanarile reale) - auditul nu sterge date bune;
  2. PUTERE: fiecare tip de date false, injectat intr-o copie a unui plan real, e prins cu motivul lui;
  3. PLAFON: o marcare in masa nu sterge nimic (auditul se considera suspect el insusi);
  4. CAP-COADA (ai_agent.audit_memory, pe un dosar temporar): planurile live false ies din
     plans.json, motivul ajunge in plan_audit.json, agentul se reantreneaza daca le invatase;
     planurile de backtest gresite doar se raporteaza;
  5. RECONSTRUCTIA (data_reset.rebuild): backtest-ul vechi, planurile live ale versiunilor anterioare
     si arhiva ies; planurile live complete raman; ponderile se ingheata; epoca nu se repeta;
  6. GARZI: backtest pe alt timeframe refuzat; merge_only cu un backtest vechi refuzat la reconstructie;
     arhivarea nu muta planurile live cand procesul ruleaza pe alta familie; niveluri negative respinse.
Nu scrie nimic in repo.
"""

import copy
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("SCAN_TIMEFRAME", "4h")

import ai_agent as AG                   # noqa: E402
import data_reset as DR                 # noqa: E402
import plan_tracker as PT               # noqa: E402
from core import agent as CA            # noqa: E402
from core import evidence as EV         # noqa: E402
from core import plan_audit as A        # noqa: E402

FAILS = []


def ok(cond, label):
    print(f"  {'OK ' if cond else 'ERR'} {label}")
    if not cond:
        FAILS.append(label)


def _audit(plans, candles):
    return A.audit(plans, time.time(), candles, EV.FEATURE_KEYS, CA.BASE_FEATURES, CA.PENDING_DEFAULT,
                   PT.evaluate_plan, PT.cost_in_r, PT._r_at, PT.TP1_FRACTION)


def main():
    store = json.load(open(os.path.join("data", "plans.json")))
    plans = store["plans"]
    det = (json.load(open(os.path.join("data", "latest_details.json"))).get("symbols") or {})
    candles = {s: d.get("candles") for s, d in det.items()}

    print("1. datele reale")
    rep = _audit(plans, candles)
    live_f = [f for f in rep["flagged"] if f["source"] != "backtest"]
    ok(not live_f, f"niciun plan live marcat din {sum(1 for p in plans if p.get('source') != 'backtest')} "
                   f"({rep['reevaluated']} reevaluate pe lumanarile reale)")
    print(f"     backtest marcat: {[(f['id'], f['reason']) for f in rep['flagged'] if f['source'] == 'backtest'][:3]}")

    print("2. putere: date false injectate")
    live = [p for p in plans if p.get("source") != "backtest" and p.get("state") in ("SL_HIT", "TP2_HIT")]
    reev = [p for p in live if A.reevaluate(p, candles.get(p["symbol"]), PT.evaluate_plan)]
    base = reev[0] if reev else live[0]
    tp2 = next((p for p in live if p["state"] == "TP2_HIT"), base)
    now = time.time()

    def mut(p, **kw):
        q = copy.deepcopy(p)
        q.update(kw)
        q["id"] = 10 ** 9 + len(cases)
        q["created_ts"] = q["created_ts"] + 0.123 * len(cases)
        return q
    cases = []
    cases.append(("ordinea nivelurilor", mut(base, sl=base["tp2"] if base["direction"] == "LONG" else base["tp2"] * 0.5)))
    cases.append(("riscul salvat", mut(base, risk=base["risk"] * 3)))
    cases.append(("inchis fara R", mut(base, realized_r=None)))
    cases.append(("NO_ENTRY cu R", mut(base, state="NO_ENTRY", realized_r=-1.0)))
    cases.append(("R net", mut(base, realized_r=(base["realized_r"] or 0) + 0.7)))
    cases.append(("SL atins", mut(base, state="SL_HIT", gross_r=0.8, realized_r=0.8 - PT.cost_in_r(base["entry"], base["sl"]),
                                  tp1_hit_ts=None)))
    cases.append(("TP2 atins", mut(tp2, state="TP2_HIT", gross_r=9.0, realized_r=9.0 - PT.cost_in_r(tp2["entry"], tp2["sl"]))))
    cases.append(("deschis cu R", mut(base, state="OPEN", realized_r=1.2)))
    cases.append(("creat in viitor", mut(base, created_ts=now + 86400 * 3)))
    cases.append(("in afara intervalului", mut(base, components={**(base.get("components") or {}), "ev_macd": 7.0})))
    cases.append(("lipsesc caracteristici", mut(base, components={k: v for k, v in (base.get("components") or {}).items()
                                                                   if k != "ev_macd"})))
    if reev:
        flip = "TP2_HIT" if base["state"] == "SL_HIT" else "SL_HIT"
        g = 3.0 if flip == "TP2_HIT" else -1.0
        q = copy.deepcopy(base)
        q.pop("tp1_hit_ts", None)             # rezultat inventat, dar coerent intern: doar lumanarile il contrazic
        q.update(state=flip, gross_r=g, realized_r=round(g - PT.cost_in_r(base["entry"], base["sl"]), 3))
        if flip == "TP2_HIT":
            q["gross_r"] = round(PT._r_at(q["tp2"], q["entry"], q["sl"], q["direction"]), 3)
            q["realized_r"] = round(q["gross_r"] - PT.cost_in_r(q["entry"], q["sl"]), 3)
        cases.append(("contrazis de lumanarile reale", q))
    dup = copy.deepcopy(base)
    dup["id"] = 10 ** 9 + 99
    cases.append(("dublura", dup))
    for label, bad in cases:
        others = [p for p in plans if p.get("id") != bad["id"]] if label == "contrazis de lumanarile reale" else plans
        r = _audit(others + [bad], candles)
        hit = [f for f in r["flagged"] if f["id"] == bad["id"] or (label == "dublura" and f["id"] in (bad["id"], base["id"]))]
        ok(hit and label.split()[0].lower() in hit[0]["reason"].lower(),
           f"{label}: {hit[0]['reason'][:90] if hit else 'NEPRINS'}")

    print("3. plafon")
    many = [mut(base, realized_r=None) for _ in range(60)]
    r = _audit(plans + many, candles)
    allowed, why = A.removal_allowed({"flagged": [f for f in r["flagged"] if f["source"] != "backtest"],
                                      "checked": sum(1 for p in plans if p.get("source") != "backtest") + 60})
    ok(not allowed, f"60 de planuri marcate dintr-odata -> nu se sterge nimic ({why[:70]})")

    print("4. cap-coada: ai_agent.audit_memory")
    tmp = tempfile.mkdtemp(prefix="audit-")
    try:
        AG.AUDIT_FILE = os.path.join(tmp, "plan_audit.json")
        AG.DETAILS_FILE = os.path.join("data", "latest_details.json")
        st = copy.deepcopy(store)
        bad1 = mut(base, realized_r=(base["realized_r"] or 0) + 0.7, agent_trained=True)
        bad2 = mut(base, risk=base["risk"] * 3)
        st["plans"] += [bad1, bad2]
        n0 = len(st["plans"])
        retrain = AG.audit_memory(st, list(CA.PENDING_DEFAULT))
        log = json.load(open(AG.AUDIT_FILE))
        ids = {p["id"] for p in st["plans"]}
        ok(bad1["id"] not in ids and bad2["id"] not in ids and len(st["plans"]) == n0 - 2,
           f"planurile false scoase din memorie ({n0} -> {len(st['plans'])})")
        ok(retrain, "agentul invatase din unul dintre ele -> reantrenare de la zero")
        ok(len(log.get("removed") or []) == 2 and all(r.get("reason") for r in log["removed"]),
           "motivul fiecarui plan scos e in plan_audit.json")
        ok(not log.get("blocked") and st.get("summary") and st.get("calibration"),
           "rezumatul si calibrarea recalculate fara planurile scoase")
        st2 = copy.deepcopy(store)
        st2["plans"] += [mut(base, realized_r=None) for _ in range(40)]
        n0 = len(st2["plans"])
        AG.audit_memory(st2, list(CA.PENDING_DEFAULT))
        log = json.load(open(AG.AUDIT_FILE))
        ok(len(st2["plans"]) == n0 and log.get("blocked"), "peste plafon: nimic sters, audit raportat ca blocat")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("5. reconstructia memoriei")
    tmp = tempfile.mkdtemp(prefix="rebuild-")
    saved = {k: getattr(DR, k) for k in ("PLANS_FILE", "ARCHIVE_DIR", "RESEARCH_FILE", "AGENT_FILE", "POLICY_FILE",
                                          "WEIGHTS_FILE", "WEIGHTS_HISTORY_FILE", "HISTORY_FILE", "AUDIT_FILE")}
    try:
        d = os.path.join(tmp, "data")
        os.makedirs(os.path.join(d, "archive"))
        open(os.path.join(d, "archive", "v1.json"), "w").write("{}")
        for f in ("research.json", "agent_model.json", "scan_history.json"):
            if os.path.exists(os.path.join("data", f)):
                shutil.copy(os.path.join("data", f), os.path.join(d, f))
        for k, f in (("PLANS_FILE", "plans.json"), ("ARCHIVE_DIR", "archive"), ("RESEARCH_FILE", "research.json"),
                     ("AGENT_FILE", "agent_model.json"), ("POLICY_FILE", "scoring_policy.json"),
                     ("WEIGHTS_FILE", "weights.json"), ("WEIGHTS_HISTORY_FILE", "weights_history.json"),
                     ("HISTORY_FILE", "scan_history.json"), ("AUDIT_FILE", "plan_audit.json")):
            setattr(DR, k, os.path.join(d, f))
        st = copy.deepcopy(store)
        st.pop("data_epoch", None)
        ok(DR.needs_rebuild(st), "plans.json fara epoca -> reconstructie la urmatoarea integrare de backtest")
        fresh = [dict(p, id=None, fv=PT.FEATURE_VERSION, components={**(p.get("components") or {}), "ev_analyst": 0.0})
                 for p in plans if p.get("source") == "backtest"][:500]
        complete_live = [p for p in plans if p.get("source") != "backtest" and PT.same_family(p.get("geometry"))
                         and all(k in (p.get("components") or {}) for k in EV.FEATURE_KEYS if k != "ev_analyst")]
        s = DR.rebuild(st, fresh, {"trend": 1.0, "momentum": 1.0, "volatility": 1.0, "volume": 1.0},
                       PT.same_family, PT.GEOMETRY_FAMILY, PT.FEATURE_VERSION)
        left_bt = [p for p in st["plans"] if p.get("source") == "backtest"]
        left_live = [p for p in st["plans"] if p.get("source") != "backtest"]
        ok(len(left_bt) == len(fresh) and all("ev_analyst" in (p.get("components") or {}) for p in left_bt),
           f"backtest-ul vechi inlocuit integral cu cel regenerat ({len(left_bt)})")
        ok({p["id"] for p in left_live} == {p["id"] for p in complete_live},
           f"raman doar planurile live complete ({len(left_live)}), scoase cele ale versiunilor anterioare "
           f"({s['removed']})")
        ok(not os.path.exists(DR.ARCHIVE_DIR) and not os.path.exists(DR.RESEARCH_FILE)
           and not os.path.exists(DR.AGENT_FILE), "arhiva, cercetarea si modelul vechi sterse")
        pol = json.load(open(DR.POLICY_FILE))
        ok(pol.get("frozen") and pol["weights"]["trend"] == 1.0 and json.load(open(DR.WEIGHTS_FILE))["trend"] == 1.0,
           "ponderile de scor inghetate la cele ale backtest-ului")
        ok(not DR.needs_rebuild(st) and st.get("rebuilt"), "epoca marcata: reconstructia nu se repeta")
        ok(len(json.load(open(DR.HISTORY_FILE))) <= DR.SCAN_HISTORY_KEEP, "evaluarile euristice vechi taiate")
        lg = json.load(open(DR.AUDIT_FILE))
        ok((lg.get("rebuild") or {}).get("removed_total") == s["removed_total"]
           and len(lg.get("rebuild_removed_live") or []) == s["removed_total"] - s["removed"].get("backtest regenerat de la zero", 0)
           and not lg.get("removed"),
           "rezumatul reconstructiei in plan_audit.json, separat de planurile scoase pentru date false")
    finally:
        for k, v in saved.items():
            setattr(DR, k, v)
        shutil.rmtree(tmp, ignore_errors=True)

    print("6. garzi")
    fam_err = DR.check_family(store, "v6-1h")
    ok(fam_err and "4h" in fam_err, f"backtest pe alt timeframe refuzat ({(fam_err or '')[:60]})")
    ok(DR.check_family(store, PT.GEOMETRY_FAMILY) is None, "backtest pe timeframe-ul live acceptat")
    ok(not DR.backtest_is_current({"plans": []}, PT.FEATURE_VERSION)
       and DR.backtest_is_current({"meta": {"fv": PT.FEATURE_VERSION, "data_epoch": A.DATA_EPOCH}}, PT.FEATURE_VERSION),
       "la reconstructie se integreaza doar un backtest generat de codul curent")
    from core import plans as CP
    tmp = tempfile.mkdtemp(prefix="arch-")
    orig = (PT.PLANS_FILE, PT.ARCHIVE_DIR, PT.ARCHIVE_INDEX_FILE, CP.GEOMETRY_VERSION, CP.GEOMETRY_FAMILY)
    try:
        PT.PLANS_FILE = os.path.join(tmp, "plans.json")
        PT.ARCHIVE_DIR = os.path.join(tmp, "archive")
        PT.ARCHIVE_INDEX_FILE = os.path.join(PT.ARCHIVE_DIR, "_index.json")
        CP.GEOMETRY_VERSION, CP.GEOMETRY_FAMILY = "v6-1h-o", "v6-1h"
        st = {"next_id": 5, "plans": [dict(p) for p in plans if p.get("source") != "backtest"][-4:]}
        PT.save_plans(st)
        ok(len(st["plans"]) == 4 and not os.path.exists(PT.ARCHIVE_DIR),
           "un proces pe 1h nu arhiveaza planurile live de 4h")
    finally:
        PT.PLANS_FILE, PT.ARCHIVE_DIR, PT.ARCHIVE_INDEX_FILE, CP.GEOMETRY_VERSION, CP.GEOMETRY_FAMILY = orig
        shutil.rmtree(tmp, ignore_errors=True)
    from core.geometry import compute_trade_plan
    lv = compute_trade_plan("LONG", 0.0144, 0.02, {"resistance": [], "support": []},
                            {"swing_high": 0.02, "swing_low": 0.01, "extension": {"1.618": 0.05}, "retracement": {}})
    ok(lv is None, "plan cu SL sub zero (ATR extrem) respins la generare")

    print("\nAUDITUL MEMORIEI: " + ("RESPINS - " + "; ".join(FAILS) if FAILS else "TRECUT"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
