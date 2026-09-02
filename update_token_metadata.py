#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_token_metadata.py
==========================
Construieste o "baza de cunostinte" despre proiectele scanate: categorii
CoinGecko, apartenenta la portofoliul Binance Labs (redenumit intre timp
YZi Labs), si un scor de similaritate intre proiecte prin suprapunere de
categorii. NU antreneaza nicio retea neuronala - e similaritate clasica,
suficienta ca sa raspunda la "ce alte proiecte seamana cu X".

Ruleaza dupa crypto_ai_scanner.py:
    python3 crypto_ai_scanner.py && python3 update_token_metadata.py

Se actualizeaza o data pe saptamana (categoriile nu se schimba des) -
restul rularilor ies instant, ca sa nu iroseasca din cota CoinGecko
gratuita. Foloseste `--force` ca argument pentru actualizare imediata.

OPTIONAL - raspuns narativ scris de un LLM real:
    Seteaza GEMINI_API_KEY (gratuit, fara card - aistudio.google.com/apikey,
    Google AI Studio, tier-ul Free) si scriptul cere modelului Gemini 2-3
    propozitii despre cel mai bun candidat curent + proiectele similare.
    Nu antrenezi nimic - doar apelezi un model deja antrenat de Google,
    gratuit in limitele lor (in jur de 1500 cereri/zi pe modelele Flash,
    verifica ai.google.dev/gemini-api/docs/models daca s-a schimbat).

OPTIONAL - CoinGecko Demo API key (gratuit, fara card):
    Fara cheie, CoinGecko permite doar 5-15 apeluri/minut (mergem oricum
    foarte lent ca sa ne incadram). Cu o cheie Demo gratuita (de la
    coingecko.com/en/api/pricing, buton "Demo"), urci la 100/minut.
    Seteaza COINGECKO_API_KEY daca vrei asta.
