#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
exchanges.py
=============
Registru de burse cu CAPABILITATI declarate si sondate.

PROBLEMA PE CARE O REZOLVA
--------------------------
Bursele difera prin ce date ofera. OKX nu da order book istoric; Bybit publica
seturi L2 gratuite, dar de ordinul zecilor de mii de GB pentru universul nostru,
deci inutilizabile intr-un job de 90 de minute. Tranzactiile recente, in schimb,
sunt accesibile prin ccxt pe majoritatea burselor - de acolo iese order flow-ul,
dar doar LIVE, nu retroactiv.

Solutia nu e sa renunt la evidentele care nu merg peste tot, ci sa le fac
CONDITIONATE si EXPLICITE:
  - fiecare bursa declara ce capabilitati are
  - fiecare evidenta declara ce capabilitate ii trebuie
  - evidentele fara suport sunt omise, nu inlocuite cu valori inventate
  - setul activ de capabilitati intra in SEMNATURA care separa invatarea

Ultimul punct e cel care previne dezastrul tacut. Un plan creat cu order flow
si unul creat fara au vectori de caracteristici diferiti. Fara semnatura,
agentul ar invata din amandoua ca si cum ar fi acelasi lucru - exact greseala
pe care versionarea geometriei o previne la schimbarile de timeframe.

ARHITECTURA
-----------
Implementarea per bursa e in adapters/exchanges/<bursa>.py (cate un modul per
bursa); acest fisier pastreaza interfata folosita de scaner.

CAPABILITATI
------------
  ohlcv             lumanari istorice - obligatoriu, toate bursele
  orderbook_live    adancime curenta (ccxt fetch_order_book)
  trades_live       tranzactii recente cu partea agresoare (ccxt fetch_trades)
                    -> order flow / footprint delta, DOAR live
  orderbook_history arhive istorice de order book (doar Bybit public, dar
                    nefolosibile la scara noastra - declarat pentru corectitudine)
"""

# Capabilitatile si registrul traiesc acum in adaptoarele per bursa
# (adapters/exchanges/). Modulul acesta ramane fatada compatibila: scanerul si
# celelalte module il folosesc la fel ca inainte.
from adapters import exchanges as _adapters
from adapters.exchanges.base import (ALL_CAPS, CAP_OHLCV, CAP_OPEN_INTEREST,  # noqa: F401
                                     CAP_ORDERBOOK_HISTORY, CAP_ORDERBOOK_LIVE,
                                     CAP_TRADES_LIVE)

# Capabilitatile DECLARATE, construite din adaptoare - o singura sursa de adevar.
REGISTRY = {a.id: {"label": a.label, "declared": list(a.declared)}
            for a in _adapters.all_adapters()}

DEFAULT_ORDER = _adapters.ids()


def adapter(exchange_id):
    """Adaptorul bursei (modulul ei din adapters/exchanges/)."""
    return _adapters.get(exchange_id)


def probe_exchange(ccxt_mod, exchange_id, probe_symbol=None, timeout_note=""):
    """Incearca sa se conecteze si sa verifice ce capabilitati chiar functioneaza.

    Returneaza o fisa de stare folosita si de logica de scanare, si de dashboard.
    Nu arunca exceptii: o bursa care nu raspunde e o stare valida, nu o eroare.
    Implementarea e in adaptorul bursei.
    """
    return adapter(exchange_id).probe(ccxt_mod, probe_symbol)


# Logica de extragere (flux, open interest, semnatura) e in nucleu: core/market.py.
from core.market import capability_signature, open_interest, order_flow  # noqa: F401,E402
