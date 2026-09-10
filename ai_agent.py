#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ai_agent.py
============
Agent cu invatare ONLINE (incrementala): invata din fiecare semnal evaluat,
unul cate unul, si devine mai bun pe masura ce se acumuleaza scanari.

Diferenta fata de ce exista deja in proiect:
  - `evaluate_and_learn` din scanner ajusteaza 4 ponderi cu +/-5% - o
    euristica, nu invatare din date.
  - Agentul asta invata efectiv relatia (caracteristici -> rezultat) din
    istoric, prin regresie logistica antrenata cu SGD. E model statistic
    real, cu greutati invatate din date, nu setate de mine.

CE INVATA
---------
Intrari (deja calculate de scanner pentru fiecare semnal):
  trend, momentum, volatility, volume, is_long, persistence
Iesire: probabilitatea ca semnalul sa fie "hit" (pretul s-a miscat in
directia prezisa cu cel putin hit_threshold_atr x ATR in lookahead_hours).

MEMORIE IN CLOUD
----------------
Starea (greutatile invatate + statistici) se salveaza in
data/agent_model.json, comis pe git la fiecare rulare de catre workflow.
Asta E memoria in cloud: gratuita, versionata, si poti vedea literal in
`git log` cum s-au schimbat greutatile in timp.

EVALUARE CORECTA (prequential / test-then-train)
------------------------------------------------
Pentru fiecare exemplu nou: intai PREZICE (si notez daca a nimerit), abia
apoi INVATA din el. Asa acuratetea raportata e onesta - masurata mereu pe
date pe care modelul nu le vazuse inca. E standardul in invatarea online.

MOD SHADOW
----------
Agentul NU influenteaza deciziile pana nu demonstreaza ca bate euristica
existenta, pe minim MIN_SAMPLES_TO_ACTIVATE exemple. Pana atunci doar
observa si isi masoara performanta. Nu vreau ca un model neantrenat sa
strice semnalele care deja functioneaza.

Ruleaza dupa scanner:
    python3 crypto_ai_scanner.py && python3 ai_agent.py