"""

import json
import os
import sys
import time
import urllib.request

DATA_DIR = "data"
METADATA_FILE = os.path.join(DATA_DIR, "token_metadata.json")
HISTORY_FILE = os.path.join(DATA_DIR, "scan_history.json")
LABS_SEED_FILE = os.path.join(DATA_DIR, "binance_labs_seed.json")

REFRESH_DAYS = 7
COINGECKO_BASE = "https://api.coingecko.com/api/v3"
COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.5-flash"  # verifica ai.google.dev/gemini-api/docs/models


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def http_get_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def coingecko_headers():
    return {"x-cg-demo-api-key": COINGECKO_API_KEY} if COINGECKO_API_KEY else {}


def current_universe():
    """Refolosesc simbolurile din ultima scanare deja salvata de
    crypto_ai_scanner.py, ca sa nu mai interoghez inca o data exchange-ul."""
    history = load_json(HISTORY_FILE, [])
    if not history:
        return []
    return sorted({r["symbol"] for r in history[-1].get("results", [])})


def find_coingecko_id(symbol_base, coins_list):
    matches = [c for c in coins_list if c["symbol"].lower() == symbol_base.lower()]
    return matches[0]["id"] if matches else None


def fetch_token_info(coingecko_id):
    url = (f"{COINGECKO_BASE}/coins/{coingecko_id}"
           f"?localization=false&tickers=false&market_data=false"
           f"&community_data=false&developer_data=false")
    try:
        data = http_get_json(url, coingecko_headers())
        return {
            "categories": [c for c in (data.get("categories") or []) if c],
            "market_cap_rank": data.get("market_cap_rank"),
            "genesis_date": data.get("genesis_date"),
        }
    except Exception as e:
        print(f"[!] CoinGecko {coingecko_id}: {e}")
        return None


def jaccard(set_a, set_b):
    a, b = set(set_a), set(set_b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def compute_similarity(tokens, labs_tickers):
    symbols = list(tokens.keys())
    for sym in symbols:
        cats_a = tokens[sym].get("categories", [])
        is_labs_a = sym.split("/")[0] in labs_tickers
        scores = []
        for other in symbols:
            if other == sym:
                continue
            score = jaccard(cats_a, tokens[other].get("categories", []))
            if is_labs_a and other.split("/")[0] in labs_tickers:
                score = min(score + 0.25, 1.0)
            if score > 0:
                scores.append([other, round(score, 3)])
        scores.sort(key=lambda x: x[1], reverse=True)
        tokens[sym]["similar"] = scores[:3]
    return tokens


def call_gemini(prompt):
    if not GEMINI_API_KEY:
        return None
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}")
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print(f"[!] Gemini ({GEMINI_MODEL}): {e}")
        return None


def main():
    meta = load_json(METADATA_FILE, {})
    force = "--force" in sys.argv

    if not force and time.time() - meta.get("_updated_ts", 0) < REFRESH_DAYS * 86400:
        print("Metadata e proaspata (< 7 zile) - sar peste actualizare. "
              "Foloseste --force pentru actualizare imediata.")
        return

    universe = current_universe()
    if not universe:
        print("Nu exista inca nicio scanare - ruleaza intai crypto_ai_scanner.py.")
        return

    labs = load_json(LABS_SEED_FILE, {"tickers": []})
    labs_tickers = set(labs.get("tickers", []))

    delay = 0.7 if COINGECKO_API_KEY else 12  # respecta limita gratuita CoinGecko
    print(f"Actualizez metadata pentru {len(universe)} simboluri "
          f"(delay {delay}s intre apeluri, {'cu' if COINGECKO_API_KEY else 'fara'} cheie Demo)...")

    try:
        coins_list = http_get_json(f"{COINGECKO_BASE}/coins/list", coingecko_headers())
    except Exception as e:
        # NU las asta sa omoare workflow-ul: daca pasul iese cu cod != 0,
        # GitHub Actions opreste job-ul si nu mai ajunge la pasul de commit,
        # deci s-ar pierde toata scanarea. Metadata e optionala; scanarea nu.
        print(f"[!] CoinGecko indisponibil ({e}) - sar peste actualizarea metadata "
              f"in aceasta rulare. Se reincearca la urmatoarea.")
        return

    tokens = {}
    for sym in universe:
        base = sym.split("/")[0]
        cg_id = find_coingecko_id(base, coins_list)
        if cg_id:
            info = fetch_token_info(cg_id)
            if info:
                info["coingecko_id"] = cg_id
                info["binance_labs"] = base in labs_tickers
                tokens[sym] = info
        time.sleep(delay)

    tokens = compute_similarity(tokens, labs_tickers)
    result = {
        "_updated_ts": time.time(),
        "_updated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "tokens": tokens,
    }

    history = load_json(HISTORY_FILE, [])
    best = history[-1].get("best_candidate") if history else None
    if best and best["symbol"] in tokens and GEMINI_API_KEY:
        sym = best["symbol"]
        info = tokens[sym]
        similar_names = ", ".join(s for s, _ in info.get("similar", [])) or "niciunul gasit"
        prompt = (
            f"Esti un analist crypto. Token: {sym}. Categorii CoinGecko: "
            f"{', '.join(info.get('categories', [])[:5]) or 'necunoscute'}. "
            f"In portofoliul Binance Labs: {info.get('binance_labs', False)}. "
            f"Proiecte similare gasite in universul scanat (dupa categorie): {similar_names}. "
            f"Scrie 2-3 propozitii, in romana, despre ce tip de proiect e "
            f"si de ce ar putea avea potential similar cu cele enumerate. "
            f"Fii factual si precaut, nu da sfaturi de investitie."
        )
        narrative = call_gemini(prompt)
        if narrative:
            result["narrative"] = {"symbol": sym, "text": narrative}

    save_json(METADATA_FILE, result)
    print(f"Salvat: {METADATA_FILE} ({len(tokens)} simboluri cu metadata)")


if __name__ == "__main__":
    main()
