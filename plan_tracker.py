#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
plan_tracker.py
================
Fundatia agentului: planuri numerotate, cu ciclu de viata urmarit in timp si
R REALIZAT masurat bara cu bara.

DE CE E ASTA FUNDATIA
---------------------
Pana acum proiectul genera un plan (entry/SL/TP1/TP2) la fiecare scanare si
il uita imediat. Nimic nu verifica vreodata daca TP1 a fost atins sau daca
SL a fost lovit. Consecinta: `expected_r` era o cifra decorativa, iar
ponderile adaptive si agentul invatau din "unde era pretul la ora 24", nu
din "a functionat tranzactia".

Aici masor corect: descarc lumanarile de la crearea planului incoace si
parcurg bara cu bara ca sa vad ce s-a atins PRIMUL.

REGULI EXPLICITE (ca sa nu-mi umflu rezultatele)
------------------------------------------------
1. AMBIGUITATE IN ACEEASI BARA: daca o bara atinge si SL si TP, presupun ca
   SL a venit primul. Fara date de tick nu pot sti ordinea, deci aleg mereu
   varianta defavorabila mie. Altfel as raporta rezultate mai bune decat
   realitatea.
2. MODEL DE POZITIE: 50% din pozitie se inchide la TP1, restul merge la TP2,
   iar SL-ul se muta la breakeven dupa TP1. E o regula standard, si e
   declarata explicit ca sa fie reproductibila.
3. EXPIRARE: dupa MAX_BARS fara sa atinga nimic, planul se inchide la pretul
   curent (mark-to-market), nu se sterge. Un plan care nu a mers nicaieri e
   tot un rezultat din care se invata.

