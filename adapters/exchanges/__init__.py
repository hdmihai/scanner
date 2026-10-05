"""
adapters.exchanges - registrul burselor: cate un modul per bursa.

ORDER e ordinea de alegere a bursei active (prima care acopera watchlist-ul) si
ordinea modulelor din dashboard. O bursa noua: un modul nou in acest dosar, o
clasa care mosteneste ExchangeAdapter, o linie mai jos.
"""

from adapters.exchanges.base import ExchangeAdapter
from adapters.exchanges.bybit import Bybit
from adapters.exchanges.gate import Gate
from adapters.exchanges.kraken import Kraken
from adapters.exchanges.kucoin import KuCoin
from adapters.exchanges.mexc import MEXC
from adapters.exchanges.okx import OKX

_ADAPTERS = [OKX(), KuCoin(), Gate(), MEXC(), Kraken(), Bybit()]
ORDER = [a.id for a in _ADAPTERS]
_BY_ID = {a.id: a for a in _ADAPTERS}


def ids():
    return list(ORDER)


def all_adapters():
    return list(_ADAPTERS)


def get(exchange_id):
    """Adaptorul inregistrat, sau unul generic (doar OHLCV declarat) pentru un id
    necunoscut - acelasi comportament ca registrul vechi."""
    return _BY_ID.get(exchange_id) or ExchangeAdapter(exchange_id)
