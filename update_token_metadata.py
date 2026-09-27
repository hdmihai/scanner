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
import urllib.error
import urllib.request

DATA_DIR = "data"
METADATA_FILE = os.path.join(DATA_DIR, "token_metadata.json")
HISTORY_FILE = os.path.join(DATA_DIR, "scan_history.json")
# BUG TACUT, gasit uitandu-ma direct in repo: fisierul e la RADACINA, dar codul
# il cauta in data/. `load_json` cu implicit gol nu arunca eroare, deci flagul
# Binance Labs nu s-a activat niciodata si nimic nu a semnalat-o. Caut acum in
# ambele locuri si spun explicit daca nu il gasesc.
LABS_SEED_CANDIDATES = [
    os.path.join(DATA_DIR, "binance_labs_seed.json"),
    "binance_labs_seed.json",
]


def find_labs_seed():
    for path in LABS_SEED_CANDIDATES:
        if os.path.exists(path):
            return path
    return None

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


def http_get_json(url, headers=None, retries=2):
    """GET JSON, cu reincercare la 429 (limita de rata CoinGecko). Fara ea, un
    singur 429 facea tokenul sa dispara din metadata pana la urmatoarea rulare."""
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:
                wait = 20 * (attempt + 1)
                print(f"    limita de rata CoinGecko - astept {wait}s si reincerc")
                time.sleep(wait)
                continue
            raise


def coingecko_headers():
    return {"x-cg-demo-api-key": COINGECKO_API_KEY} if COINGECKO_API_KEY else {}


def current_universe():
    """Refolosesc simbolurile din ultima scanare deja salvata de
    crypto_ai_scanner.py, ca sa nu mai interoghez inca o data exchange-ul."""
    history = load_json(HISTORY_FILE, [])
    if not history:
        return []
    return sorted({r["symbol"] for r in history[-1].get("results", [])})


