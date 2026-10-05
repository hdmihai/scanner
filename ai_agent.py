# -*- coding: utf-8 -*-
"""
ai_agent.py - adaptorul de stocare al agentului, jobul de antrenare si fatada nucleului core/agent.py.

Modelul, caracteristicile, antrenarea si predictia sunt in nucleu, fara fisiere.
Aici raman: citirea si scrierea modelului (agent_model.json), carantina citita din
self_check.json, excluderile salvate, si antrenarea ca job (`python ai_agent.py`,
pasul din scan.yml). Celelalte nume (ai_agent.predict_for_signal,
ai_agent.comparable_entries, ...) se citesc si se scriu direct in nucleu - vezi
adapters/compat.py.
"""

import json
import os

import plan_tracker
from adapters import compat
from core import agent as _core
from core import evidence as ev_mod

DATA_DIR = "data"


HISTORY_FILE = os.path.join(DATA_DIR, "scan_history.json")


PLANS_FILE = os.path.join(DATA_DIR, "plans.json")


MODEL_FILE = os.path.join(DATA_DIR, "agent_model.json")


_Q_CACHE = {"ts": 0, "set": set()}


def _quarantined():
    """Caracteristicile puse in carantina de auto-diagnostic (reincarcate cel
    mult o data pe minut - extract_features ruleaza pentru fiecare plan)."""
    import time as _t
    if _t.time() - _Q_CACHE["ts"] > 60:
        try:
            with open(os.path.join(os.path.dirname(MODEL_FILE), "self_check.json")) as f:
                q = (json.load(f).get("mitigations") or {}).get("quarantine") or {}
            _Q_CACHE["set"] = set(q)
        except Exception:
            _Q_CACHE["set"] = set()
        _Q_CACHE["ts"] = _t.time()
    return _Q_CACHE["set"]


_X_CACHE = {"ts": 0, "set": set(_core.LIVE_ONLY_FEATURES)}


def excluded_features():
    """Caracteristicile excluse din model: mereu cele doar-live, plus cele cu decalaj
    de prezenta masurat (salvate in starea agentului, reincarcate o data pe minut)."""
    import time as _t
    if _t.time() - _X_CACHE["ts"] > 60:
        try:
            with open(MODEL_FILE) as f:
                _X_CACHE["set"] = set(json.load(f).get("skew_excluded") or []) | _core.LIVE_ONLY_FEATURES
        except Exception:
            _X_CACHE["set"] = set(_core.LIVE_ONLY_FEATURES)
        _X_CACHE["ts"] = _t.time()
    return _X_CACHE["set"]


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # SCRIERE ATOMICA: fisier temporar, golit pe disc, apoi inlocuire intr-un singur
    # pas. Un job oprit in timpul scrierii (timeout, anulare) lasa intact fisierul
    # vechi - pasul de commit din workflow ruleaza cu if: always() si ar fi urcat
    # un JSON trunchiat, oprind toate scanarile urmatoare.
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_agent():
    """Folosita si de scanner ca sa obtina predictii, fara sa reantreneze."""
    state = load_json(MODEL_FILE, _core.default_state())
    model = _core.OnlineLogisticRegression.from_dict(state.get("model", {}))
    return model, state


