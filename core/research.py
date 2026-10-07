# -*- coding: utf-8 -*-
"""core.research - investigatia autonoma: cauta reguli care readuc edge-ul si le accepta doar pe dovezi (NUCLEU, fara I/O).

CE FACE
-------
Testeaza un spatiu FINIT si explicit de reguli de excludere pe planurile inchise de
backtest: "nu emite planul cand <conditie>". O regula e buna daca planurile pe care
le exclude pierd, iar cele ramase nu.

  - directia (toate SHORT-urile / toate LONG-urile);
  - conflictul cu o evidenta (evidenta orientata <= -0.1: contrazice planul),
    pe toate planurile sau doar pe o directie;
  - pragul de scor (sub 30 / 40 / 50), pe toate planurile sau pe o directie;
  - un token anume, pe toate planurile sau pe o directie.

Distanta stopului NU e in spatiu: schimbarea ei schimba toate planurile si cere o
re-simulare completa (backtest --sweep), nu un filtru peste planurile existente.

DE CE ATATEA CONDITII DE ACCEPTARE
----------------------------------
Cu ~200 de reguli testate pe aceleasi date, cateva par bune din intamplare. Fara
garzi, agentul si-ar "repara" singur strategia cu reguli false - exact ce s-a
intamplat cand o regula de regim, buna pe datele vazute, a dus R-ul total de la
+464 la -78 pe date nevazute. De aceea o regula trece doar daca:

  1. CAUTARE (tot istoricul fara ultimele HOLDOUT_DAYS zile): planurile excluse au
     R mediu negativ, cu prag Bonferroni (p < 0.05 / numarul de reguli testate);
  2. CONSISTENTA: negativ in cel putin 3 din 4 perioade cronologice ale cautarii;
  3. PERIOADA REZERVATA (ultimele HOLDOUT_DAYS zile, nefolosite la cautare): tot
     negativ (p < 0.10) si sub planurile ramase;
  4. MASURA: exclude intre MIN_EXCLUDED planuri si MAX_EXCLUDED_FRAC din total -
     o regula care exclude aproape tot nu repara strategia, o opreste.

O regula acceptata NU filtreaza imediat: intra in SHADOW si devine activa doar dupa
confirmarea pe planuri LIVE (vezi live_verdict). Rezultatul cel mai probabil e ca
nicio regula sa nu treaca - un raspuns valid: erodarea vine din piata.
"""

import math

HOLDOUT_DAYS = 180
FOLDS = 4
ALPHA = 0.05
HOLDOUT_P = 0.10
MIN_EXCLUDED = 200          # planuri excluse in cautare
MIN_EXCLUDED_HOLDOUT = 30   # planuri excluse in perioada rezervata
MAX_EXCLUDED_FRAC = 0.5
CONFLICT = -0.1
LIVE_MIN = 20               # planuri live inchise, potrivite regulii, pentru verdict
LIVE_P = 0.10
# caracteristici care exista doar live (0 in backtest) sau deja filtrate in productie
SKIP_FEATURES = {"ev_book_imbalance", "ev_order_flow"}
ALREADY_ACTIVE = {("conflict", "ev_elliott", None)}   # filtrul de conflict Elliott, deja activ


def _ts(p):
    v = p.get("created_ts") or 0
    return v / 1000 if v > 1e11 else v


def _score(p):
    s = p.get("score_at_entry")
    return p.get("risk_adjusted") if s is None else s


def matches(rule, p):
    """True daca regula ar exclude planul / semnalul `p` (plan salvat sau semnal live)."""
    d = rule.get("direction")
    if d and p.get("direction") != d:
        return False
    kind = rule["kind"]
    if kind == "direction":
        return True
    if kind == "conflict":
        v = (p.get("components") or {}).get(rule["feature"])
        return v is not None and v <= CONFLICT
    if kind == "score_below":
        s = _score(p)
        return s is not None and s < rule["threshold"]
    if kind == "symbol":
        return str(p.get("symbol", "")).split("/")[0] == rule["symbol"]
    return False


