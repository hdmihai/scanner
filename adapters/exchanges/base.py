# -*- coding: utf-8 -*-
"""
adapters.exchanges.base - contractul comun al adaptoarelor de bursa (portul "date de piata").

DE CE CATE UN MODUL PER BURSA
-----------------------------
Bursele difera prin detalii care, amestecate in scaner, devin conditii ascunse:
KuCoin accepta order book doar la 20 sau 100 de niveluri, Kraken coteaza aproape
totul in USD si adauga un timestamp fiecarui nivel, Bybit blocheaza geografic
runner-ele GitHub. Fiecare particularitate traieste acum in modulul bursei ei;
nucleul primeste aceleasi date, in aceeasi forma, de la oricare bursa.

CE FACE UN ADAPTOR - si ce NU face
----------------------------------
Aduce date: piete, tickere, lumanari inchise, adancime, tranzactii, open
interest. NU scoreaza, NU decide si NU invata - logica de analiza e aceeasi
pentru toate bursele, deci un adaptor nou nu poate schimba comportamentul
agentului. O bursa noua inseamna un modul nou aici si o linie in registru.

Toate metodele raporteaza esecul ca stare (None / card cu eroare), nu ca
exceptie: o bursa care nu raspunde nu are voie sa opreasca scanarea.
"""

import time

# Vocabularul capabilitatilor e al portului (comun nucleului si adaptoarelor).
from ports.market_data import (ALL_CAPS, CAP_OHLCV, CAP_OPEN_INTEREST,  # noqa: F401,E402
                               CAP_ORDERBOOK_HISTORY, CAP_ORDERBOOK_LIVE, CAP_TRADES_LIVE)


class ExchangeAdapter:
    """Adaptorul de baza. Subclasele declara doar ce le deosebeste."""

    id = None              # identificatorul intern = numele modulului
    ccxt_id = None         # clasa din ccxt, daca difera de id
    label = None
    declared = (CAP_OHLCV,)
    # adancimile de order book incercate, in ordine; None = fara parametru
    book_limits = (100, 20, None)
    # particularitatea afisata in dashboard (ex. de ce lipsesc unele date)
    note = None

    def __init__(self, exchange_id=None, label=None):
        if exchange_id:                      # adaptor generic, pentru id-uri neinregistrate
            self.id = exchange_id
            self.label = label or exchange_id

    # ------------------------------------------------------------- conexiune
    def create(self, ccxt_mod):
        cls = getattr(ccxt_mod, self.ccxt_id or self.id, None)
        return cls({"enableRateLimit": True}) if cls is not None else None

    def probe(self, ccxt_mod, probe_symbol=None):
        """Conectare + capabilitatile care chiar functioneaza de pe acest runner.

        Returneaza (fisa, conexiune). Fisa e folosita si de alegerea bursei
        active, si de dashboard. O bursa care nu raspunde e o stare valida."""
        card = {
            "id": self.id, "label": self.label,
            "declared": list(self.declared),
            "available": [], "connected": False, "markets": 0,
            "error": None, "checked_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        }
        cls = getattr(ccxt_mod, self.ccxt_id or self.id, None)
        if cls is None:
            card["error"] = f"'{self.id}' nu exista in ccxt (redenumit sau eliminat)"
            return card, None
        try:
            ex = cls({"enableRateLimit": True})
            markets = ex.load_markets()
        except Exception as exc:
            card["error"] = str(exc)[:180]
            return card, None

        card["connected"] = True
        card["markets"] = len(markets)
        card["available"].append(CAP_OHLCV)

        sym = probe_symbol
        if not sym:
            for cand in markets:
                if cand.endswith("/USDT") and markets[cand].get("active", True):
                    sym = cand
                    break
        if not sym:
            card["error"] = "conectat, dar nicio pereche USDT activa"
            return card, ex

        if CAP_ORDERBOOK_LIVE in self.declared:
            try:
                ob = ex.fetch_order_book(sym, limit=20)
                if ob and (ob.get("bids") or ob.get("asks")):
                    card["available"].append(CAP_ORDERBOOK_LIVE)
            except Exception:
                pass
        if CAP_OPEN_INTEREST in self.declared:
            try:
                oi = ex.fetch_open_interest(sym)
                if oi and (oi.get("openInterestAmount") or oi.get("openInterestValue")):
                    card["available"].append(CAP_OPEN_INTEREST)
            except Exception:
                pass
        if CAP_TRADES_LIVE in self.declared:
            try:
                tr = ex.fetch_trades(sym, limit=50)
                if tr and any(t.get("side") for t in tr):
                    card["available"].append(CAP_TRADES_LIVE)
            except Exception:
                pass
        return card, ex

    # ------------------------------------------------------------- date
    def markets(self, handle):
        """Pietele bursei (ccxt le tine in cache dupa prima incarcare)."""
        try:
            return handle.load_markets() or {}
        except Exception:
            return {}

    def tickers(self, handle):
        """Tickerele, pentru alegerea perechii cu volumul cel mai mare. {} la esec:
        rezolvarea cade atunci pe ordinea monedelor de cotare."""
        try:
            return handle.fetch_tickers() or {}
        except Exception:
            return {}

    def order_book_raw(self, handle, symbol):
        """(order book, None) sau (None, eroare), incercand adancimile acceptate."""
        last_err = None
        for attempt in self.book_limits:
            try:
                ob = (handle.fetch_order_book(symbol, limit=attempt) if attempt
                      else handle.fetch_order_book(symbol))
                return ob, None
            except Exception as e:
                last_err = e
        return None, last_err
