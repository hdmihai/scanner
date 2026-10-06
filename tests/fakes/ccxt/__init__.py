"""ccxt FALS, determinist, pentru testele offline ale scanerului (tests/fakes/ccxt).

Simuleaza cele 6 burse cu particularitatile vazute in productie:
  - OKX/KuCoin/Gate/MEXC: perechi USDT (+ cateva USDC cu volum mic)
  - Kraken: perechi USD cu volum mare + cateva USDT cu volum mic; nivelurile de
    order book au 3 campuri (pret, cantitate, timestamp)
  - KuCoin: order book accepta doar limit 20 sau 100
  - simboluri lipsa: Gate ZEN, MEXC THETA, Kraken GRAM/ZEN/THETA
  - open interest: indisponibil peste tot (ca in productie)
  - Bybit: 403 la load_markets (blocat geografic)
Preturile difera usor intre burse (factor + zgomot determinist), volumele difera.
Fiecare apel e numarat in CALLS, ca testele sa poata masura costul in apeluri API.
"""
import hashlib, json, math, os

__version__ = "fake-4.5"
CALLS = {}


class BaseError(Exception):
    pass


class ExchangeError(BaseError):
    pass


class NotSupported(ExchangeError):
    pass


class BadRequest(ExchangeError):
    pass


_DATA = None


def _data():
    global _DATA
    if _DATA is None:
        with open(os.environ["TESTS_DATASET"]) as f:
            _DATA = json.load(f)
    return _DATA


def _h(*parts):
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest(), 16)


TF_MS = {"5m": 300000, "15m": 900000, "1h": 3600000, "4h": 14400000, "1d": 86400000, "1w": 604800000}


def _fail(eid, what):
    return f"{eid}:{what}" in (os.environ.get("FAKE_FAIL") or "").split(",")


