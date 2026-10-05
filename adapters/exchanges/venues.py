# -*- coding: utf-8 -*-
"""adapters.exchanges.venues - registrul burselor ca implementare a portului ports.market_data.Venues.

Leaga nucleul de ccxt: sondarea fiecarei burse (prin adaptorul ei), conexiunile noi
si particularitatile per bursa. Nucleul nu importa ccxt si nici adaptoarele.
"""

from adapters import exchanges as _registry


class CcxtVenues:
    def __init__(self, ccxt_mod):
        self.ccxt = ccxt_mod

    def order(self):
        return _registry.ids()

    def probe(self, exchange_id):
        return _registry.get(exchange_id).probe(self.ccxt)

    def open(self, exchange_id):
        cls = getattr(self.ccxt, exchange_id, None)
        return cls({"enableRateLimit": True}) if cls is not None else None

    def adapter(self, exchange_id):
        return _registry.get(exchange_id)
