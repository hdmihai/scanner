# -*- coding: utf-8 -*-
"""adapters.exchanges.kraken - adaptorul Kraken.

Coteaza aproape totul in USD (perechile USDT au volum mic, de aceea perechea se
alege dupa volum) si adauga un timestamp fiecarui nivel de order book. Limita de o
cerere pe secunda o face cea mai lenta bursa din scanare.
"""

from adapters.exchanges.base import (CAP_OHLCV, CAP_OPEN_INTEREST, CAP_ORDERBOOK_LIVE,
                                     CAP_TRADES_LIVE, ExchangeAdapter)


class Kraken(ExchangeAdapter):
    id = "kraken"
    label = "Kraken"
    declared = (CAP_OHLCV, CAP_ORDERBOOK_LIVE, CAP_TRADES_LIVE, CAP_OPEN_INTEREST)
    note = ("Coteaza aproape totul in USD (perechile USDT au volum mic, de aceea perechea se alege dupa volum) si adauga un timestamp fiecarui nivel de order book. Limita de o cerere pe secunda o face cea mai lenta bursa din scanare.")
