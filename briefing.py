#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
briefing.py
============
Briefing narativ: ce a facut agentul, ce a invatat, si de ce a decis ce a decis.

DOUA MODURI
-----------
1. FARA cheie API (implicit): genereaza un briefing DETERMINIST din statistici.
   Nu e "AI", e un text construit din numere reale - dar e mereu corect, mereu
   disponibil, si nu inventeaza nimic. Merita sa fie modul implicit.
2. CU GEMINI_API_KEY: acelasi set de numere e trimis unui model care le scrie
   mai natural. Gratuit, fara card (aistudio.google.com/apikey).

DE CE FAPTELE SE CONSTRUIESC INTAI, IN PYTHON
---------------------------------------------
Toate cifrele din briefing sunt calculate aici, din plans.json si
agent_model.json, si abia apoi date modelului. Modelului i se cere explicit sa
NU adauge cifre proprii si sa NU dea sfaturi de investitie. Un LLM lasat sa
"analizeze piata" liber ar produce numere plauzibile si false - exact tipul de
"86% confirmed" nemasurat pe care l-am evitat in restul proiectului.
"""

import json
import os
import urllib.request

DATA_DIR = "data"
PLANS_FILE = os.path.join(DATA_DIR, "plans.json")
AGENT_FILE = os.path.join(DATA_DIR, "agent_model.json")
HISTORY_FILE = os.path.join(DATA_DIR, "scan_history.json")
BRIEFING_FILE = os.path.join(DATA_DIR, "briefing.json")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.5-flash"


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


# ========================= FAPTELE (doar numere reale) ======================

def gather_facts():
    plans_store = load_json(PLANS_FILE, {})
    agent = load_json(AGENT_FILE, {})
    history = load_json(HISTORY_FILE, [])

    plans = plans_store.get("plans", [])
    summary = plans_store.get("summary") or {}
    calibration = plans_store.get("calibration") or {}
    current_geo = summary.get("geometry", "v2")

    # Numar la fel ca plan_tracker.summarize: doar geometria curenta.
    # Fara asta apare o inconsistenta - `plans_closed` ar include planuri vechi
    # in timp ce `avg_r` (din summary) le exclude, si briefing-ul ar raporta
    # cifre care nu se potrivesc intre ele.
    # FARA planurile NO_ENTRY (pretul nu a revenit la intrare, R = 0): rata de
    # castig si R-ul mediu din summary le exclud, deci si numaratoarea trebuie.
    # Altfel briefing-ul spunea "din 21.488 planuri, +0.087R in medie" desi
    # 1409R / 21488 = 0.066 - numitori diferiti in aceeasi propozitie.
    all_closed = [p for p in plans if p.get("realized_r") is not None
                  and p.get("state") != "NO_ENTRY"]
    # FAMILIA de geometrie (versiune + timeframe), ca in plan_tracker.summarize.
    # Comparatia exacta a semnaturii raporta pe dashboard ca "geometrie
    # anterioara, nu intra in calibrare" 21.458 de planuri care INTRA in
    # calibrare - cardul de calibrare de pe aceeasi pagina le folosea.
    fam = "-".join(str(current_geo).split("-")[:2])
    same = lambda g: "-".join(str(g or "v1").split("-")[:2]) == fam
    closed = [p for p in all_closed if same(p.get("geometry", "v1"))]
    open_plans = [p for p in plans if p.get("realized_r") is None
                  and not p.get("migrated")]
    legacy_closed = [p for p in all_closed if not same(p.get("geometry", "v1"))]

    # ce s-a inchis recent (ultimele 5, dupa momentul inchiderii)
    recent_closed = sorted(closed, key=lambda p: p.get("closed_ts") or 0, reverse=True)[:5]

    # cel mai bun si cel mai slab interval de scor, dintre cele fiabile
    reliable = {b: c for b, c in calibration.items() if c.get("reliable")}
    best_bucket = max(reliable.items(), key=lambda kv: kv[1]["avg_r"], default=None)
    worst_bucket = min(reliable.items(), key=lambda kv: kv[1]["avg_r"], default=None)

    last_scan = history[-1] if history else {}

    # DATE REALE vs ISTORIC SIMULAT, separat: doar planurile live confirma sau infirma agentul pe
    # piata reala; backtest-ul il pre-antreneaza. In acelasi paragraf, cifrele backtest-ului
    # (16.000+ planuri) acopereau complet rezultatul live.
    def _stats(ps):
        rs = [p["realized_r"] for p in ps]
        if not rs:
            return None
        wins = [r for r in rs if r > 0]
        loss = [-r for r in rs if r <= 0]
        return {"n": len(rs), "win_rate": round(100 * len(wins) / len(rs), 1),
                "avg_r": round(sum(rs) / len(rs), 3), "total_r": round(sum(rs), 2),
                "profit_factor": round(sum(wins) / sum(loss), 2) if loss and sum(loss) > 0 else None}
    live_closed = [p for p in closed if p.get("source") != "backtest"]
    bt_closed = [p for p in closed if p.get("source") == "backtest"]
    live_open = [p for p in open_plans if p.get("source") != "backtest"]
    live_recent = sorted(live_closed, key=lambda p: p.get("closed_ts") or 0, reverse=True)[:3]
    real = {
        "all": _stats(live_closed),
        "by_direction": {d: _stats([p for p in live_closed if p.get("direction") == d]) for d in ("LONG", "SHORT")},
        "no_entry": sum(1 for p in plans if p.get("source") != "backtest" and p.get("state") == "NO_ENTRY"
                        and same(p.get("geometry", "v1"))),
        "open": len(live_open),
        "recent": [{"id": p["id"], "symbol": p["symbol"], "direction": p["direction"], "r": p["realized_r"]}
                   for p in live_recent],
        "divergence": summary.get("divergence"),
        "agent_live": agent.get("live"),
    }
    simulated = {
        "all": _stats(bt_closed),
        "since": (summary.get("backtest") or {}).get("since"),
        "edge_recent": (summary.get("edge") or {}).get("recent"),
        "edge_status": (summary.get("edge") or {}).get("status"),
        "rebuilt": plans_store.get("rebuilt"),
    }

    return {
        "scan_time": last_scan.get("scan_time"),
        "universe_size": last_scan.get("universe_size"),
        "plans_total": len(plans),
        "plans_open": len(open_plans),
        "plans_closed": len(closed),
        "legacy_closed": len(legacy_closed),
        "legacy_total_r": round(sum(p["realized_r"] for p in legacy_closed), 2) if legacy_closed else None,
        "win_rate": summary.get("win_rate"),
        "total_r": summary.get("total_r"),
        "avg_r": summary.get("avg_r"),
        "profit_factor": summary.get("profit_factor"),
        "live": summary.get("live") or {},
        "backtest": summary.get("backtest") or {},
        "recent_closed": [
            {"id": p["id"], "symbol": p["symbol"], "direction": p["direction"],
             "state": p.get("state_detail"), "r": p["realized_r"]}
            for p in recent_closed
        ],
        "open_now": [
            {"id": p["id"], "symbol": p["symbol"], "direction": p["direction"],
             "state": p.get("state_detail")}
            for p in sorted(open_plans, key=lambda x: x["id"], reverse=True)[:5]
        ],
        "best_bucket": ({"range": f"{best_bucket[0]}-{int(best_bucket[0])+19}",
                         **best_bucket[1]} if best_bucket else None),
        "worst_bucket": ({"range": f"{worst_bucket[0]}-{int(worst_bucket[0])+19}",
                          **worst_bucket[1]} if worst_bucket else None),
        "agent_status": agent.get("status"),
        "agent_reason": agent.get("status_reason"),
        "agent_samples": (agent.get("agent") or {}).get("total", 0),
        "agent_balanced": agent.get("balanced_agent"),
        "baseline_balanced": agent.get("balanced_baseline"),
        "agent_weights": (agent.get("model") or {}).get("weights"),
        "real": real,
        "simulated": simulated,
    }


# ===================== BRIEFING DETERMINIST (implicit) ======================

def deterministic_briefing(f):
    """Text construit din numere, fara model de limbaj. Mereu disponibil."""
    parts = []

    if not f["plans_total"]:
        return ("Niciun plan deschis inca. Agentul deschide planuri la primele "
                "semnale si incepe sa invete dupa ce acestea se inchid.")

    if f["plans_closed"] == 0:
        parts.append(
            f"{f['plans_open']} planuri sunt deschise, niciunul inchis inca. "
            f"Pana la primele inchideri nu pot spune nimic despre performanta - "
            f"orice cifra ar fi speculatie.")
    else:
        pf = f"{f['profit_factor']}" if f.get("profit_factor") is not None else "inca nedefinit"
        wr = f"{f['win_rate']}%" if f.get("win_rate") is not None else "necalculata"
        tr = f"{f['total_r']:+.2f}R" if f.get("total_r") is not None else "necalculat"
        ar = f"{f['avg_r']:+.3f}R" if f.get("avg_r") is not None else "necalculat"
        parts.append(
            f"Din {f['plans_closed']} planuri inchise, rata de succes e {wr}, "
            f"cu {tr} cumulat ({ar} in medie pe plan) "
            f"si profit factor {pf}. {f['plans_open']} planuri sunt inca deschise.")
        # Totalul de mai sus amesteca live si backtest. Spun explicit cat e live -
        # altfel cifrele backtest-ului par rezultatul sistemului in piata reala.
        lv, bt = f.get("live") or {}, f.get("backtest") or {}
        if bt.get("closed"):
            if lv.get("closed"):
                ci = (f" (IC95 {lv['avg_r_ci_low']:+.3f}..{lv['avg_r_ci_high']:+.3f}R)"
                      if lv.get("avg_r_ci_low") is not None else "")
                parts.append(
                    f"Atentie: {bt['closed']} dintre ele sunt din backtest. LIVE"
                    + (f", din {lv['since']}" if lv.get("since") else "")
                    + f": {lv['closed']} planuri inchise, castig {lv['win_rate']}% "
                    f"(IC {lv['wr_ci_low']}-{lv['wr_ci_high']}%), R mediu {lv['avg_r']:+.3f}R{ci}, "
                    f"total {lv['total_r']:+.2f}R.")
            else:
                parts.append(f"Atentie: toate cele {bt['closed']} sunt din backtest; "
                             f"niciun plan live inchis inca.")

    if f.get("legacy_closed"):
        parts.append(
            f"Separat, {f['legacy_closed']} planuri inchise ({f['legacy_total_r']:+.2f}R) provin "
            f"dintr-o geometrie anterioara si nu intra in calibrare - regulile de plasare a "
            f"tintelor s-au schimbat, deci rezultatele lor nu sunt comparabile.")

    if f["recent_closed"]:
        items = ", ".join(f"#{p['id']} {p['symbol']} {p['r']:+.2f}R" for p in f["recent_closed"][:3])
        parts.append(f"Ultimele inchise: {items}.")

    if f["best_bucket"] and f["worst_bucket"] and f["best_bucket"]["range"] != f["worst_bucket"]["range"]:
        b, w = f["best_bucket"], f["worst_bucket"]
        parts.append(
            f"Pe intervalele de scor cu destule date, cel mai bine merge {b['range']} "
            f"({b['win_rate']}%, {b['avg_r']:+.2f}R mediu, n={b['total']}), "
            f"iar cel mai slab {w['range']} ({w['win_rate']}%, {w['avg_r']:+.2f}R, n={w['total']}). "
            f"Deciziile de a deschide sau refuza planuri se bazeaza pe aceste cifre masurate, "
            f"nu pe scorul brut.")
    else:
        parts.append(
            "Inca nu am destule planuri inchise pe niciun interval de scor ca sa pronunt "
            "o probabilitate calibrata, deci deschid planuri in mod explorativ, ca sa strang date.")

    if f["agent_status"] == "ACTIVE":
        # Motivul vine din ai_agent.agent_is_active (AUC vs scor, R-ul treimii de
        # sus, acuratete vs clasa majoritara). "Acuratete echilibrata vs baseline"
        # compara cu o formula care prezice mereu castig - nu dovedea nimic.
        parts.append(
            f"Agentul cu invatare online e ACTIV si contribuie la decizii ({f['agent_reason']}), "
            f"pe {f['agent_samples']} planuri invatate.")
    else:
        parts.append(
            f"Agentul cu invatare online e in modul SHADOW - invata si isi masoara "
            f"performanta, dar nu influenteaza inca deciziile ({f['agent_reason']}).")

    return " ".join(parts)


def _r(v, nd=3):
    return "n/d" if v is None else f"{v:+.{nd}f}R"


def sections(f):
    """Briefing-ul in doua sectiuni: DATE REALE (piata reala - pe ele se confirma agentul) si
    ISTORIC SIMULAT (backtest - pre-antrenare, calibrare, cercetare). Fiecare e o lista de
    propozitii construite doar din cifre masurate."""
    real, sim = f.get("real") or {}, f.get("simulated") or {}
    lv, lsum = real.get("all"), f.get("live") or {}
    r = []
    if lv:
        ci = (f" (IC95 {lsum['avg_r_ci_low']:+.3f}..{lsum['avg_r_ci_high']:+.3f}R)"
              if lsum.get("avg_r_ci_low") is not None else "")
        r.append(f"Pe piata reala{(' din ' + lsum['since']) if lsum.get('since') else ''}: {lv['n']} planuri "
                 f"inchise, castig {lv['win_rate']}%, R mediu {_r(lv['avg_r'])}{ci}, total {lv['total_r']:+.2f}R"
                 + (f", profit factor {lv['profit_factor']}" if lv.get("profit_factor") is not None else "") + ".")
        dirs = [f"{d} {s_['n']} planuri {_r(s_['avg_r'])}" for d, s_ in (real.get("by_direction") or {}).items() if s_]
        if dirs:
            r.append("Pe directii: " + "; ".join(dirs) + ".")
        dv = real.get("divergence") or {}
        vs = dv.get("vs_all") or {}
        verd = {"sub_backtest": "SEMNIFICATIV sub simulare", "in_marja": "in marja statistica",
                "peste_backtest": "peste simulare"}
        parts = []
        if dv.get("basis") == "aceeasi_perioada" and dv.get("diff") is not None:
            w = dv.get("window") or ["?", "?"]
            parts.append(f"pe aceeasi perioada ({w[0]} - {w[1]}, {dv.get('n_bt_window')} planuri simulate) "
                         f"{dv['diff']:+.3f}R/plan (IC95 {dv['ci_low']:+.3f}..{dv['ci_high']:+.3f}), "
                         + verd.get(dv.get("status"), dv.get("status") or ""))
        if vs.get("diff") is not None:
            parts.append(f"fata de tot istoricul simulat {vs['diff']:+.3f}R/plan (IC95 {vs['ci_low']:+.3f}.."
                         f"{vs['ci_high']:+.3f}), " + verd.get(vs.get("status"), vs.get("status") or ""))
        if parts:
            r.append("Real vs simulat: " + "; ".join(parts) + ".")
    else:
        r.append("Niciun plan live inchis inca - nu exista inca nicio masuratoare pe piata reala.")
    al = real.get("agent_live") or {}
    if al.get("n") is not None:
        r.append(f"Agentul pe date reale: " + (f"AUC {al['auc']:.3f} (IC {al.get('ci_low')}-{al.get('ci_high')}) "
                                               if al.get("auc") is not None else "")
                 + f"pe {al['n']}/100 planuri necesare confirmarii.")
    if real.get("recent"):
        r.append("Ultimele inchise live: " + ", ".join(f"#{p['id']} {p['symbol']} {p['r']:+.2f}R"
                                                       for p in real["recent"]) + ".")
    r.append(f"Deschise acum: {real.get('open', 0)}; anulate fara intrare (pretul nu a revenit): "
             f"{real.get('no_entry', 0)}.")

    s_ = []
    bt = sim.get("all")
    if bt:
        rb = sim.get("rebuilt") or {}
        s_.append(f"Backtest {sim.get('since') or ''}-azi" + (f" (regenerat {rb['when']})" if rb.get("when") else "")
                  + f": {bt['n']} planuri inchise, castig {bt['win_rate']}%, R mediu {_r(bt['avg_r'])}, "
                  f"total {bt['total_r']:+.2f}R" + (f", profit factor {bt['profit_factor']}" if bt.get("profit_factor") else "") + ".")
        er = sim.get("edge_recent") or {}
        if er.get("r") is not None:
            s_.append(f"Ultimele 12 luni simulate: {er['r']:+.3f}R/plan (IC95 {er['ci_low']:+.3f}..{er['ci_high']:+.3f}) pe "
                      f"{er['n']} planuri - " + {"pozitiv": "distinct pozitiv", "neconcludent": "nu e distinct de zero",
                                                 "negativ": "negativ"}.get(sim.get("edge_status"), "") + ".")
        b, w = f.get("best_bucket"), f.get("worst_bucket")
        if b and w and b["range"] != w["range"]:
            s_.append(f"Calibrare (dominata de backtest): cel mai bun interval de scor {b['range']} ({b['win_rate']}%, "
                      f"{b['avg_r']:+.2f}R), cel mai slab {w['range']} ({w['win_rate']}%, {w['avg_r']:+.2f}R).")
        s_.append("Rol: pre-antrenarea agentului, calibrarea si cercetarea regulilor - nu e rezultat pe piata reala.")
    else:
        s_.append("Niciun backtest integrat - agentul invata doar din planurile live.")

    if f.get("agent_status") == "ACTIVE":
        ag = f"Agentul e ACTIV ({f.get('agent_reason')})."
    else:
        ag = f"Agentul e in SHADOW: invata, dar nu filtreaza ({f.get('agent_reason')})."
    return {"real": r, "simulated": s_, "agent": ag}


# ========================= VARIANTA CU GEMINI ==============================

LLM_ERROR = None      # cauza ultimului esec Gemini, raportata de diagnostic


def gemini_briefing(f):
    if not GEMINI_API_KEY:
        return None
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}")
    prompt = (
        "Esti analistul unui sistem automat de scanare crypto. Scrie un briefing "
        "de 4-6 propozitii, in limba romana, pentru operatorul sistemului.\n\n"
        "REGULI STRICTE:\n"
        "- Foloseste DOAR cifrele din datele de mai jos. Nu inventa niciun numar.\n"
        "- Nu da sfaturi de investitie si nu face predictii de pret.\n"
        "- Daca datele sunt putine, spune clar ca sunt putine si ce inseamna asta.\n"
        "- Ton factual si direct, fara entuziasm de marketing.\n"
        "- Explica ce a invatat sistemul si de ce decide cum decide.\n\n"
        f"DATE:\n{json.dumps(f, indent=2, ensure_ascii=False)}"
    )
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        # CAUZA EXACTA, salvata in briefing.json (fara cheie): in productie
        # briefing-ul cadea tacut pe varianta determinista, iar motivul aparea
        # doar in jurnalul Actions. Acum il raporteaza modulul selfrepair.
        global LLM_ERROR
        detail = str(e)
        try:
            detail += " " + e.read().decode()[:160]
        except Exception:
            pass
        import re as _re
        LLM_ERROR = _re.sub(r"key=[A-Za-z0-9_\-]+", "key=***", detail)[:240].strip()
        print(f"[!] Gemini indisponibil ({LLM_ERROR}) - folosesc briefing-ul determinist.")
        return None


def main():
    """Briefing-ul e informativ, nu critic. Daca ceva crapa aici, NU are voie
    sa opreasca workflow-ul: pasii de dupa (dashboard, email, salvare) sunt mai
    importanti decat un paragraf de text."""
    try:
        facts = gather_facts()
        text = gemini_briefing(facts)
        source = "gemini" if text else "determinist"
        if not text:
            text = deterministic_briefing(facts)
        out = {"text": text, "source": source, "facts": facts, "sections": sections(facts)}
        if source == "determinist":
            out["llm_error"] = LLM_ERROR if GEMINI_API_KEY else "GEMINI_API_KEY lipseste"
        save_json(BRIEFING_FILE, out)
        print(f"Briefing ({source}):\n{text}")
    except Exception as e:
        print(f"[!] Briefing esuat ({type(e).__name__}: {e}) - continui fara el.")


if __name__ == "__main__":
    main()
