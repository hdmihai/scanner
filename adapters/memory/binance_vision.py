# -*- coding: utf-8 -*-
"""adapters.memory.binance_vision - arhiva publica Binance (data.binance.vision), fara cheie API.

Fisiere zip lunare per simbol si timeframe, fiecare cu un .CHECKSUM (SHA256) alaturi; lista se
citeste din bucket-ul S3 public. De la 1 ianuarie 2025, timpii din arhiva spot sunt in MICROsecunde -
se normalizeaza la milisecunde, ca restul memoriei."""

import csv
import hashlib
import io
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile

BASE = "https://data.binance.vision"
LIST = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"


def _get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "scanner-memory"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _list(prefix):
    """(prefixe, chei) sub `prefix`, cu paginare S3."""
    prefixes, keys, marker = [], [], ""
    while True:
        q = {"delimiter": "/", "prefix": prefix}
        if marker:
            q["marker"] = marker
        xml = _get(LIST + "?" + urllib.parse.urlencode(q)).decode()
        prefixes += re.findall(r"<Prefix>([^<]+)</Prefix>", xml.split("</Prefix>", 1)[1] if "</Prefix>" in xml else "")
        keys += re.findall(r"<Key>([^<]+)</Key>", xml)
        if "<IsTruncated>true</IsTruncated>" not in xml:
            return prefixes, keys
        marker = (keys or prefixes)[-1]


def usdt_symbols():
    pre, _ = _list("data/spot/monthly/klines/")
    out = []
    for p in pre:
        s = p.rstrip("/").split("/")[-1]
        if s.endswith("USDT") and not re.search(r"(UP|DOWN|BULL|BEAR)USDT$", s):
            out.append(s)
    return sorted(set(out))


def months(symbol, tf):
    _, keys = _list(f"data/spot/monthly/klines/{symbol}/{tf}/")
    return sorted(k for k in keys if k.endswith(".zip"))


def month_of(key):
    m = re.search(r"-(\d{4}-\d{2})\.zip$", key)
    return m.group(1) if m else None


def download_month(key):
    """Lumanarile [ts_ms, o, h, l, c, v] dintr-o arhiva lunara, verificata cu checksum-ul ei."""
    data = _get(f"{BASE}/{key}")
    try:
        want = _get(f"{BASE}/{key}.CHECKSUM").decode().split()[0].strip()
        if hashlib.sha256(data).hexdigest() != want:
            raise ValueError(f"checksum gresit pentru {key}")
    except urllib.error.HTTPError:
        pass                                       # arhivele vechi pot sa nu aiba .CHECKSUM
    out = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            for row in csv.reader(io.TextIOWrapper(z.open(name))):
                if not row or not row[0].isdigit():
                    continue                      # antet (unele luni il au)
                ts = int(row[0])
                if ts > 10 ** 14:                  # microsecunde (2025+)
                    ts //= 1000
                out.append([ts, float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])])
    return out


def probe():
    """(accesibil, detaliu) - o luna cunoscuta pentru BTCUSDT 1d."""
    try:
        rows = download_month("data/spot/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2024-01.zip")
        return bool(rows), f"{len(rows)} lumanari BTCUSDT 1d ianuarie 2024"
    except Exception as e:
        return False, str(e)[:160]