CE FACE IN PLUS FATA DE POZE
----------------------------
Pozele arata stari de plan ("TP1 HIT · CLOSED", "SL / INVALIDATED"), dar
afiseaza si procente de tip "86% confirmed" care nu par sa fie masurate din
rezultate. Aici probabilitatea afisata e CALIBRATA din planurile inchise
efectiv, cu interval de incredere Wilson, si spun explicit cand nu am destule
date ca sa pronunt un numar.
"""

import json
import math
import os
import time
from datetime import datetime, timezone

DATA_DIR = "data"
PLANS_FILE = os.path.join(DATA_DIR, "plans.json")

MAX_BARS = 48          # cate lumanari las un plan deschis (48 = 2 zile pe 1h)
MIN_BUCKET_SAMPLES = 20  # sub atat, nu pronunt o probabilitate calibrata

# Versiunea geometriei planului. Cand regulile de plasare a TP1/TP2 se schimba,
# rezultatele vechi devin necomparabile: descriu o structura care nu mai exista.
# Calibrarea foloseste doar planuri din versiunea curenta.
# v1 -> v2: TP1 nu mai poate fi sub 1R (v1 producea planuri cu asteptare
# negativa prin constructie: 21 din 47 aveau TP1 sub 1R, unul la 0.00R).
GEOMETRY_VERSION = "v2"
TP1_FRACTION = 0.5     # cat din pozitie se inchide la TP1

STATE_OPEN = "OPEN"
STATE_TP1 = "TP1_HIT"
STATE_TP2 = "TP2_HIT"
STATE_SL = "SL_HIT"
STATE_EXPIRED = "EXPIRED"
CLOSED_STATES = (STATE_TP2, STATE_SL, STATE_EXPIRED)


# ============================== PERSISTENTA ================================

def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def auto_migrate_geometry(store):
    """Inchide automat planurile deschise generate cu o geometrie veche.

    DE CE AUTOMAT SI NU CA PAS MANUAL:
    `has_open_plan` refuza sa deschida un plan nou pentru o combinatie
    simbol+directie care are deja unul activ. Planurile ramase din geometria
    veche ar bloca astfel simbolurile respective pana s-ar inchide singure -
    intarziind degeaba colectarea de date bune. Facand-o automat, nu depinde
    de un pas manual pe care e usor sa-l uiti sau sa-l rulezi gresit.

    Nu le atribui un R: geometria lor nu mai e comparabila, deci un rezultat
    ar fi contorizat undeva unde nu-si are locul. Raman vizibile in istoric,
    cu numerotarea intacta.

    Idempotent: dupa o rulare nu mai gaseste nimic de migrat.
    """
    migrated = []
    for p in store.get("plans", []):
        if p.get("state") in CLOSED_STATES:
            continue
        if p.get("geometry", "v1") == GEOMETRY_VERSION:
            continue
        prev = p.get("state")
        p["state"] = STATE_EXPIRED
        p["state_detail"] = f"INCHIS AUTOMAT LA MIGRARE (geometrie {p.get('geometry', 'v1')})"
        p["migrated"] = True
        p["migrated_ts"] = time.time()
        migrated.append((p["id"], p["symbol"], p["direction"], prev))
    return migrated


def load_plans():
    return load_json(PLANS_FILE, {"next_id": 1, "plans": []})


def save_plans(store):
    save_json(PLANS_FILE, store)


# ============================ CREARE DE PLANURI =============================

def has_open_plan(store, symbol, direction):
    """Nu deschid un plan nou pentru acelasi simbol+directie daca deja am unul
    activ. Fara asta, un semnal persistent ar genera zeci de planuri identice
    si ar umple istoricul cu duplicate corelate."""
    return any(
        p["symbol"] == symbol and p["direction"] == direction
        and p["state"] not in CLOSED_STATES
        for p in store["plans"]
    )


def create_plan(store, signal, plan_levels, decision):
    """Inregistreaza un plan nou, numerotat (PLAN #N, ca in poze)."""
    entry, sl = plan_levels["entry"], plan_levels["sl"]
    risk = abs(entry - sl)
    if risk <= 0:
        return None

    plan = {
        "id": store["next_id"],
        "symbol": signal["symbol"],
        "direction": signal["direction"],
        "created_ts": time.time(),
        "created_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "entry": entry,
        "sl": sl,
        "tp1": plan_levels["tp1"],
        "tp2": plan_levels["tp2"],
        "risk": round(risk, 8),
        "planned_r_tp2": round(abs(plan_levels["tp2"] - entry) / risk, 2),
        "state": STATE_OPEN,
        "state_detail": "OPEN · WAITING",
        "realized_r": None,
        "closed_ts": None,
        "bars_checked": 0,
        # context la momentul deciziei - ca sa pot invata ce fel de setup merge
        "score_at_entry": signal.get("risk_adjusted"),
        "components": signal.get("components"),
        "persistence_at_entry": signal.get("persistence"),
        "decision": decision,
        "geometry": GEOMETRY_VERSION,
    }
    store["plans"].append(plan)
    store["next_id"] += 1
    return plan


# ======================= EVALUARE BARA CU BARA ==============================

def _r_at(price, entry, sl, direction):
    """Cati R fata de intrare, cu semn (pozitiv = in favoare)."""
    risk = abs(entry - sl)
    if risk <= 0:
        return 0.0
    move = (price - entry) if direction == "LONG" else (entry - price)
    return move / risk


def evaluate_plan(plan, candles):
    """Parcurge lumanarile de dupa crearea planului si determina ce s-a atins
    PRIMUL. `candles` = [[ts, open, high, low, close, volume], ...].

    Returneaza True daca starea planului s-a schimbat."""
    if plan["state"] in CLOSED_STATES:
        return False

    entry, sl, tp1, tp2 = plan["entry"], plan["sl"], plan["tp1"], plan["tp2"]
    direction = plan["direction"]
    is_long = direction == "LONG"
    risk = abs(entry - sl)
    if risk <= 0:
        return False

    relevant = [c for c in candles if c[0] / 1000.0 >= plan["created_ts"]]
    if not relevant:
        return False

    # BUG FIX (idempotenta): retin MOMENTUL cand s-a atins TP1, nu doar un
    # boolean. Fara asta, la reevaluarea planului - se intampla la FIECARE
    # scanare, pe aceleasi lumanari - SL-ul mutat la breakeven se aplica
    # RETROACTIV si barelor de dinainte de TP1. Cum entry-ul e langa pretul
    # curent, aproape orice plan coboara sub entry la un moment dat inainte de
    # TP1, deci toti castigatorii s-ar fi inchis fals la +0.5R in loc sa ruleze
    # spre TP2 - si toate datele de invatare ar fi fost falsificate in jos.
    tp1_ts = plan.get("tp1_hit_ts")
    changed = False
    bars = 0

    for c in relevant:
        bars += 1
        bar_ts, high, low, close = c[0] / 1000.0, c[2], c[3], c[4]

        # breakeven-ul se aplica DOAR barelor de dupa cea in care s-a atins TP1
        after_tp1 = tp1_ts is not None and bar_ts > tp1_ts
        active_sl = entry if after_tp1 else sl

        if is_long:
            sl_touched = low <= active_sl
            tp1_touched = high >= tp1
            tp2_touched = high >= tp2
        else:
            sl_touched = high >= active_sl
            tp1_touched = low <= tp1
            tp2_touched = low <= tp2

        # REGULA 1: ambiguitate in aceeasi bara -> presupun SL primul
        if sl_touched:
            if after_tp1:
                # jumatate luata la TP1, restul iesit la breakeven
                r = TP1_FRACTION * _r_at(tp1, entry, sl, direction)
                plan["state_detail"] = "TP1 HIT · SL LA BREAKEVEN"
            else:
                r = -1.0
                plan["state_detail"] = "SL HIT · INVALIDATED"
            plan["state"] = STATE_SL
            plan["realized_r"] = round(r, 3)
            plan["closed_ts"] = bar_ts
            changed = True
            break

        if tp2_touched:
            r_tp1 = _r_at(tp1, entry, sl, direction)
            r_tp2 = _r_at(tp2, entry, sl, direction)
            r = (TP1_FRACTION * r_tp1 + (1 - TP1_FRACTION) * r_tp2
                 if tp1_ts is not None else r_tp2)
            plan["state"] = STATE_TP2
            plan["state_detail"] = "TP2 HIT · CLOSED"
            plan["realized_r"] = round(r, 3)
            plan["closed_ts"] = bar_ts
            changed = True
            break

        if tp1_touched and tp1_ts is None:
            tp1_ts = bar_ts
            plan["tp1_hit_ts"] = bar_ts
            plan["state"] = STATE_TP1
            plan["state_detail"] = "TP1 HIT · RULEAZA SPRE TP2"
            changed = True

        if bars >= MAX_BARS:
            r = _r_at(close, entry, sl, direction)
            if tp1_ts is not None:
                r = TP1_FRACTION * _r_at(tp1, entry, sl, direction) + (1 - TP1_FRACTION) * r
            plan["state"] = STATE_EXPIRED
            plan["state_detail"] = f"EXPIRAT dupa {bars} bare"
            plan["realized_r"] = round(r, 3)
            plan["closed_ts"] = bar_ts
            changed = True
            break

    plan["bars_checked"] = bars
    return changed


# ===================== CALIBRARE DIN REZULTATE REALE ========================

def wilson_interval(successes, total, z=1.96):
    """Interval de incredere Wilson - onest si la esantioane mici, spre
    deosebire de intervalul normal care da rezultate absurde acolo."""
    if total == 0:
        return (0.0, 1.0)
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def build_calibration(store, bucket_size=20):
    """Rata REALA de succes pe intervale de scor, calculata din planurile
    inchise. Asta inlocuieste formula `50 + scor * 0.35`, care nu era o
    probabilitate ci scorul rescalat."""
    buckets = {}
    for p in store["plans"]:
        if p["state"] not in CLOSED_STATES or p.get("realized_r") is None:
            continue
        if p.get("geometry", "v1") != GEOMETRY_VERSION:
            continue  # geometrie veche: rezultatele nu sunt comparabile
        score = p.get("score_at_entry")
        if score is None:
            continue
        b = int(score // bucket_size) * bucket_size
        entry = buckets.setdefault(b, {"wins": 0, "total": 0, "sum_r": 0.0,
                                       "sum_win_r": 0.0, "sum_loss_r": 0.0,
                                       "wins_n": 0, "losses_n": 0})
        entry["total"] += 1
        entry["sum_r"] += p["realized_r"]
        if p["realized_r"] > 0:
            entry["wins"] += 1
            entry["wins_n"] += 1
            entry["sum_win_r"] += p["realized_r"]
        else:
            entry["losses_n"] += 1
            entry["sum_loss_r"] += abs(p["realized_r"])

    calibration = {}
    for b, e in buckets.items():
        lo, hi = wilson_interval(e["wins"], e["total"])
        calibration[str(b)] = {
            "total": e["total"],
            "win_rate": round(100 * e["wins"] / e["total"], 1),
            "ci_low": round(100 * lo, 1),
            "ci_high": round(100 * hi, 1),
            "avg_r": round(e["sum_r"] / e["total"], 3),
            "avg_win_r": round(e["sum_win_r"] / e["wins_n"], 3) if e["wins_n"] else None,
            "avg_loss_r": round(e["sum_loss_r"] / e["losses_n"], 3) if e["losses_n"] else None,
            "reliable": e["total"] >= MIN_BUCKET_SAMPLES,
        }
    return calibration


def calibrated_probability(calibration, score, bucket_size=20):
    """Probabilitatea masurata pentru scorul asta. Returneaza None daca nu am
    destule date - prefer sa spun "nu stiu" decat sa inventez un numar."""
    b = str(int(score // bucket_size) * bucket_size)
    entry = calibration.get(b)
    if not entry or not entry["reliable"]:
        return None
    return entry


# ========================= POARTA DE DECIZIE ================================

def decide(calibration, signal, agent_pred=None, bucket_size=20):
    """Agentul decide singur daca merita deschis un plan, pe baza istoricului
    lui de rezultate - nu pe baza unei formule fixe.

    Politica: EXPLOREAZA cand nu stie, EXPLOATEAZA cand stie.
      - fara date suficiente -> deschide planul, marcat ca EXPLORARE
        (are nevoie de date ca sa invete; a refuza tot ar insemna sa nu
        invete niciodata nimic)
      - cu date -> calculeaza valoarea asteptata in R si deschide doar daca
        e pozitiva

    ROLUL AGENTULUI (`agent_pred` din ai_agent.predict_for_signal):
    Calibrarea stie doar in ce interval de scor cade semnalul. Agentul vede
    toate caracteristicile (trend, momentum, volatilitate, volum, directie,
    persistenta), deci e mai fin. Cand agentul e ACTIVE - adica a demonstrat
    pe date ca bate baseline-ul - probabilitatea lui inlocuieste rata bruta
    de bucket in calculul valorii asteptate. Marimile de castig/pierdere in R
    raman cele MASURATE din planuri inchise; agentul estimeaza doar sansa,
    nu inventeaza si magnitudini.
    Cand agentul e in SHADOW, predictia lui e doar inregistrata pe plan, ca
    sa se poata verifica ulterior daca ar fi ajutat.
    """
    score = signal.get("risk_adjusted", 0)
    cal = calibrated_probability(calibration, score, bucket_size)
    agent_p = (agent_pred or {}).get("probability")
    agent_active = bool((agent_pred or {}).get("active"))

    if cal is None:
        return {"action": "ISSUE", "mode": "EXPLORARE",
                "reason": f"inca nu am destule planuri inchise la scor ~{score} "
                          f"(prag {MIN_BUCKET_SAMPLES}) - deschid ca sa invat",
                "expected_value_r": None, "calibrated_prob": None,
                "agent_prob": agent_p, "agent_used": False}

    avg_win = cal["avg_win_r"] if cal["avg_win_r"] is not None else 1.0
    avg_loss = cal["avg_loss_r"] if cal["avg_loss_r"] is not None else 1.0

    if agent_active and agent_p is not None:
        p, source = agent_p, f"agent AI ({100*agent_p:.0f}%)"
        agent_used = True
    else:
        p, source = cal["win_rate"] / 100.0, f"calibrare ({cal['win_rate']}%, n={cal['total']})"
        agent_used = False

    ev = p * avg_win - (1 - p) * avg_loss
    base = {"expected_value_r": round(ev, 3), "calibrated_prob": cal["win_rate"],
            "agent_prob": agent_p, "agent_used": agent_used}

    if ev <= 0:
        return {"action": "SKIP", "mode": "EXPLOATARE",
                "reason": f"valoare asteptata negativa ({ev:+.2f}R) la scor ~{score}, "
                          f"sansa din {source}", **base}

    return {"action": "ISSUE", "mode": "EXPLOATARE",
            "reason": f"valoare asteptata {ev:+.2f}R la scor ~{score}, sansa din {source} "
                      f"(IC calibrare {cal['ci_low']}-{cal['ci_high']}%)", **base}


# ============================== RAPORTARE ===================================

def summarize(store):
    plans = store["plans"]
    all_closed = [p for p in plans if p["state"] in CLOSED_STATES and p.get("realized_r") is not None]
    closed = [p for p in all_closed if p.get("geometry", "v1") == GEOMETRY_VERSION]
    legacy = [p for p in all_closed if p.get("geometry", "v1") != GEOMETRY_VERSION]
    open_plans = [p for p in plans if p["state"] not in CLOSED_STATES]

    total_r = sum(p["realized_r"] for p in closed)
    wins = [p for p in closed if p["realized_r"] > 0]
    losses = [p for p in closed if p["realized_r"] <= 0]

    by_state = {}
    for p in plans:
        by_state[p["state"]] = by_state.get(p["state"], 0) + 1

    profit_factor = None
    if losses:
        gross_win = sum(p["realized_r"] for p in wins)
        gross_loss = abs(sum(p["realized_r"] for p in losses))
        profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else None

    return {
        "legacy_closed": len(legacy),
        "legacy_total_r": round(sum(p["realized_r"] for p in legacy), 2) if legacy else None,
        "geometry": GEOMETRY_VERSION,
        "total_plans": len(plans),
        "open": len(open_plans),
        "closed": len(closed),
        "by_state": by_state,
        "win_rate": round(100 * len(wins) / len(closed), 1) if closed else None,
        "total_r": round(total_r, 2),
        "avg_r": round(total_r / len(closed), 3) if closed else None,
        "profit_factor": profit_factor,
        "best": max((p["realized_r"] for p in closed), default=None),
        "worst": min((p["realized_r"] for p in closed), default=None),
    }


def print_summary(store):
    s = summarize(store)
    print(f"Planuri: {s['total_plans']} total, {s['open']} deschise, {s['closed']} inchise")
    if s["closed"]:
        print(f"  Rata de succes: {s['win_rate']}%")
        print(f"  R total: {s['total_r']:+.2f}R  |  R mediu/plan: {s['avg_r']:+.3f}R")
        if s["profit_factor"] is not None:
            print(f"  Profit factor: {s['profit_factor']}")
        print(f"  Cel mai bun: {s['best']:+.2f}R  |  cel mai slab: {s['worst']:+.2f}R")
    print(f"  Stari: {s['by_state']}")