class _Fake:
    id = "fake"
    rateLimit = 0
    PROFILE = {"factor": 1.0, "vol": 1.0, "missing": [], "quotes": [("USDT", 1.0), ("USDC", 0.05)],
               "book3": False, "book_limits": None}

    def __init__(self, config=None):
        self.config = config or {}
        self.markets = None
        self.has = {"fetchOHLCV": True, "fetchOrderBook": True, "fetchTrades": True,
                    "fetchOpenInterest": False, "fetchTickers": True}

    def parse_timeframe(self, tf):
        return TF_MS[tf] // 1000

    def milliseconds(self):
        import time as _t
        return int(_t.time() * 1000)

    def _count(self, name):
        CALLS[(self.id, name)] = CALLS.get((self.id, name), 0) + 1

    # ---- piete
    def load_markets(self, reload=False):
        self._count("load_markets")
        self._lm = getattr(self, "_lm", 0) + 1
        if self._lm > 1 and _fail(self.id, "markets2"):
            raise ExchangeError(f"{self.id} 503 Service Unavailable")
        out = {}
        for base in sorted(_data()["series"]):
            if base in self.PROFILE["missing"]:
                continue
            for q, _w in self.PROFILE["quotes"]:
                if q != self.PROFILE["quotes"][0][0] and _h(self.id, base, q) % 3:
                    continue          # perechile secundare exista doar pentru o parte din simboluri
                s = f"{base}/{q}"
                out[s] = {"id": s.replace("/", "-"), "symbol": s, "base": base, "quote": q,
                          "active": True, "spot": True, "type": "spot"}
        # token listat si sub numele vechi (ex. MATIC pentru POL), cu volum mai mare
        for alias, real in (self.PROFILE.get("alias") or {}).items():
            s = f"{alias}/USDT"
            out[s] = {"id": s, "symbol": s, "base": alias, "quote": "USDT", "active": True}
        # cateva piete in afara watchlist-ului, ca universul sa nu fie artificial de curat
        for i in range(12):
            s = f"FILL{i}/USDT"
            out[s] = {"id": s, "symbol": s, "base": f"FILL{i}", "quote": "USDT", "active": i % 4 != 0}
        self.markets = out
        return out

    def _series(self, symbol, timeframe="4h"):
        base = symbol.split("/")[0]
        base = (self.PROFILE.get("alias") or {}).get(base, base)
        rows = _data()["series"].get(base)
        if rows is None:
            raise BadRequest(f"{self.id} {symbol}: simbol necunoscut")
        f = self.PROFILE["factor"]
        if self.PROFILE.get("exact"):
            out = [list(c) for c in rows]          # bursa activa: lumanarile reale, neatinse
        else:
            out = []
        for i, c in enumerate(rows if not self.PROFILE.get("exact") else []):
            j = 1 + 0.0006 * math.sin(i * 0.7 + (_h(self.id) % 97))
            out.append([c[0], c[1] * f * j, c[2] * f * j, c[3] * f * j, c[4] * f * j,
                        c[5] * self.PROFILE["vol"] * (0.8 + 0.4 * ((_h(self.id, base, i) % 1000) / 1000))])
        base = _data().get("base_tf", "4h")
        if timeframe == base:
            return out
        if TF_MS[timeframe] < TF_MS[base]:
            k = TF_MS[base] // TF_MS[timeframe]
            sub = []
            for c in out[-400:]:
                o, h, l, cl, v = c[1], c[2], c[3], c[4], c[5]
                path = [o + (cl - o) * (m + 1) / k for m in range(k)]
                for m in range(k):
                    so = o if m == 0 else path[m - 1]
                    sc = path[m]
                    sh = h if m == 1 else max(so, sc) * 1.0005
                    sl = l if m == k - 2 else min(so, sc) * 0.9995
                    sub.append([c[0] + m * TF_MS[timeframe], so, max(sh, so, sc), min(sl, so, sc), sc, v / k])
            return sub
        k = max(1, TF_MS[timeframe] // TF_MS[base])
        agg = []
        start = len(out) % k
        for a in range(start, len(out), k):
            g = out[a:a + k]
            agg.append([g[0][0], g[0][1], max(x[2] for x in g), min(x[3] for x in g), g[-1][4], sum(x[5] for x in g)])
        return agg

    def fetch_ohlcv(self, symbol, timeframe="1m", since=None, limit=None, params=None):
        self._count("fetch_ohlcv")
        if _fail(self.id, "ohlcv"):
            raise ExchangeError(f"{self.id} 429 Too Many Requests")
        if _fail(self.id, "bug") and timeframe == _data().get("base_tf", "4h"):
            return [[0, "x", "y", "z", "w", None]] * (limit or 100)
        if self.markets is not None and symbol not in self.markets:
            raise BadRequest(f"{self.id} does not have market symbol {symbol}")
        rows = self._series(symbol, timeframe)
        if since is not None:
            rows = [r for r in rows if r[0] >= since]
            return [list(r) for r in rows[:limit or len(rows)]]
        return [list(r) for r in rows[-(limit or 100):]]

    def fetch_tickers(self, symbols=None, params=None):
        self._count("fetch_tickers")
        mk = self.markets or self.load_markets()
        out = {}
        for s, m in mk.items():
            if s.startswith("FILL"):
                out[s] = {"symbol": s, "last": 1.0, "quoteVolume": 1000.0}
                continue
            last = self._series(s)[-1][4]
            w = dict(self.PROFILE["quotes"]).get(m["quote"], 0.01)
            if m["base"] in (self.PROFILE.get("alias") or {}):
                w *= 100                      # numele vechi are volumul mai mare
            out[s] = {"symbol": s, "last": last, "quoteVolume": 1e7 * w * (1 + _h(self.id, s) % 50)}
        return out

    def fetch_order_book(self, symbol, limit=None, params=None):
        self._count("fetch_order_book")
        lim = self.PROFILE["book_limits"]
        if lim and limit is not None and limit not in lim:
            raise BadRequest(f"{self.id} order book: limit {limit} invalid, accepta {lim}")
        px = self._series(symbol)[-1][4]
        n = min(limit or 50, 50)
        bids, asks = [], []
        for i in range(n):
            a1 = 10 + (_h(self.id, symbol, "b", i) % 900)
            a2 = 10 + (_h(self.id, symbol, "a", i) % 900)
            b = [px * (1 - 0.0008 * (i + 1)), a1 / 10]
            a = [px * (1 + 0.0008 * (i + 1)), a2 / 10]
            if self.PROFILE["book3"]:
                b.append(1791158400 + i); a.append(1791158400 + i)
            bids.append(b); asks.append(a)
        return {"symbol": symbol, "bids": bids, "asks": asks}

    def fetch_trades(self, symbol, since=None, limit=None, params=None):
        self._count("fetch_trades")
        px = self._series(symbol)[-1][4]
        n = limit or 50
        return [{"symbol": symbol, "price": px, "amount": 1 + (_h(self.id, symbol, "t", i) % 40) / 4,
                 "side": "buy" if _h(self.id, symbol, "s", i) % 100 < 45 + _h(symbol) % 15 else "sell"}
                for i in range(n)]

    def fetch_open_interest(self, symbol, params=None):
        self._count("fetch_open_interest")
        raise NotSupported(f"{self.id} fetchOpenInterest() is not supported yet")


class okx(_Fake):
    id = "okx"
    PROFILE = {**_Fake.PROFILE, "factor": 1.0, "vol": 1.0, "exact": True}


class kucoin(_Fake):
    id = "kucoin"
    PROFILE = {**_Fake.PROFILE, "factor": 1.0004, "vol": 0.55, "book_limits": (20, 100)}


class gate(_Fake):
    id = "gate"
    PROFILE = {**_Fake.PROFILE, "factor": 0.9997, "vol": 0.7, "missing": ["ZEN"]}


class mexc(_Fake):
    id = "mexc"
    PROFILE = {**_Fake.PROFILE, "factor": 1.0002, "vol": 0.8, "missing": ["THETA"], "alias": {"MATIC": "POL"}}


class kraken(_Fake):
    id = "kraken"
    PROFILE = {**_Fake.PROFILE, "factor": 0.9995, "vol": 0.35, "missing": ["GRAM", "ZEN", "THETA"],
               "quotes": [("USD", 1.0), ("USDT", 0.08)], "book3": True}


class bybit(_Fake):
    id = "bybit"

    def load_markets(self, reload=False):
        self._count("load_markets")
        raise ExchangeError('bybit GET https://api.bybit.com/v5/market/instruments-info?category=spot 403 Forbidden {"retCode":10024}')

    fetch_tickers = load_markets
