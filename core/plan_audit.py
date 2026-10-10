# -*- coding: utf-8 -*-
"""core.plan_audit - auditul memoriei de planuri a agentului (NUCLEU, fara I/O).

DE CE
-----
Tot ce stie agentul vine din planurile inchise: invata din R-ul lor, calibrarea masoara pe
ele probabilitatea, cercetarea cauta reguli in ele, iar statusul (ACTIVE / SHADOW, AUC,
confirmarea live, divergenta live-backtest) e calculat din ele. Un plan cu un rezultat fals -
niveluri imposibile, un R care nu decurge din stare, un rezultat pe care lumanarile reale il
contrazic, un dublu - da agentului un status fals, iar eroarea se propaga in toate cifrele.

Auditul ruleaza la fiecare antrenare (ai_agent.main), INAINTE de invatare. Planurile care nu
trec se scot din memorie, cu motivul inregistrat (data/plan_audit.json), iar agentul se
reantreneaza de la zero daca invatase din vreunul. Nu se corecteaza nimic: un plan dovedit
gresit nu devine "probabil corect" prin reparatie, iar unul bun nu se atinge.

PLASA DE SIGURANTA: un audit care ar sterge dintr-odata mai mult de MAX_REMOVE_FRAC din planuri
(sau peste MAX_REMOVE_N) e tratat ca o eroare a auditului, nu a datelor - nu sterge nimic si
raporteaza (auto-diagnosticul il arata). Stergerile mari raman o decizie a omului.

REGULI (fiecare verificabila, nicio euristica de "pare ciudat"):
  niveluri    - entry/SL/TP lipsa, ordine imposibila pentru directie, risc != |entry - SL|;
  stare       - inchis fara R sau fara moment de inchidere, inchis inainte de creare, deschis
                cu R, NO_ENTRY cu R nenul, pozitie inchisa fara moment de intrare (live);
  R           - R net != R brut - cost; SL fara -1R (sau fara TP1 atins inainte); TP2 cu alt R
                decat cel dat de niveluri;
  rezultat    - reevaluat pe lumanarile reale (aceeasi functie, core/plans.evaluate_plan),
                planul inchis ajunge la alta stare sau alt R;
  timp        - creat sau inchis in viitor;
  caracteristici - valori ne-numerice sau in afara intervalului; un plan live caruia ii lipseste
                o caracteristica pe care planurile live o aveau deja la crearea lui (calcul rupt);
  dublu       - acelasi simbol, directie, moment si intrare (se pastreaza primul).

EPOCA DE DATE (DATA_EPOCH): o reconstructie completa - backtest regenerat de la zero cu codul
curent, planurile live din versiuni anterioare ale agentului scoase, statisticile vechi sterse,
agentul reantrenat - se face o singura data pe epoca, la integrarea backtest-ului
(backtest.py --merge). `rebuild_selection` decide ce ramane.
"""

import math

DATA_EPOCH = 1                # 1 = 2026-10: prima memorie curata (backtest regenerat cu ev_analyst)
MAX_REMOVE_FRAC = 0.02        # peste 2% din planuri intr-o rulare: auditul se opreste si raporteaza
MAX_REMOVE_N = 300
TOL_R = 0.002                 # rotunjirea la 3 zecimale din core/plans
CLOSED = ("TP2_HIT", "SL_HIT", "EXPIRED", "NO_ENTRY")
POSITION_CLOSED = ("TP2_HIT", "SL_HIT", "EXPIRED")
REEVAL_FIELDS = ("state", "state_detail", "realized_r", "gross_r", "closed_ts", "entered_ts",
                 "tp1_hit_ts", "bars_checked")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def check_levels(p):
    e, sl, t1, t2, d = p.get("entry"), p.get("sl"), p.get("tp1"), p.get("tp2"), p.get("direction")
    if d not in ("LONG", "SHORT"):
        return "directie invalida"
    if not all(_num(x) and x > 0 for x in (e, sl, t1, t2)):
        return "niveluri lipsa sau ne-numerice"
    if not ((sl < e < t1 < t2) if d == "LONG" else (sl > e > t1 > t2)):
        return "ordinea nivelurilor e imposibila pentru directie"
    risk = p.get("risk")
    if _num(risk) and abs(risk - abs(e - sl)) > max(1e-6 * e, 1e-9) * 10:
        return "riscul salvat nu e |entry - SL|"
    return None