def describe(rule):
    d = {"LONG": " pe LONG", "SHORT": " pe SHORT", None: ""}[rule.get("direction")]
    k = rule["kind"]
    if k == "direction":
        return f"nu emite planuri {rule['direction']}"
    if k == "conflict":
        return f"nu emite cand {rule['feature'][3:]} contrazice planul{d}"
    if k == "score_below":
        return f"nu emite sub scorul {rule['threshold']}{d}"
    return f"nu emite pe {rule['symbol']}{d}"


def rule_id(rule):
    return "|".join(str(rule.get(k) or "-") for k in ("kind", "feature", "threshold", "symbol", "direction"))


def candidate_rules(plans):
    feats = sorted({k for p in plans for k in (p.get("components") or {})
                    if k.startswith("ev_") and k not in SKIP_FEATURES})
    syms = sorted({str(p.get("symbol", "")).split("/")[0] for p in plans})
    out = [{"kind": "direction", "direction": d} for d in ("LONG", "SHORT")]
    for d in (None, "LONG", "SHORT"):
        out += [{"kind": "conflict", "feature": f, "direction": d} for f in feats
                if ("conflict", f, d) not in ALREADY_ACTIVE]
        out += [{"kind": "score_below", "threshold": t, "direction": d} for t in (30, 40, 50)]
        out += [{"kind": "symbol", "symbol": s, "direction": d} for s in syms]
    for r in out:
        r["id"] = rule_id(r)
        r["text"] = describe(r)
    return out


def _mean_se(v):
    n = len(v)
    if n < 2:
        return (v[0] if v else 0.0), float("inf")
    m = sum(v) / n
    var = sum((x - m) ** 2 for x in v) / (n - 1)
    return m, math.sqrt(var / n) if var > 0 else 1e-9


def _p_below(m, se, ref=0.0):
    """p unilateral pentru media < ref (aproximare normala)."""
    if se == float("inf"):
        return 1.0
    z = (m - ref) / se
    return 0.5 * math.erfc(-z / math.sqrt(2))


def _p_diff_below(a, b):
    """p unilateral pentru media(a) < media(b) (Welch, aproximare normala)."""
    (ma, sa), (mb, sb) = _mean_se(a), _mean_se(b)
    if math.isinf(sa) or math.isinf(sb):
        return 1.0
    se = math.sqrt(sa ** 2 + sb ** 2) or 1e-9
    return 0.5 * math.erfc(-((ma - mb) / se) / math.sqrt(2))


def _r(v, n=3):
    return None if v is None or (isinstance(v, float) and (math.isinf(v) or math.isnan(v))) else round(v, n)