"""

import json
import math
import os

import plan_tracker

DATA_DIR = "data"
HISTORY_FILE = os.path.join(DATA_DIR, "scan_history.json")
PLANS_FILE = os.path.join(DATA_DIR, "plans.json")
# Legata de geometria planurilor: v2 intra la piata, v3 intra pe pullback.
# Acelasi vector de caracteristici duce la rezultate diferite sub cele doua
# sisteme, deci antrenarea pe amandoua ar invata media a doua functii diferite.
# La schimbarea geometriei, agentul reporneste - costa exemplele acumulate, dar
# oricum ramane in SHADOW pana la 300, deci pierderea e doar contabila.
STATE_SOURCE = "plans-v4"
# Versiunea urcata odata cu adaugarea metricilor pentru date dezechilibrate
# (AUC, prag de clasa majoritara, rata de predictii pozitive). Perechile
# (predictie, rezultat) pe care se calculeaza se acumuleaza doar la invatare,
# deci fara reset noile praguri n-ar avea pe ce lucra. Reinvatarea nu costa
# nimic: agentul e oricum in SHADOW pana la 300 de exemple.
MODEL_FILE = os.path.join(DATA_DIR, "agent_model.json")

FEATURES = ["trend", "momentum", "volatility", "volume", "is_long", "persistence_n"]

LEARNING_RATE = 0.05
L2 = 1e-4
MIN_SAMPLES_TO_ACTIVATE = 300   # sub atat, agentul ramane in mod shadow
MIN_DAYS_TO_ACTIVATE = 21       # ...si trebuie sa acopere si destul timp calendaristic
MIN_AUC = 0.55                  # sub atat, modelul nu ordoneaza mai bine decat hazardul
AUC_WINDOW = 500                # cate perechi (predictie, rezultat) pastrez pentru AUC
RECENT_WINDOW = 200             # fereastra pentru acuratetea "recenta"
CURVE_EVERY = 25                # la cate exemple salvez un punct pe curba

# DE CE MIN_DAYS_TO_ACTIVATE, pe langa numarul de exemple:
# Pe datele reale din acest proiect, 634 de exemple stranse in doar 41 de ore
# au dat o acuratete aparenta de 76% - dar LONG avea 19.7% hit si SHORT 77.7%.
# Piata pur si simplu scazuse in acea fereastra. Modelul invatase "prezice
# SHORT" - memorare de regim, nu avantaj real, si s-ar intoarce complet la
# prima inversare de trend. 634 de exemple din 41 de ore NU sunt 634 de
# observatii independente. De aceea activarea cere si acoperire in timp
# (mai multe regimuri de piata), si acuratete ECHILIBRATA, nu bruta.


# ======================= MODEL: REGRESIE LOGISTICA ONLINE ==================

class OnlineLogisticRegression:
    """Regresie logistica antrenata cu SGD, un exemplu pe rand.

    Implementata direct (~40 de linii) in loc de o librarie externa - vezi
    nota din raspuns. Serializeaza in JSON curat, deci greutatile invatate
    sunt lizibile si urmaribile in git diff, si nu adauga nicio dependinta
    de instalat la fiecare rulare de GitHub Actions.
    """

    def __init__(self, lr=LEARNING_RATE, l2=L2):
        self.lr = lr
        self.l2 = l2
        self.weights = {f: 0.0 for f in FEATURES}
        self.bias = 0.0

    @staticmethod
    def _sigmoid(z):
        # forma numeric stabila, evita overflow pe exponent mare
        if z >= 0:
            return 1.0 / (1.0 + math.exp(-z))
        e = math.exp(z)
        return e / (1.0 + e)

    def predict_proba(self, x):
        z = self.bias + sum(self.weights.get(f, 0.0) * x.get(f, 0.0) for f in FEATURES)
        return self._sigmoid(z)

    def learn_one(self, x, y):
        """Un pas de SGD pe gradientul log-loss, cu regularizare L2."""
        p = self.predict_proba(x)
        error = p - y
        for f in FEATURES:
            grad = error * x.get(f, 0.0) + self.l2 * self.weights.get(f, 0.0)
            self.weights[f] = self.weights.get(f, 0.0) - self.lr * grad
        self.bias -= self.lr * error
        return p

    def to_dict(self):
        return {"weights": {k: round(v, 6) for k, v in self.weights.items()},
                "bias": round(self.bias, 6)}

    @classmethod
    def from_dict(cls, d):
        m = cls()
        m.weights = {f: float(d.get("weights", {}).get(f, 0.0)) for f in FEATURES}
        m.bias = float(d.get("bias", 0.0))
        return m


# ============================ CARACTERISTICI ==============================

def extract_features(result):
    """Accepta atat un rezultat de scanare cat si un plan - planurile pastreaza
    contextul de la momentul deschiderii sub alte nume de campuri."""
    """Transforma un rezultat de scanare in vectorul de intrare al modelului.
    Componentele sunt deja 0-1; persistenta o normalizez si o plafonez, ca sa
    nu domine restul doar pentru ca e un numar mai mare."""
    comp = result.get("components") or {}
    return {
        "trend": float(comp.get("trend", 0.0)),
        "momentum": float(comp.get("momentum", 0.0)),
        "volatility": float(comp.get("volatility", 0.0)),
        "volume": float(comp.get("volume", 0.0)),
        "is_long": 1.0 if result.get("direction") == "LONG" else 0.0,
        "persistence_n": min(float(
            result.get("persistence", result.get("persistence_at_entry", 0)) or 0) / 10.0, 1.0),
    }


def heuristic_proba(result):
    """Ce ar fi prezis sistemul euristic existent - baseline-ul de batut."""
    return float(result.get("probability", 50.0)) / 100.0


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


def default_state():
    return {
        "model": OnlineLogisticRegression().to_dict(),
        "samples_trained": 0,
        "last_trained_scan_ts": 0.0,
        "agent": {"correct": 0, "total": 0},
        "baseline": {"correct": 0, "total": 0},
        # defalcat pe directie - ca sa se vada daca modelul doar calareste
        # un regim de piata (ex: numai shorturile castiga) in loc sa invete
        "by_direction": {
            "LONG": {"agent": 0, "baseline": 0, "total": 0},
            "SHORT": {"agent": 0, "baseline": 0, "total": 0},
        },
        "first_scan_ts": 0.0,
        "pairs": [],           # (probabilitate, rezultat) - pentru AUC si clasa majoritara
        "recent": [],          # 1/0 pentru ultimele predictii ale agentului
        "recent_baseline": [],
        "curve": [],           # puncte pentru graficul learning curve
        "status": "SHADOW",
    }


def auc_score(pairs):
    """Aria sub curba ROC, calculata prin numararea perechilor concordante.

    DE CE E NECESARA: pe date dezechilibrate, acuratetea insala. Cu 13.3% rata de
    castig, un model care spune MEREU "pierde" obtine 86.7% acuratete fara sa fi
    invatat nimic. Exact asta s-a intamplat: agentul nu prezicea castig in niciun
    caz, iar acuratetea lui (83.3%) era chiar SUB regula triviala. AUC masoara
    altceva - daca modelul ORDONEAZA corect: 0.5 = hazard, 1.0 = separare perfecta.
    """
    pos = [p for p, y in pairs if y == 1.0]
    neg = [p for p, y in pairs if y == 0.0]
    if not pos or not neg:
        return None
    concordant = 0.0
    for a_ in pos:
        for b_ in neg:
            concordant += 1.0 if a_ > b_ else (0.5 if a_ == b_ else 0.0)
    return concordant / (len(pos) * len(neg))


def majority_class_accuracy(pairs):
    """Acuratetea pe care o obtii prezicand mereu clasa majoritara. E pragul REAL
    pe care un model trebuie sa-l depaseasca; formula veche nu era suficienta."""
    if not pairs:
        return None
    ones = sum(1 for _, y in pairs if y == 1.0)
    return 100 * max(ones, len(pairs) - ones) / len(pairs)


def predicted_positive_rate(pairs):
    """Cat de des prezice modelul 'castig'. Aproape de 0 sau 1 = model degenerat."""
    if not pairs:
        return None
    return 100 * sum(1 for p, _ in pairs if p >= 0.5) / len(pairs)


def balanced_accuracy(by_direction, which, min_per_direction=30):
    """Media acuratetii pe LONG si pe SHORT, nu acuratetea bruta.
    Un model care prezice mereu SHORT intr-o piata in scadere are acuratete
    bruta mare, dar acuratete echilibrata ~50% - exact ce vreau sa expun.

    Returneaza (valoare, directii_incluse). Daca o directie nu are destule
    exemple e exclusa - dar atunci rezultatul NU mai e echilibrat, si apelantul
    trebuie sa stie asta. Inainte se raporta valoarea unei singure directii sub
    eticheta "echilibrat", ceea ce era inselator."""
    accs, used = [], []
    for d in ("LONG", "SHORT"):
        stats = by_direction.get(d, {})
        if stats.get("total", 0) >= min_per_direction:
            accs.append(stats[which] / stats["total"])
            used.append(d)
    if not accs:
        return None, []
    return 100 * sum(accs) / len(accs), used


def days_covered(state, plans=None):
    """Acoperirea calendaristica reala a datelor din care a invatat agentul.

    BUG FIX: `first` se lua din starea salvata si nu cobora niciodata. Dupa ce
    au fost adaugate 5510 planuri de backtest, vechi de pana la 180 de zile,
    acoperirea raportata a ramas 7.8 zile - valoarea fixata la prima rulare
    live. Agentul era blocat pe nedrept sub pragul de 21 de zile, desi avea
    172 de zile de date. Acum se ia MINIMUL peste tot ce exista, nu prima
    valoare intalnita.
    """
    candidates_first = [t for t in [state.get("first_scan_ts")] if t]
    candidates_last = [t for t in [state.get("last_event_ts")] if t]
    if plans:
        candidates_first += [p["created_ts"] for p in plans if p.get("created_ts")]
        candidates_last += [p["closed_ts"] for p in plans if p.get("closed_ts")]
    if not candidates_first or not candidates_last:
        return 0.0
    first, last = min(candidates_first), max(candidates_last)
    return (last - first) / 86400 if last > first else 0.0


def load_agent():
    """Folosita si de scanner ca sa obtina predictii, fara sa reantreneze."""
    state = load_json(MODEL_FILE, default_state())
    model = OnlineLogisticRegression.from_dict(state.get("model", {}))
    return model, state


def agent_is_active(state, plans=None):
    """Agentul influenteaza deciziile doar daca trece TOATE conditiile:
      1. a vazut destule exemple
      2. acopera destule zile (mai multe regimuri de piata, nu doar unul)
      3. bate euristica la acuratete ECHILIBRATA (media pe LONG si SHORT)
    Returneaza (activ, motiv) - motivul e afisat in dashboard."""
    a = state.get("agent", {})
    if a.get("total", 0) < MIN_SAMPLES_TO_ACTIVATE:
        return False, f"are nevoie de {MIN_SAMPLES_TO_ACTIVATE} exemple (are {a.get('total', 0)})"

    days = days_covered(state, plans or [])
    if days < MIN_DAYS_TO_ACTIVATE:
        return False, (f"acopera doar {days:.1f} zile din {MIN_DAYS_TO_ACTIVATE} necesare "
                       f"(prea putine regimuri de piata)")

    bd = state.get("by_direction", {})
    bal_agent, used_a = balanced_accuracy(bd, "agent")
    bal_base, used_b = balanced_accuracy(bd, "baseline")
    if bal_agent is None or bal_base is None or len(used_a) < 2:
        return False, ("inca nu am destule exemple pe AMBELE directii "
                       f"(am: {', '.join(used_a) if used_a else 'niciuna'})")

    # PRAGURI PENTRU DATE DEZECHILIBRATE. Fara ele, un model care spune mereu
    # "pierde" pare excelent: la 13.3% rata de castig obtine 86.7% acuratete.
    # Exact asta se intampla - agentul nu prezicea castig in niciun caz.
    pairs = state.get("pairs") or []
    maj = majority_class_accuracy(pairs)
    acc = 100 * a["correct"] / a["total"]
    if maj is not None and acc <= maj:
        return False, (f"acuratetea {acc:.1f}% nu bate regula triviala "
                       f"'prezice mereu clasa majoritara' ({maj:.1f}%)")

    ppr = predicted_positive_rate(pairs)
    if ppr is not None and (ppr < 5 or ppr > 95):
        return False, (f"model degenerat: prezice 'castig' in {ppr:.0f}% din cazuri "
                       f"- nu discrimineaza, doar reproduce clasa dominanta")

    auc = auc_score(pairs)
    if auc is None:
        return False, "inca nu am ambele clase (castig si pierdere) in fereastra"
    if auc < MIN_AUC:
        return False, (f"AUC {auc:.3f} sub pragul {MIN_AUC} - modelul nu ordoneaza "
                       f"semnalele mai bine decat hazardul (0.5)")
    if bal_agent <= bal_base:
        return False, (f"acuratete echilibrata {bal_agent:.1f}% nu bate inca "
                       f"euristica ({bal_base:.1f}%)")
    return True, f"acuratete echilibrata {bal_agent:.1f}% vs euristica {bal_base:.1f}%"


# ============================== ANTRENARE =================================

def train_from_plans(plans, model, state):
    """Invata din PLANURI INCHISE, nu din campul `outcome` al scanarilor.

    DE CE AM SCHIMBAT SEMNALUL DE INVATARE:
    `outcome` (hit/miss) compara pretul de intrare cu pretul dintr-un singur
    moment, la 24h. Nu spune daca tranzactia a functionat - un semnal care a
    atins TP si apoi a revenit conta "miss", iar unul care a trecut prin SL si
    si-a revenit conta "hit". Agentul e chemat sa decida daca merita deschis un
    plan, deci trebuie sa invete exact din ce inseamna "planul a mers": R
    realizat, masurat bara cu bara.

    Consecinta onesta: contorul de exemple reporneste de la zero cand se
    schimba sursa de semnal. Datele vechi masurau altceva; amestecarea lor ar
    fi produs un model antrenat pe doua definitii diferite ale succesului.

    Fiecare plan e invatat exact o data (marcat cu `agent_trained`).

    BUG FIX 1: filtrul era scris hardcodat "v2". Cand geometria a trecut la v3,
    agentul a continuat sa invete din planurile VECHI si sa le ignore pe cele
    curente - exact pe dos. Acum se leaga de plan_tracker.GEOMETRY_VERSION.

    BUG FIX 2: planurile NO_ENTRY (pretul nu a revenit la zona de intrare) sunt
    excluse complet. Au realized_r = 0.0, iar regula `y = 1 daca r > 0` le
    transforma in exemple NEGATIVE - agentul invata ca acele configuratii esueaza,
    cand de fapt nicio tranzactie nu a avut loc. E o eticheta falsa, nu un rezultat.
    """
    closed = [p for p in plans
              if p.get("realized_r") is not None and not p.get("agent_trained")
              and p.get("geometry", "v1") == plan_tracker.GEOMETRY_VERSION
              and p.get("state") != plan_tracker.STATE_NO_ENTRY]
    closed.sort(key=lambda p: p.get("closed_ts") or 0)

    new_samples = 0
    for p in closed:
        y = 1.0 if p["realized_r"] > 0 else 0.0
        x = extract_features(p)

        # 1) INTAI prezic (pe date nevazute) - acuratete onesta
        p_agent = model.predict_proba(x)
        # baseline: formula pe care o afisa sistemul inainte de calibrare
        score = p.get("score_at_entry") or 0
        p_base = min(50 + score * 0.35, 88) / 100.0

        agent_ok = 1 if (p_agent >= 0.5) == (y == 1.0) else 0
        base_ok = 1 if (p_base >= 0.5) == (y == 1.0) else 0

        state["agent"]["correct"] += agent_ok
        state["agent"]["total"] += 1
        state["baseline"]["correct"] += base_ok
        state["baseline"]["total"] += 1

        d = p.get("direction")
        if d in state["by_direction"]:
            state["by_direction"][d]["agent"] += agent_ok
            state["by_direction"][d]["baseline"] += base_ok
            state["by_direction"][d]["total"] += 1

        state["pairs"] = (state.get("pairs", []) + [[round(p_agent, 5), y]])[-AUC_WINDOW:]
        # perechi paralele pentru SCORUL brut, ca sa pot compara ordonarea:
        # nu e de-ajuns ca agentul sa bata hazardul, trebuie sa bata euristica
        # pe care ar urma sa o inlocuiasca.
        state["score_pairs"] = (state.get("score_pairs", []) +
                                [[round((score or 0) / 100.0, 5), y]])[-AUC_WINDOW:]
        state["recent"] = (state.get("recent", []) + [agent_ok])[-RECENT_WINDOW:]
        state["recent_baseline"] = (state.get("recent_baseline", []) + [base_ok])[-RECENT_WINDOW:]

        # 2) ABIA APOI invat din el
        model.learn_one(x, y)
        p["agent_trained"] = True
        new_samples += 1

        total = state["agent"]["total"]
        if total % CURVE_EVERY == 0:
            state["curve"].append({
                "n": total,
                "agent": round(100 * state["agent"]["correct"] / total, 2),
                "baseline": round(100 * state["baseline"]["correct"] / state["baseline"]["total"], 2),
                "agent_recent": round(100 * sum(state["recent"]) / len(state["recent"]), 2),
            })

    if closed:
        first = min((p.get("created_ts") or 0) for p in plans if p.get("created_ts"))
        last = max((p.get("closed_ts") or 0) for p in plans if p.get("closed_ts"))
        prev_first = state.get("first_scan_ts")
        state["first_scan_ts"] = min(prev_first, first) if prev_first else first
        state["last_event_ts"] = last

    state["samples_trained"] = state.get("samples_trained", 0) + new_samples
    return new_samples


def predict_for_signal(model, state, signal):
    """Probabilitatea agentului pentru un semnal nou, plus daca are voie sa
    influenteze decizia (doar cand e ACTIVE)."""
    x = extract_features(signal)
    return {
        "probability": round(model.predict_proba(x), 4),
        "active": state.get("status") == "ACTIVE",
        # Agentul influenteaza EV doar daca ordoneaza mai bine decat scorul brut.
        "superior": bool(state.get("agent_superior")),
    }


def train_incremental(history, model, state):
    """Parcurge doar scanarile netreantrenate inca, in ordine cronologica.
    Fiecare exemplu e folosit exact o data - altfel modelul ar vedea aceleasi
    date de zeci de ori si s-ar supraantrena pe ele."""
    last_ts = state.get("last_trained_scan_ts", 0.0)
    new_samples = 0
    max_ts = last_ts

    for scan in sorted(history, key=lambda s: s.get("scan_id_ts", 0)):
        ts = scan.get("scan_id_ts", 0)
        if ts <= last_ts:
            continue
        for r in scan.get("results", []):
            outcome = r.get("outcome")
            if outcome not in ("hit", "miss"):
                continue  # inca neevaluat - nu am eticheta, deci nu pot invata
            y = 1.0 if outcome == "hit" else 0.0
            x = extract_features(r)

            # 1) INTAI prezic (pe date nevazute) - asta da acuratetea onesta
            p_agent = model.predict_proba(x)
            p_base = heuristic_proba(r)
            agent_ok = 1 if (p_agent >= 0.5) == (y == 1.0) else 0
            base_ok = 1 if (p_base >= 0.5) == (y == 1.0) else 0

            state["agent"]["correct"] += agent_ok
            state["agent"]["total"] += 1
            state["baseline"]["correct"] += base_ok
            state["baseline"]["total"] += 1

            d = r.get("direction")
            if d in state["by_direction"]:
                state["by_direction"][d]["agent"] += agent_ok
                state["by_direction"][d]["baseline"] += base_ok
                state["by_direction"][d]["total"] += 1

            state["pairs"] = (state.get("pairs", []) + [[round(p_agent, 5), y]])[-AUC_WINDOW:]
            state["recent"] = (state.get("recent", []) + [agent_ok])[-RECENT_WINDOW:]
            state["recent_baseline"] = (state.get("recent_baseline", []) + [base_ok])[-RECENT_WINDOW:]

            # 2) ABIA APOI invat din el
            model.learn_one(x, y)
            new_samples += 1

            total = state["agent"]["total"]
            if total % CURVE_EVERY == 0:
                state["curve"].append({
                    "n": total,
                    "agent": round(100 * state["agent"]["correct"] / total, 2),
                    "baseline": round(100 * state["baseline"]["correct"] / state["baseline"]["total"], 2),
                    "agent_recent": round(100 * sum(state["recent"]) / len(state["recent"]), 2),
                })

        max_ts = max(max_ts, ts)

    state["last_trained_scan_ts"] = max_ts
    state["samples_trained"] = state.get("samples_trained", 0) + new_samples
    return new_samples


def summarize(state):
    a, b = state["agent"], state["baseline"]
    acc_agent = 100 * a["correct"] / a["total"] if a["total"] else None
    acc_base = 100 * b["correct"] / b["total"] if b["total"] else None
    recent = state.get("recent", [])
    acc_recent = 100 * sum(recent) / len(recent) if recent else None
    return acc_agent, acc_base, acc_recent


def main():
    plans_store = load_json(PLANS_FILE, {"plans": []})
    plans = plans_store.get("plans", [])

    state = load_json(MODEL_FILE, None)
    # Daca starea salvata provine din alta sursa/geometrie de semnal (campul `outcome`),
    # o resetez: etichetele masurau altceva. Vezi nota din train_from_plans.
    if state is None or state.get("source") != STATE_SOURCE:
        if state is not None:
            print(f"[i] Resetez agentul: sursa de invatare s-a schimbat "
                  f"({state.get('source')} -> {STATE_SOURCE}).")
            # BUG FIX: la reset trebuie sterse si marcajele `agent_trained` de pe
            # planurile geometriei CURENTE. Fara asta, planurile deja marcate erau
            # sarite dupa reset, iar agentul repornea la zero si ramanea acolo -
            # nu mai avea din ce sa invete pana la urmatoarele planuri noi.
            cleared = 0
            for p in plans:
                if (p.get("geometry", "v1") == plan_tracker.GEOMETRY_VERSION
                        and p.pop("agent_trained", None)):
                    cleared += 1
            if cleared:
                print(f"[i] Am eliberat {cleared} planuri {plan_tracker.GEOMETRY_VERSION} "
                      f"pentru reinvatare.")
        state = default_state()
        state["source"] = STATE_SOURCE
    model = OnlineLogisticRegression.from_dict(state.get("model", {}))

    if not plans:
        print("Niciun plan inca - agentul invata din planuri inchise. "
              "Ruleaza intai crypto_ai_scanner.py.")
        save_json(MODEL_FILE, state)
        return

    new_samples = train_from_plans(plans, model, state)
    save_json(PLANS_FILE, plans_store)  # persist marcajele agent_trained

    state["model"] = model.to_dict()
    active, reason = agent_is_active(state, plans)
    state["status"] = "ACTIVE" if active else "SHADOW"
    state["status_reason"] = reason
    state["days_covered"] = round(days_covered(state, plans), 2)
    ba, used = balanced_accuracy(state["by_direction"], "agent")
    bb, _ = balanced_accuracy(state["by_direction"], "baseline")
    state["balanced_agent"], state["balanced_baseline"] = ba, bb
    state["balanced_directions"] = used
    pairs = state.get("pairs") or []
    state["auc"] = auc_score(pairs)
    state["auc_score_baseline"] = auc_score(state.get("score_pairs") or [])
    sa, sb = state["auc"], state["auc_score_baseline"]
    state["agent_superior"] = bool(sa is not None and sb is not None and sa > sb)
    state["majority_baseline"] = majority_class_accuracy(pairs)
    state["predicted_positive_rate"] = predicted_positive_rate(pairs)
    save_json(MODEL_FILE, state)

    acc_agent, acc_base, acc_recent = summarize(state)
    print(f"Planuri noi invatate acum: {new_samples}")
    print(f"Total planuri invatate: {state['agent']['total']} pe {state['days_covered']} zile")
    if acc_agent is not None:
        print(f"Acuratete BRUTA       - agent {acc_agent:.2f}% | baseline {acc_base:.2f}%")
        ba, bb = state["balanced_agent"], state["balanced_baseline"]
        if ba is not None:
            lbl = ("ECHILIBRATA" if len(used) == 2
                   else f"doar {used[0]} (cealalta directie sub prag)")
            print(f"Acuratete {lbl} - agent {ba:.2f}% | baseline {bb:.2f}%")
        for d in ("LONG", "SHORT"):
            st = state["by_direction"][d]
            if st["total"]:
                print(f"  {d}: agent {100*st['agent']/st['total']:.1f}% "
                      f"| baseline {100*st['baseline']/st['total']:.1f}% (din {st['total']})")
    auc = state.get("auc")
    maj = state.get("majority_baseline")
    ppr = state.get("predicted_positive_rate")
    if maj is not None:
        print(f"Prag trivial (clasa majoritara): {maj:.2f}%  <- de batut, nu doar baseline-ul vechi")
    if ppr is not None:
        print(f"Prezice 'castig' in {ppr:.0f}% din cazuri" +
              ("  [!] model degenerat" if ppr < 5 or ppr > 95 else ""))
    if auc is not None:
        sb = state.get("auc_score_baseline")
        extra = f" | AUC scor brut: {sb:.3f}" if sb is not None else ""
        verdict = ("agentul ordoneaza mai bine" if state.get("agent_superior")
                   else "scorul brut ordoneaza cel putin la fel de bine")
        print(f"AUC: {auc:.3f} (0.5 = hazard, prag {MIN_AUC}){extra} -> {verdict}")
    print(f"Status: {state['status']} - {reason}")
    print("Greutati invatate:", json.dumps(state["model"]["weights"]))


if __name__ == "__main__":
    main()
