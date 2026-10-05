# -*- coding: utf-8 -*-
"""adapters.exchanges.okx - adaptorul OKX.

Bursa activa implicita: acopera toata watchlist-ul si ofera, de pe runner-ele GitHub,
lumanari, adancime si flux de tranzactii.
"""

from adapters.exchanges.base import (CAP_OHLCV, CAP_OPEN_INTEREST, CAP_ORDERBOOK_LIVE,
                                     CAP_TRADES_LIVE, ExchangeAdapter)


class OKX(ExchangeAdapter):
    id = "okx"
    label = "OKX"
    declared = (CAP_OHLCV, CAP_ORDERBOOK_LIVE, CAP_TRADES_LIVE, CAP_OPEN_INTEREST)
    note = ("Bursa activa implicita: acopera toata watchlist-ul si ofera, de pe runner-ele GitHub, lumanari, adancime si flux de tranzactii.")
