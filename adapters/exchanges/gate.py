# -*- coding: utf-8 -*-
"""adapters.exchanges.gate - adaptorul Gate.io.

Redenumita din `gateio` in ccxt; un id vechi ar fi fost sarit in tacere. Open
interest indisponibil pe spot - evidentele care depind de el sunt omise.
"""

from adapters.exchanges.base import (CAP_OHLCV, CAP_OPEN_INTEREST, CAP_ORDERBOOK_LIVE,
                                     CAP_TRADES_LIVE, ExchangeAdapter)


class Gate(ExchangeAdapter):
    id = "gate"
    label = "Gate.io"
    declared = (CAP_OHLCV, CAP_ORDERBOOK_LIVE, CAP_TRADES_LIVE, CAP_OPEN_INTEREST)
    note = ("Redenumita din `gateio` in ccxt; un id vechi ar fi fost sarit in tacere. Open interest indisponibil pe spot - evidentele care depind de el sunt omise.")