def investigate(plans, holdout_days=HOLDOUT_DAYS):
    """Investigatia completa pe planurile inchise de backtest. Intoarce raportul:
    regulile acceptate (toate conditiile), cele mai promitatoare respinse cu motivul
    exact, si ferestrele folosite."""
    rows = sorted((p for p in plans if p.get("realized_r") is not None and p.get("state") != "NO_ENTRY"
                   and p.get("source") == "backtest"), key=_ts)
    if len(rows) < 2000:
        return {"status": "date_insuficiente", "n_plans": len(rows), "accepted": [], "rejected_top": []}
    cut = _ts(rows[-1]) - holdout_days * 86400
    search = [p for p in rows if _ts(p) < cut]
    hold = [p for p in rows if _ts(p) >= cut]
    rules = candidate_rules(rows)
    n_tests = len(rules)
    alpha = ALPHA / n_tests
    fold_len = max(1, len(search) // FOLDS)
    folds = [search[i * fold_len:(i + 1) * fold_len if i < FOLDS - 1 else len(search)] for i in range(FOLDS)]
    base_s = _mean_se([p["realized_r"] for p in search])[0]
    base_h = _mean_se([p["realized_r"] for p in hold])[0]
    accepted, scored = [], []
    for rule in rules:
        ex = [p["realized_r"] for p in search if matches(rule, p)]
        kept = [p["realized_r"] for p in search if not matches(rule, p)]
        res = {"id": rule["id"], "text": rule["text"], "rule": {k: v for k, v in rule.items() if k not in ("id", "text")},
               "n_ex": len(ex), "frac_ex": round(len(ex) / len(search), 3)}
        if len(ex) < MIN_EXCLUDED or len(ex) > MAX_EXCLUDED_FRAC * len(search):
            continue                                       # in afara masurii: nu conteaza ca test
        m, se = _mean_se(ex)
        p = _p_below(m, se)
        neg_folds = 0
        for f in folds:
            fv = [q["realized_r"] for q in f if matches(rule, q)]
            if len(fv) >= 20 and sum(fv) / len(fv) < 0:
                neg_folds += 1
        hx = [q["realized_r"] for q in hold if matches(rule, q)]
        hk = [q["realized_r"] for q in hold if not matches(rule, q)]
        hm, hse = _mean_se(hx) if hx else (None, float("inf"))
        hp = _p_below(hm, hse) if hx else 1.0
        res.update({"r_ex": _r(m), "r_kept": _r(_mean_se(kept)[0]), "p": float(f"{p:.2e}"),
                    "folds_negative": neg_folds, "n_ex_holdout": len(hx), "r_ex_holdout": _r(hm),
                    "r_kept_holdout": _r(_mean_se(hk)[0]) if hk else None, "p_holdout": round(hp, 4),
                    "gain_r_per_plan_holdout": _r((sum(hk) / len(hk) - base_h) if hk else None)})
        why = None
        if not (m < 0 and p < alpha):
            why = f"in cautare: R exclus {m:+.3f}, p={p:.1e} (prag Bonferroni {alpha:.1e})"
        elif neg_folds < FOLDS - 1:
            why = f"inconsistent: negativ in doar {neg_folds} din {FOLDS} perioade"
        elif len(hx) < MIN_EXCLUDED_HOLDOUT:
            why = f"prea putine planuri in perioada rezervata ({len(hx)})"
        elif not (hm < 0 and hp < HOLDOUT_P and hk and hm < sum(hk) / len(hk)):
            why = f"nu se confirma pe perioada rezervata: R exclus {hm:+.3f}, p={hp:.3f}"
        res["reason"] = why or "acceptata: toate conditiile indeplinite"
        (accepted if why is None else scored).append(res)
    scored.sort(key=lambda r: r["p"])
    # efectul combinat al regulilor acceptate, pe perioada rezervata
    combo = None
    if accepted:
        ar = [{**a["rule"]} for a in accepted]
        kept_h = [q["realized_r"] for q in hold if not any(matches(r, q) for r in ar)]
        combo = {"r_before": _r(base_h), "r_after": _r(_mean_se(kept_h)[0]) if kept_h else None,
                 "n_before": len(hold), "n_after": len(kept_h)}
    return {"status": "ok", "n_plans": len(rows), "n_rules": n_tests, "alpha": float(f"{alpha:.2e}"),
            "search": {"n": len(search), "r": _r(base_s)}, "holdout": {"n": len(hold), "r": _r(base_h),
                                                                     "days": holdout_days},
            "accepted": accepted, "rejected_top": scored[:8], "combined_holdout": combo}


def live_verdict(rule, live_closed, since_ts):
    """Confirmarea pe planuri LIVE create dupa acceptare: planurile pe care regula le-ar
    fi exclus pierd mai mult decat celelalte? 'confirma' / 'infirma' / 'asteapta'."""
    after = [p for p in live_closed if _ts(p) >= since_ts]
    m = [p["realized_r"] for p in after if matches(rule, p)]
    k = [p["realized_r"] for p in after if not matches(rule, p)]
    out = {"n_match": len(m), "n_rest": len(k), "r_match": _r(_mean_se(m)[0]) if m else None,
           "r_rest": _r(_mean_se(k)[0]) if k else None}
    if len(m) < LIVE_MIN or len(k) < LIVE_MIN:
        return "asteapta", out
    p = _p_diff_below(m, k)
    out["p"] = round(p, 4)
    if p < LIVE_P and out["r_match"] < 0:
        return "confirma", out
    if _p_diff_below(k, m) < LIVE_P:
        return "infirma", out
    return "asteapta", out
