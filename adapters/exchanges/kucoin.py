# -*- coding: utf-8 -*-
"""adapters.exchanges.kucoin - adaptorul KuCoin.

Order book doar la 20 sau 100 de niveluri - alte valori sunt respinse. Rata de
interogare permisiva.
"""

from adapters.exchanges.base import (CAP_OHLCV, CAP_OPEN_INTEREST, CAP_ORDERBOOK_LIVE,
                                     CAP_TRADES_LIVE, ExchangeAdapter)


class KuCoin(ExchangeAdapter):
    id = "kucoin"
    label = "KuCoin"
    declared = (CAP_OHLCV, CAP_ORDERBOOK_LIVE, CAP_TRADES_LIVE, CAP_OPEN_INTEREST)
    book_limits = (100, 20)
    note = ("Order book doar la 20 sau 100 de niveluri - alte valori sunt respinse. Rata de interogare permisiva.")
