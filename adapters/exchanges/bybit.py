# -*- coding: utf-8 -*-
"""adapters.exchanges.bybit - adaptorul Bybit.

Raspunde cu 403 runner-elor GitHub (blocheaza geografic regiunea serverelor), deci
apare in dashboard ca indisponibila, cu motivul exact. Nu exista o ocolire gratuita
si curata. Singura bursa cu arhive publice de order book istoric - declarate pentru
corectitudine, dar nefolosibile la scara noastra (zeci de mii de GB).
"""

from adapters.exchanges.base import (CAP_OHLCV, CAP_OPEN_INTEREST, CAP_ORDERBOOK_HISTORY,
                                     CAP_ORDERBOOK_LIVE, CAP_TRADES_LIVE, ExchangeAdapter)


class Bybit(ExchangeAdapter):
    id = "bybit"
    label = "Bybit"
    declared = (CAP_OHLCV, CAP_ORDERBOOK_LIVE, CAP_TRADES_LIVE, CAP_ORDERBOOK_HISTORY,
                CAP_OPEN_INTEREST)
    note = ("Blocata geografic pentru runner-ele GitHub (raspunde 403); ramane afisata "
            "ca indisponibila, cu motivul exact.")