def resolve_ids(bases, delay):
    """Simbol -> ID CoinGecko, alegand proiectul cu CEA MAI MARE capitalizare.

    DEFECT REPARAT: varianta anterioara lua PRIMA potrivire din /coins/list,
    ordonata alfabetic dupa ID. Pentru tickere comune asta alegea proiectul
    gresit - masurat: ADA -> "ada-the-dog" (o memecoin de pe Solana, rang 6128)
    in loc de Cardano, iar "proiectele similare" se calculau din categoriile ei
    ("Meme", "Dog-Themed").

    1) topul dupa capitalizare (/coins/markets, primele 500): prima aparitie a
       unui simbol e proiectul cu capitalizarea cea mai mare;
    2) pentru ce lipseste, /search: potrivire exacta de simbol cu rangul minim.
    """
    want = {b.lower() for b in bases}
    found = {}
    for page in (1, 2):
        try:
            rows = http_get_json(f"{COINGECKO_BASE}/coins/markets?vs_currency=usd"
                                 f"&order=market_cap_desc&per_page=250&page={page}",
                                 coingecko_headers())
        except Exception as e:
            print(f"[!] /coins/markets pagina {page}: {e}")
            break
        for c in rows or []:
            sym = (c.get("symbol") or "").lower()
            if sym in want and sym not in found:
                found[sym] = (c["id"], "market_cap")
        time.sleep(delay)
    for b in sorted(want - set(found)):
        try:
            res = http_get_json(f"{COINGECKO_BASE}/search?query={b}", coingecko_headers())
        except Exception as e:
            print(f"[!] /search {b}: {e}")
            continue
        cands = [c for c in (res.get("coins") or [])
                 if (c.get("symbol") or "").lower() == b and c.get("market_cap_rank")]
        if cands:
            best = min(cands, key=lambda c: c["market_cap_rank"])
            found[b] = (best["id"], "search")
        time.sleep(delay)
    return found


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

    universe = current_universe()
    if not universe:
        print("Nu exista inca nicio scanare - ruleaza intai crypto_ai_scanner.py.")
        return

    # CAND ACTUALIZEZ. Doar vechimea (7 zile) nu ajungea: tokenii adaugati in
    # watchlist dupa ultima actualizare lipseau o saptamana intreaga - masurat,
    # 10 din 30, inclusiv candidatul principal. Actualizez si cand lipsesc
    # tokeni, sau cand exista intrari rezolvate cu metoda veche (gresita).
    old_tokens = meta.get("tokens") or {}
    missing = [t for t in universe if t not in old_tokens]
    legacy = [t for t, v in old_tokens.items() if not v.get("resolved_by")]
    stale = time.time() - meta.get("_updated_ts", 0) >= REFRESH_DAYS * 86400
    if not (force or stale or missing or legacy):
        print("Metadata e completa si proaspata - nimic de actualizat. "
              "Foloseste --force pentru actualizare imediata.")
        return
    print(f"Actualizez: fortat={force}, vechi={stale}, lipsa={len(missing)}, "
          f"rezolvari vechi={len(legacy)}")

    seed_path = find_labs_seed()
    if seed_path is None:
        print(f"[!] binance_labs_seed.json negasit in {LABS_SEED_CANDIDATES} - "
              f"flagul Binance Labs va fi False pentru toate proiectele.")
        labs_tickers = set()
    else:
        labs = load_json(seed_path, {"tickers": []})
        labs_tickers = set(labs.get("tickers", []))
        print(f"Binance Labs: {len(labs_tickers)} tickere incarcate din {seed_path}")

    delay = 0.7 if COINGECKO_API_KEY else 12  # respecta limita gratuita CoinGecko
    print(f"Actualizez metadata pentru {len(universe)} simboluri "
          f"(delay {delay}s intre apeluri, {'cu' if COINGECKO_API_KEY else 'fara'} cheie Demo)...")

    ids = resolve_ids([sym.split("/")[0] for sym in universe], delay)
    print(f"ID-uri rezolvate: {len(ids)} din {len(universe)}")
    if not ids:
        # FARA RASPUNS DE LA API, NU SALVEZ NIMIC. Varianta initiala scria totusi
        # fisierul: arunca intrarile existente si punea o data proaspata - o pana
        # CoinGecko ar fi sters toata metadata (prins la simulare, cu HTTP 403).
        print("[!] CoinGecko nu a raspuns - pastrez metadata existenta neschimbata. "
              "Se reincearca la urmatoarea rulare.")
        return

    # IMBINARE peste datele existente: un token al carui apel esueaza acum isi
    # pastreaza informatia anterioara. O intrare veche se elimina DOAR cand e
    # dovedita gresita - API-ul a rezolvat simbolul la alt ID.
    tokens = {t: v for t, v in old_tokens.items() if t in universe}
    changed = False
    for sym in universe:
        base = sym.split("/")[0]
        hit = ids.get(base.lower())
        if not hit:
            print(f"  {sym}: niciun proiect CoinGecko gasit")
            continue
        cg_id, how = hit
        if (not force and not stale and sym in tokens and tokens[sym].get("resolved_by")
                and tokens[sym].get("coingecko_id") == cg_id):
            continue                      # deja corect si proaspat
        info = fetch_token_info(cg_id)
        if not info and sym in tokens and tokens[sym].get("coingecko_id") != cg_id:
            del tokens[sym]               # maparea veche e dovedit gresita
            changed = True
            print(f"  {sym}: maparea veche ({old_tokens[sym].get('coingecko_id')}) era "
                  f"gresita; o elimin, se completeaza la urmatoarea rulare")
        if info:
            changed = True
            info["coingecko_id"] = cg_id
            info["resolved_by"] = how
            info["binance_labs"] = base in labs_tickers
            tokens[sym] = info
            print(f"  {sym} -> {cg_id} (rang {info.get('market_cap_rank')}, prin {how})")
        time.sleep(delay)

    if not changed:
        print("Nimic nou obtinut de la CoinGecko - metadata ramane neschimbata.")
        return
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
