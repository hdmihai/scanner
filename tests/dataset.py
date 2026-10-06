# -*- coding: utf-8 -*-
"""tests.dataset - seturile de lumanari ale burselor simulate, construite din datele reale ale repo-ului.

Ultimele bare REALE din data/latest_details.json, extinse inapoi determinist pana la
~3000 de bare (ca timeframe-urile de confirmare 1d/1w sa aiba destule inchideri),
plus bara curenta neinchisa. Acelasi repo da mereu acelasi set - testele sunt
reproductibile bit cu bit.
"""

import json
import math
import random
import statistics

TOTAL = 3000
TF_NAMES = {300000: "5m", 900000: "15m", 3600000: "1h", 14400000: "4h", 86400000: "1d"}


def build(repo, out_path):
    with open(f"{repo}/data/latest_details.json") as f:
        d = json.load(f)
    series = {}
    for sym, v in (d.get("symbols") or {}).items():
        c = v.get("candles") or []
        if len(c) >= 20:
            series[sym.split("/")[0]] = [[int(x[0])] + [float(y) for y in x[1:6]] for x in c]
    if not series:
        raise SystemExit("data/latest_details.json nu are lumanari: setul de test nu poate fi construit")
    bar = int(statistics.median(b[0] - a[0] for rows in series.values() for a, b in zip(rows, rows[1:])))
    last_ts = max(rows[-1][0] for rows in series.values())
    full = {}
    for base, rows in sorted(series.items()):
        rnd = random.Random("ext:" + base)
        rets = [math.log(rows[i][4] / rows[i - 1][4]) for i in range(1, len(rows)) if rows[i - 1][4] > 0]
        sd = max(0.004, (sum(r * r for r in rets) / max(1, len(rets))) ** 0.5)
        vavg = sum(r[5] for r in rows) / len(rows)
        back, nxt_open, t, drift = [], rows[0][1], rows[0][0], 0.0
        for _ in range(TOTAL - len(rows)):
            t -= bar
            drift = 0.97 * drift + rnd.gauss(0, sd * 0.25)          # regimuri de trend
            c = nxt_open
            o = c / math.exp(rnd.gauss(drift, sd))
            h = max(o, c) * (1 + abs(rnd.gauss(0, sd * 0.45)))
            lo = min(o, c) * (1 - abs(rnd.gauss(0, sd * 0.45)))
            back.append([int(t), o, h, lo, c, vavg * math.exp(rnd.gauss(0, 0.35))])
            nxt_open = o
        allr = back[::-1] + rows
        lc = allr[-1][4]
        allr.append([allr[-1][0] + bar, lc, lc * 1.003, lc * 0.997, lc * 1.001, vavg * 0.4])   # bara neinchisa
        full[base] = allr
    meta = {"bar_ms": bar, "base_tf": TF_NAMES.get(bar, "4h"), "last_closed": last_ts}
    with open(out_path, "w") as f:
        json.dump({**meta, "series": full}, f)
    return {**meta, "symbols": len(full)}


def add_bar(in_path, out_path):
    """Scenariul "bara noua": bara neinchisa se inchide (miscare determinista de -1%..+1%)
    si apare o bara noua, neinchisa - planurile deschise se evalueaza pe date noi."""
    with open(in_path) as f:
        d = json.load(f)
    b = d["bar_ms"]
    for base, rows in d["series"].items():
        last, o = rows[-1], rows[-2][4]
        k = (sum(map(ord, base)) % 7 - 3) / 300.0
        c = o * (1 + k)
        rows[-1] = [last[0], o, max(o, c) * 1.006, min(o, c) * 0.994, c, rows[-2][5]]
        rows.append([last[0] + b, c, c * 1.002, c * 0.998, c, rows[-2][5] * 0.3])
    d["last_closed"] += b
    with open(out_path, "w") as f:
        json.dump(d, f)


def frozen_time(meta):
    """Momentul scanarii: la 49 de minute dupa inchiderea ultimei bare (ca in productie)."""
    return (meta["last_closed"] + meta["bar_ms"]) / 1000 + 49 * 60