def check_state(p, now, cost_fn, r_at, tp1_fraction):
    s, r, g = p.get("state"), p.get("realized_r"), p.get("gross_r")
    c_ts, cr_ts = p.get("closed_ts"), p.get("created_ts")
    if not _num(cr_ts):
        return "fara moment de creare"
    if cr_ts > now + 3600:
        return "creat in viitor"
    if s in CLOSED:
        if r is None or not _num(c_ts):
            return "inchis fara R sau fara moment de inchidere"
        if not _num(r):
            return "R ne-numeric"
        if c_ts < cr_ts - 1:
            return "inchis inainte de creare"
        if c_ts > now + 3600:
            return "inchis in viitor"
        if s == "NO_ENTRY":
            return "NO_ENTRY cu R nenul" if abs(r) > 1e-9 else None
        if p.get("source") != "backtest" and not p.get("entered_ts"):
            return "pozitie inchisa fara moment de intrare"
        if not _num(g):
            return "fara R brut"
        if abs(r - (g - cost_fn(p["entry"], p["sl"]))) > TOL_R:
            return "R net nu e R brut minus cost"
        if s == "SL_HIT" and not (abs(g + 1.0) < 1e-6 or (p.get("tp1_hit_ts") and g >= 0)):
            return "SL atins cu R brut diferit de -1R"
        if s == "TP2_HIT":
            r2 = r_at(p["tp2"], p["entry"], p["sl"], p["direction"])
            r1 = r_at(p["tp1"], p["entry"], p["sl"], p["direction"])
            exp = (tp1_fraction * r1 + (1 - tp1_fraction) * r2) if p.get("tp1_hit_ts") else r2
            if abs(g - exp) > TOL_R:
                return "TP2 atins cu alt R decat cel dat de niveluri"
        return None
    if s in ("PENDING", "OPEN", "TP1_HIT"):
        return "plan deschis cu R" if r is not None else None
    return f"stare necunoscuta ({s})"


def check_features(p, feature_keys, base_features):
    comp = p.get("components")
    if comp is None:
        return None
    if not isinstance(comp, dict):
        return "vector de caracteristici corupt"
    for k in feature_keys:
        v = comp.get(k)
        if v is None:
            continue
        if not _num(v) or v < -1.0001 or v > 1.0001:
            return f"caracteristica {k} in afara intervalului [-1, 1]"
    for k in base_features:
        v = comp.get(k)
        if v is not None and (not _num(v) or v < -1e-6 or v > 1.0001):
            return f"componenta {k} in afara intervalului [0, 1]"
    return None


def missing_at_creation(plans, feature_keys, pending=()):
    """{id: [caracteristici]} pentru planurile LIVE carora le lipseste o caracteristica pe care
    planurile live o aveau deja inainte de crearea lor (calculul s-a rupt). Planurile mai vechi
    decat prima aparitie a unei caracteristici nu sunt afectate: lipsa ei acolo e istorie, nu
    eroare (ele se scot doar la reconstructia unei epoci noi)."""
    live = sorted((p for p in plans if p.get("source") != "backtest" and isinstance(p.get("components"), dict)
                   and _num(p.get("created_ts"))), key=lambda p: p["created_ts"])
    first = {}
    for p in live:
        for k in feature_keys:
            if k in p["components"] and k not in first:
                first[k] = p["created_ts"]
    out = {}
    for p in live:
        miss = [k for k in feature_keys if k not in pending and k in first and first[k] < p["created_ts"]
                and k not in p["components"]]
        if miss:
            out[p.get("id")] = miss
    return out


def reevaluate(p, candles, evaluate_fn):
    """Starea si R-ul pe care le-ar avea planul reevaluat de la zero pe `candles`, sau None daca
    lumanarile nu acopera planul de la creare pana la inchidere."""
    if not candles or p.get("state") not in POSITION_CLOSED + ("NO_ENTRY",):
        return None
    if candles[0][0] / 1000.0 > p["created_ts"] or candles[-1][0] / 1000.0 < (p.get("closed_ts") or 0):
        return None
    q = {k: v for k, v in p.items() if k not in REEVAL_FIELDS}
    q["state"] = "PENDING"
    evaluate_fn(q, candles)
    return q


