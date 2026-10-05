# -*- coding: utf-8 -*-
"""ports.market_data - portul datelor de piata: ce cere nucleul unei burse.

Forma metodelor e cea din ccxt (load_markets, fetch_ohlcv, ...), asa ca o
conexiune ccxt implementeaza portul direct; un adaptor pentru o sursa non-ccxt
trebuie doar sa ofere aceleasi metode. Capabilitatile (CAP_*) sunt vocabularul
comun dintre nucleu (care decide ce evidente calculeaza) si adaptoare (care
raporteaza ce functioneaza efectiv pe fiecare bursa).
"""

from typing import Protocol

CAP_OHLCV = "ohlcv"
CAP_ORDERBOOK_LIVE = "orderbook_live"
CAP_TRADES_LIVE = "trades_live"
CAP_ORDERBOOK_HISTORY = "orderbook_history"
# Open interest curent (ccxt fetch_open_interest). Rafineaza harta de lichidari;
# lipsa lui schimba magnitudinea, nu forma.
CAP_OPEN_INTEREST = "open_interest"

ALL_CAPS = [CAP_OHLCV, CAP_ORDERBOOK_LIVE, CAP_TRADES_LIVE,
            CAP_ORDERBOOK_HISTORY, CAP_OPEN_INTEREST]


class MarketData(Protocol):
    """Conexiunea la O bursa."""

    def load_markets(self, reload=False): ...

    def fetch_tickers(self, symbols=None, params=None): ...

    def fetch_ohlcv(self, symbol, timeframe="1m", since=None, limit=None, params=None): ...

    def fetch_order_book(self, symbol, limit=None, params=None): ...

    def fetch_trades(self, symbol, since=None, limit=None, params=None): ...

    def fetch_open_interest(self, symbol, params=None): ...


class Venues(Protocol):
    """Registrul burselor, vazut din nucleu: ordinea, sondarea si conexiunile."""

    def order(self):
        """Id-urile burselor, in ordinea registrului."""

    def probe(self, exchange_id):
        """(fisa de stare, conexiune MarketData sau None)."""

    def open(self, exchange_id):
        """O conexiune noua (MarketData) sau None daca bursa nu exista."""

    def adapter(self, exchange_id):
        """Particularitatile bursei: label, book_limits, markets(conn), tickers(conn)."""
