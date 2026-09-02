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

DATA_DIR = "data"
HISTORY_FILE = os.path.join(DATA_DIR, "scan_history.json")
MODEL_FILE = os.path.join(DATA_DIR, "agent_model.json")

FEATURES = ["trend", "momentum", "volatility", "volume", "is_long", "persistence_n"]

LEARNING_RATE = 0.05
L2 = 1e-4
MIN_SAMPLES_TO_ACTIVATE = 300   # sub atat, agentul ramane in mod shadow
MIN_DAYS_TO_ACTIVATE = 21       # ...si trebuie sa acopere si destul timp calendaristic
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
        "persistence_n": min(float(result.get("persistence", 0)) / 10.0, 1.0),
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
        "recent": [],          # 1/0 pentru ultimele predictii ale agentului
        "recent_baseline": [],
        "curve": [],           # puncte pentru graficul learning curve
        "status": "SHADOW",
    }


def balanced_accuracy(by_direction, which):
    """Media acuratetii pe LONG si pe SHORT, nu acuratetea bruta.
    Un model care prezice mereu SHORT intr-o piata in scadere are acuratete
    bruta mare, dar acuratete echilibrata ~50% - exact ce vreau sa expun."""
    accs = []
    for d in ("LONG", "SHORT"):
        stats = by_direction.get(d, {})
        if stats.get("total", 0) >= 30:   # prea putine exemple = nu numar directia
            accs.append(stats[which] / stats["total"])
    return 100 * sum(accs) / len(accs) if accs else None


def days_covered(state, history):
    first = state.get("first_scan_ts") or (min((s.get("scan_id_ts", 0) for s in history), default=0))
    last = max((s.get("scan_id_ts", 0) for s in history), default=0)
    return (last - first) / 86400 if first and last else 0.0


def load_agent():
    """Folosita si de scanner ca sa obtina predictii, fara sa reantreneze."""
    state = load_json(MODEL_FILE, default_state())
    model = OnlineLogisticRegression.from_dict(state.get("model", {}))
    return model, state


def agent_is_active(state, history=None):
    """Agentul influenteaza deciziile doar daca trece TOATE conditiile:
      1. a vazut destule exemple
      2. acopera destule zile (mai multe regimuri de piata, nu doar unul)
      3. bate euristica la acuratete ECHILIBRATA (media pe LONG si SHORT)
    Returneaza (activ, motiv) - motivul e afisat in dashboard."""
    a = state.get("agent", {})
    if a.get("total", 0) < MIN_SAMPLES_TO_ACTIVATE:
        return False, f"are nevoie de {MIN_SAMPLES_TO_ACTIVATE} exemple (are {a.get('total', 0)})"

    days = days_covered(state, history or [])
    if days < MIN_DAYS_TO_ACTIVATE:
        return False, (f"acopera doar {days:.1f} zile din {MIN_DAYS_TO_ACTIVATE} necesare "
                       f"(prea putine regimuri de piata)")

    bd = state.get("by_direction", {})
    bal_agent = balanced_accuracy(bd, "agent")
    bal_base = balanced_accuracy(bd, "baseline")
    if bal_agent is None or bal_base is None:
        return False, "inca nu am destule exemple pe ambele directii"
    if bal_agent <= bal_base:
        return False, (f"acuratete echilibrata {bal_agent:.1f}% nu bate inca "
                       f"euristica ({bal_base:.1f}%)")
    return True, f"acuratete echilibrata {bal_agent:.1f}% vs euristica {bal_base:.1f}%"


# ============================== ANTRENARE =================================

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
    history = load_json(HISTORY_FILE, [])
    if not history:
        print("Nu exista inca nicio scanare - ruleaza intai crypto_ai_scanner.py.")
        return

    model, state = load_agent()
    if not state.get("first_scan_ts"):
        state["first_scan_ts"] = min((s.get("scan_id_ts", 0) for s in history), default=0.0)

    new_samples = train_incremental(history, model, state)

    state["model"] = model.to_dict()
    active, reason = agent_is_active(state, history)
    state["status"] = "ACTIVE" if active else "SHADOW"
    state["status_reason"] = reason
    state["days_covered"] = round(days_covered(state, history), 2)
    state["balanced_agent"] = balanced_accuracy(state["by_direction"], "agent")
    state["balanced_baseline"] = balanced_accuracy(state["by_direction"], "baseline")
    save_json(MODEL_FILE, state)

    acc_agent, acc_base, acc_recent = summarize(state)
    print(f"Exemple noi invatate acum: {new_samples}")
    print(f"Total exemple vazute: {state['agent']['total']} pe {state['days_covered']} zile")
    if acc_agent is not None:
        print(f"Acuratete BRUTA    - agent {acc_agent:.2f}% | euristica {acc_base:.2f}%")
        ba, bb = state["balanced_agent"], state["balanced_baseline"]
        if ba is not None:
            print(f"Acuratete ECHILIBRATA - agent {ba:.2f}% | euristica {bb:.2f}%  <- asta conteaza")
        print("Defalcare pe directie:")
        for d in ("LONG", "SHORT"):
            s = state["by_direction"][d]
            if s["total"]:
                print(f"  {d}: agent {100*s['agent']/s['total']:.1f}% "
                      f"| euristica {100*s['baseline']/s['total']:.1f}% (din {s['total']})")
    print(f"Status: {state['status']} - {reason}")
    print("Greutati invatate:", json.dumps(state["model"]["weights"]))


if __name__ == "__main__":
    main()
