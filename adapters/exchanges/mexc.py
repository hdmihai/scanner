# -*- coding: utf-8 -*-
"""adapters.exchanges.mexc - adaptorul MEXC.

Open interest indisponibil pe spot - evidentele care depind de el sunt omise, nu
inlocuite cu valori neutre.
"""

from adapters.exchanges.base import (CAP_OHLCV, CAP_OPEN_INTEREST, CAP_ORDERBOOK_LIVE,
                                     CAP_TRADES_LIVE, ExchangeAdapter)


class MEXC(ExchangeAdapter):
    id = "mexc"
    label = "MEXC"
    declared = (CAP_OHLCV, CAP_ORDERBOOK_LIVE, CAP_TRADES_LIVE, CAP_OPEN_INTEREST)
    note = ("Open interest indisponibil pe spot - evidentele care depind de el sunt omise, nu inlocuite cu valori neutre.")