def audit(plans, now, candles_by_symbol, feature_keys, base_features, pending, evaluate_fn, cost_fn, r_at,
          tp1_fraction):
    """{"checked", "flagged": [{"id", "symbol", "direction", "source", "state", "realized_r",
    "created_ts", "reason"}], "by_reason": {motiv: n}, "reevaluated": n}."""
    flagged, seen, reev = [], {}, 0
    miss = missing_at_creation(plans, feature_keys, pending)
    for p in sorted(plans, key=lambda x: x.get("id") or 0):
        reason = check_levels(p) or check_state(p, now, cost_fn, r_at, tp1_fraction) \
            or check_features(p, feature_keys, base_features)
        if not reason and p.get("id") in miss:
            reason = "lipsesc caracteristici calculate la crearea lui: " + ", ".join(miss[p["id"]][:3])
        if not reason:
            key = (p.get("symbol"), p.get("direction"), round(p.get("created_ts") or 0, 3),
                   round(p.get("entry") or 0, 10), p.get("source") == "backtest")
            if key in seen:
                reason = f"dublura a planului #{seen[key]}"
            else:
                seen[key] = p.get("id")
        if not reason and p.get("source") != "backtest":
            q = reevaluate(p, candles_by_symbol.get(p.get("symbol")), evaluate_fn)
            if q is not None:
                reev += 1
                if q.get("state") != p.get("state") or (
                        p.get("realized_r") is not None and q.get("realized_r") is not None
                        and abs(q["realized_r"] - p["realized_r"]) > TOL_R):
                    reason = (f"rezultat contrazis de lumanarile reale: {p.get('state')} {p.get('realized_r')}R "
                              f"in memorie, {q.get('state')} {q.get('realized_r')}R la reevaluare")
        if reason:
            flagged.append({"id": p.get("id"), "symbol": p.get("symbol"), "direction": p.get("direction"),
                            "source": p.get("source") or "live", "state": p.get("state"),
                            "realized_r": p.get("realized_r"), "created_ts": p.get("created_ts"),
                            "reason": reason})
    by = {}
    for f in flagged:
        k = f["reason"].split(":")[0]
        by[k] = by.get(k, 0) + 1
    return {"checked": len(plans), "flagged": flagged, "by_reason": by, "reevaluated": reev}


def removal_allowed(report):
    """(permis, motiv). Peste prag, auditul nu sterge: o eroare in masa e mai probabil un defect
    al auditului (sau o schimbare de format) decat date corupte - decide omul."""
    n, tot = len(report.get("flagged") or []), report.get("checked") or 0
    if not n:
        return True, "nimic de sters"
    cap = min(MAX_REMOVE_N, max(10, int(MAX_REMOVE_FRAC * tot)))
    if n > cap:
        return False, (f"{n} planuri marcate din {tot} - peste plafonul de {cap} pe rulare; nu sterg nimic, "
                       "un audit care ar sterge atat e suspect el insusi")
    return True, f"{n} planuri cu date false scoase din memorie"


def rebuild_selection(plans, feature_keys, pending, same_family):
    """Reconstructia unei epoci noi: ce ramane si ce se scoate din memorie (lista de motive).

    Se scot: TOATE planurile de backtest (se inlocuiesc cu cele regenerate acum de codul curent),
    planurile din alta familie de geometrie, planurile live create de versiuni anterioare ale
    agentului (le lipsesc caracteristici pe care agentul le foloseste acum - pentru model, o
    evidenta absenta inseamna 0, adica o informatie falsa). Raman planurile live complete:
    sunt singura masuratoare reala, iar stergerea lor ar ascunde exact rezultatul live
    (ar face statusul agentului sa para mai bun decat e)."""
    keep, removed = [], []
    need = [k for k in feature_keys if k not in pending]
    for p in plans:
        if p.get("source") == "backtest":
            removed.append((p, "backtest regenerat de la zero"))
        elif not same_family(p.get("geometry")):
            removed.append((p, f"alta geometrie ({p.get('geometry')})"))
        else:
            comp = p.get("components") if isinstance(p.get("components"), dict) else {}
            miss = [k for k in need if k not in comp]
            if miss:
                removed.append((p, "versiune anterioara a agentului (lipsesc " + ", ".join(miss[:3])
                                + ("..." if len(miss) > 3 else "") + ")"))
            else:
                keep.append(p)
    return keep, removed


def compact_record(p, reason, when):
    return {"id": p.get("id"), "symbol": p.get("symbol"), "direction": p.get("direction"),
            "source": p.get("source") or "live", "geometry": p.get("geometry"), "state": p.get("state"),
            "realized_r": p.get("realized_r"), "created_ts": p.get("created_ts"), "reason": reason,
            "removed": when}