def main():
    plans_store = load_json(PLANS_FILE, {"plans": []})
    plans = plans_store.get("plans", [])
    import time as _t
    _skew = _core.feature_skew(plans)
    _excl = _core.skew_excluded(_skew)
    _X_CACHE.update(ts=_t.time() + 10 ** 9, set=set(_excl))     # fix pentru aceasta rulare
    if _excl:
        print(f"[i] Excluse din model (decalaj live-backtest): {', '.join(_excl)}")

    state = load_json(MODEL_FILE, None)
    # MASCA DE CARACTERISTICI (excluse + carantina). Cand se schimba, reantrenez de la
    # zero: o caracteristica zerorizata cat timp modelul s-a antrenat ramane cu greutatea
    # 0 si dupa ridicarea carantinei (ev_elliott: 0.0 dupa o carantina falsa de 2 zile).
    _mask = sorted(set(_excl) | set(_quarantined()))
    if state is not None and state.get("feature_mask") != _mask:
        print(f"[i] Masca de caracteristici s-a schimbat ({state.get('feature_mask')} -> {_mask}) - "
              "reantrenez de la zero pe toate planurile.")
        state = None
        # fara asta, modelul nou nu ar invata nimic: planurile poarta marcajul
        # agent_trained de la antrenarea anterioara (testat: 0 planuri invatate)
        for p in plans:
            p.pop("agent_trained", None)
    # Daca starea salvata provine din alta sursa/geometrie de semnal (campul `outcome`),
    # o resetez: etichetele masurau altceva. Vezi nota din train_from_plans.
    source = _core.current_source()

    # EXTINDEREA SETULUI DE CARACTERISTICI, fara reset.
    # Cand apar caracteristici noi (ex. ichimoku, mtf_align), le adaug in model
    # cu greutate 0 si pastrez tot ce s-a invatat pana acum. Greutatea 0
    # inseamna "nu stiu inca nimic despre asta" - exact starea corecta - si se
    # invata din planurile urmatoare. Alternativa pe care o foloseam, resetul
    # complet, arunca zeci de mii de exemple pentru o schimbare care nu afecta
    # deloc rezultatele.
    fv = getattr(plan_tracker, "FEATURE_VERSION", "f1")
    if state is not None and state.get("source") == source:
        # BUG FIX: operez pe state["model"]["weights"], NU pe `model` - obiectul
        # `model` se construieste mai jos, din aceasta stare. Varianta initiala
        # scria intr-un obiect care nu exista inca in acest punct, deci
        # extinderea nu se aplica niciodata: modelul ramanea cu setul vechi de
        # greutati si caracteristicile noi erau ignorate tacut.
        mw = (state.get("model") or {}).get("weights")
        if isinstance(mw, dict):
            fresh = [f for f in _core.FEATURES if f not in mw]
            if fresh:
                for f in fresh:
                    mw[f] = 0.0
                print(f"[i] Caracteristici noi adaugate fara reset: {', '.join(fresh)}")
                print(f"    Pastrez cele {state.get('samples', 0)} exemple invatate; "
                      f"cele noi pornesc de la greutate 0.")
                state["feature_version"] = fv

    # RECONSTRUIRE UNICA A EVALUARII. Starea scrisa de versiunea anterioara are
    # perechi (predictie, rezultat) FARA R, iar criteriul de activare masoara
    # acum castigul in R al treimii de sus. Toate planurile sunt deja marcate
    # `agent_trained`, deci perechi noi cu R ar aparea doar din planuri LIVE
    # noi - saptamani intregi in care agentul ar sta blocat in SHADOW desi are
    # 16.000+ de exemple. Reconstruiesc o singura data: reantrenez cronologic
    # (train_from_plans sorteaza dupa closed_ts), predictie INAINTE de invatare,
    # deci evaluarea ramane in afara esantionului.
    legacy_eval = (state is not None and state.get("source") == source
                   and (state.get("pairs") or [])
                   and not any(len(pp) >= 3 for pp in state.get("pairs") or []))
    if legacy_eval:
        print("[i] Evaluarea salvata nu contine R - o reconstruiesc cronologic, o singura data.")
        state["source"] = "legacy-eval-rebuild"

    if state is None or state.get("source") != source:
        if state is not None:
            print(f"[i] Resetez agentul: sursa de invatare s-a schimbat "
                  f"({state.get('source')} -> {source}).")
            # BUG FIX: la reset trebuie sterse si marcajele `agent_trained` de pe
            # planurile geometriei CURENTE. Fara asta, planurile deja marcate erau
            # sarite dupa reset, iar agentul repornea la zero si ramanea acolo -
            # nu mai avea din ce sa invete pana la urmatoarele planuri noi.
            cleared = 0
            for p in plans:
                if (plan_tracker.same_family(p.get("geometry", "v1"))
                        and p.pop("agent_trained", None)):
                    cleared += 1
            if cleared:
                print(f"[i] Am eliberat {cleared} planuri {plan_tracker.GEOMETRY_VERSION} "
                      f"pentru reinvatare.")
        state = _core.default_state()
        state["source"] = source
        state["feature_version"] = fv
    model = _core.OnlineLogisticRegression.from_dict(state.get("model", {}))

    if not plans:
        print("Niciun plan inca - agentul invata din planuri inchise. "
              "Ruleaza intai crypto_ai_scanner.py.")
        state["skew"], state["skew_excluded"], state["feature_mask"] = _skew, _excl, _mask
        save_json(MODEL_FILE, state)
        return

    new_samples = _core.train_from_plans(plans, model, state)
    # Prin plan_tracker.save_plans, nu save_json direct: aceeasi garda de
    # dimensiune si aceeasi compactare ca peste tot unde se scrie plans.json.
    # Nu adauga planuri noi aici, deci riscul e mai mic decat la merge, dar
    # consecventa conteaza - un singur punct de adevar pentru "cum se scrie
    # plans.json", nu patru variante care pot diverge.
    plan_tracker.save_plans(plans_store)

    state["model"] = model.to_dict()
    active, reason = _core.agent_is_active(state, plans)
    state["status"] = "ACTIVE" if active else "SHADOW"
    state["status_reason"] = reason
    state["days_covered"] = round(_core.days_covered(state, plans), 2)
    ba, used = _core.balanced_accuracy(state["by_direction"], "agent")
    bb, _ = _core.balanced_accuracy(state["by_direction"], "baseline")
    state["balanced_agent"], state["balanced_baseline"] = ba, bb
    state["balanced_directions"] = used
    pairs = state.get("pairs") or []
    state["auc"] = _core.auc_score(pairs)
    state["auc_score_baseline"] = _core.auc_score(state.get("score_pairs") or [])
    sa, sb = state["auc"], state["auc_score_baseline"]
    state["agent_superior"] = bool(sa is not None and sb is not None and sa > sb)
    state["majority_baseline"] = _core.majority_class_accuracy(pairs)
    state["predicted_positive_rate"] = _core.predicted_positive_rate(pairs)
    state["skew"], state["skew_excluded"], state["feature_mask"] = _skew, _excl, _mask
    save_json(MODEL_FILE, state)

    acc_agent, acc_base, acc_recent = _core.summarize(state)
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
        print(f"AUC: {auc:.3f} (0.5 = hazard, prag {_core.MIN_AUC}){extra} -> {verdict}")
    print(f"Status: {state['status']} - {reason}")
    print("Greutati invatate:", json.dumps(state["model"]["weights"]))


# Porturile de citire ale nucleului, legate la fisierele de mai sus.
_core.QUARANTINE_SOURCE = lambda: _quarantined()
_core.EXCLUDED_SOURCE = lambda: excluded_features()

compat.bind(__name__, _core)


if __name__ == "__main__":
    main()
