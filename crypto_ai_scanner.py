#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
crypto_ai_scanner.py - punctul de intrare al scanarii: radacina compozitiei si fatada nucleului.

ARHITECTURA HEXAGONALA (Etapa 2)
--------------------------------
  core/      nucleul: agentul AI, planurile, scorarea, analiza pe token, evidentele,
             Elliott, structura, lichiditatea si cazul de utilizare al scanarii
             (core/scan.py). Fara retea, fara fisiere.
  ports/     contractele nucleului: date de piata, documente, notificari.
  adapters/  implementarile: bursele prin ccxt (cate un modul per bursa),
             fisierele JSON, Telegram.

Acest fisier leaga adaptoarele de porturile nucleului si porneste scanarea - e
singurul loc care stie ce implementare concreta sta in spatele fiecarui port.
Numele vechi (crypto_ai_scanner.CONFIG, .score_symbol, .PULLBACK_ATR, ...) raman
valide: se citesc si se scriu direct in nucleu (adapters/compat.py), deci
backtest-ul si celelalte module merg neschimbate.

Rulare: python crypto_ai_scanner.py  (pasul de scanare din scan.yml)
"""

import ccxt
import requests

import ai_agent
import altseason as alt_mod
import plan_tracker
from adapters import compat
from adapters.exchanges.venues import CcxtVenues
from adapters.notify.telegram import send_telegram
from adapters.storage.json_store import JsonStore
from core import analysis, config, geometry, learning, scoring, universe
from core import scan as _core


def fetch_coingecko_top_symbols(top_n=200):
    """Simbolurile din top N CoinGecko dupa market cap (nu dupa volum de
    schimb) - folosite ca sa restrangem universul la proiecte relevante
    fundamental, nu doar la ce are volum mare pe termen scurt."""
    symbols = set()
    per_page = 250
    pages = (top_n + per_page - 1) // per_page
    for page in range(1, pages + 1):
        url = (f"https://api.coingecko.com/api/v3/coins/markets"
               f"?vs_currency=usd&order=market_cap_desc&per_page={per_page}&page={page}")
        try:
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            for coin in resp.json():
                symbols.add(coin["symbol"].upper())
        except Exception as e:
            print(f"[!] CoinGecko top-{top_n} (pagina {page}): {e}")
            break
    return symbols


# ============================ RADACINA COMPOZITIEI ===========================
_core.STORE = JsonStore()
_core.PLAN_STORE = plan_tracker
_core.AGENT_STORE = ai_agent
_core.VENUES = CcxtVenues(ccxt)
_core.NOTIFIER = send_telegram
_core.ALTSEASON = lambda market, results: alt_mod.update(market, results)
_core.TOP_SYMBOLS = fetch_coingecko_top_symbols

# numele vechi: din cazul de utilizare si din straturile lui (backtest-ul scrie PULLBACK_ATR,
# SL_ATR, RSI_LONG... - fatada le scrie in modulul care le foloseste)
compat.bind(__name__, _core, config, scoring, geometry, universe, learning, analysis)


def main():
    return _core.main()


if __name__ == "__main__":
    main()
