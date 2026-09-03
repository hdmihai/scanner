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
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ========================= FAPTELE (doar numere reale) ======================

def gather_facts():
    plans_store = load_json(PLANS_FILE, {})
    agent = load_json(AGENT_FILE, {})
    history = load_json(HISTORY_FILE, [])

    plans = plans_store.get("plans", [])
    summary = plans_store.get("summary") or {}
    calibration = plans_store.get("calibration") or {}
    closed = [p for p in plans if p.get("realized_r") is not None]
    open_plans = [p for p in plans if p.get("realized_r") is None]

    # ce s-a inchis recent (ultimele 5, dupa momentul inchiderii)
    recent_closed = sorted(closed, key=lambda p: p.get("closed_ts") or 0, reverse=True)[:5]

    # cel mai bun si cel mai slab interval de scor, dintre cele fiabile
    reliable = {b: c for b, c in calibration.items() if c.get("reliable")}
    best_bucket = max(reliable.items(), key=lambda kv: kv[1]["avg_r"], default=None)
    worst_bucket = min(reliable.items(), key=lambda kv: kv[1]["avg_r"], default=None)

    last_scan = history[-1] if history else {}

    return {
        "scan_time": last_scan.get("scan_time"),
        "universe_size": last_scan.get("universe_size"),
        "plans_total": len(plans),
        "plans_open": len(open_plans),
        "plans_closed": len(closed),
        "win_rate": summary.get("win_rate"),
        "total_r": summary.get("total_r"),
        "avg_r": summary.get("avg_r"),
        "profit_factor": summary.get("profit_factor"),
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
        pf = f"{f['profit_factor']}" if f["profit_factor"] is not None else "inca nedefinit"
        parts.append(
            f"Din {f['plans_closed']} planuri inchise, rata de succes e {f['win_rate']}%, "
            f"cu {f['total_r']:+.2f}R cumulat ({f['avg_r']:+.3f}R in medie pe plan) "
            f"si profit factor {pf}. {f['plans_open']} planuri sunt inca deschise.")

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
        parts.append(
            f"Agentul cu invatare online e ACTIV si contribuie la decizii: acuratete "
            f"echilibrata {f['agent_balanced']:.1f}% fata de {f['baseline_balanced']:.1f}% "
            f"baseline, pe {f['agent_samples']} planuri invatate.")
    else:
        parts.append(
            f"Agentul cu invatare online e in modul SHADOW - invata si isi masoara "
            f"performanta, dar nu influenteaza inca deciziile ({f['agent_reason']}).")

    return " ".join(parts)


# ========================= VARIANTA CU GEMINI ==============================

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
        print(f"[!] Gemini indisponibil ({e}) - folosesc briefing-ul determinist.")
        return None


def main():
    facts = gather_facts()
    text = gemini_briefing(facts)
    source = "gemini" if text else "determinist"
    if not text:
        text = deterministic_briefing(facts)

    save_json(BRIEFING_FILE, {"text": text, "source": source, "facts": facts})
    print(f"Briefing ({source}):\n{text}")


if __name__ == "__main__":
    main()
