# -*- coding: utf-8 -*-
"""tests.memory_check - depozitul de memorie (core/memory, adapters/memory, memory_sync), offline.

Uz (din radacina repo-ului): python -m tests.memory_check      (cere `duckdb`)

  1. NUCLEUL: un plan trece prin rand Parquet si inapoi cu toate caracteristicile; o caracteristica lipsa
     ramane lipsa (nu 0); unirea pastreaza randul nou; lumanarile imposibile sunt respinse;
  2. PLANURI: oglinda lui data/plans.json in Parquet - aceleasi planuri si acelasi R pe sursa, citite cu
     DuckDB; a doua rulare nu rescrie nimic; un plan schimbat rescrie doar partitia lui;
  3. LUMANARI (bursa simulata, tests/fakes): istoricul complet al universului USDT, fara dubluri, in mai
     multe rulari cu buget mic (fiecare continua de unde a ramas);
  4. BINANCE (arhiva simulata): luni noi adaugate, timpii in microsecunde normalizati, checksum gresit respins;
  5. RELEASE-URI GITHUB (server API simulat local): partitiile si indexul se urca, se inlocuiesc fara
     fereastra de pierdere (nume temporar + redenumire), a doua rulare nu urca nimic.
Nu scrie nimic in repo (data/memory_status.json se redirectioneaza intr-un dosar temporar).
"""

import copy
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("SCAN_TIMEFRAME", "4h")

import memory_sync as MS                         # noqa: E402
from adapters.memory import binance_vision as BV  # noqa: E402
from adapters.memory import parquet as PQ        # noqa: E402
from adapters.memory.local_store import LocalStore  # noqa: E402
from core import agent as A                      # noqa: E402
from core import memory as M                     # noqa: E402

FAILS = []


def ok(cond, label):
    print(f"  {'OK ' if cond else 'ERR'} {label}")
    if not cond:
        FAILS.append(label)


def check_core(plans):
    print("1. nucleul")
    p = next(x for x in reversed(plans) if x.get("components"))
    row = M.plan_row(p, A.FEATURES)
    back = M.row_to_plan(row)
    ok(all(abs(back["components"].get(k, 0) - v) < 1e-12 for k, v in p["components"].items() if k in A.FEATURES),
       "caracteristicile planului trec intacte prin randul Parquet")
    q = dict(p, components={k: v for k, v in p["components"].items() if k != "ev_macd"})
    ok(M.plan_row(q, A.FEATURES)["f_ev_macd"] is None, "caracteristica lipsa ramane lipsa (None), nu 0")
    a = {"symbol": "X", "ts": 1, "close": 1.0}
    b = {"symbol": "X", "ts": 1, "close": 2.0}
    ok(M.merge_rows([a], [b], M.candle_key) == [b], "la aceeasi cheie castiga randul nou")
    ok(not M.valid_candle({"symbol": "X", "ts": 5, "open": 2.0, "high": 1.0, "low": 1.5, "close": 1.2})
       and M.valid_candle({"symbol": "X", "ts": 5, "open": 1.2, "high": 1.5, "low": 1.0, "close": 1.3}),
       "lumanarile imposibile sunt respinse")
    ok(M.stale_first(["A", "B", "C"], {"A": 5, "C": 1}) == ["B", "C", "A"], "ordinea: fara date, apoi cele mai vechi")


def check_plans(tmp, plans):
    print("2. planuri")
    store = LocalStore(os.path.join(tmp, "local"))
    r1 = MS.sync_plans(store)
    names = store.names("plans")
    ok(r1["plans"] == len(plans) and r1["partitions_written"] == len(names) > 0,
       f"{r1['plans']} planuri in {len(names)} partitii")
    files = [store.local_path("plans", n) for n in names]
    got = PQ.query("SELECT source, count(*) n, sum(realized_r) r FROM {src} GROUP BY source ORDER BY source", files)
    exp = {}
    for p in plans:
        s = p.get("source") or "live"
        e = exp.setdefault(s, [0, 0.0])
        e[0] += 1
        e[1] += p.get("realized_r") or 0.0
    ok(all(g["n"] == exp[g["source"]][0] and abs((g["r"] or 0) - exp[g["source"]][1]) < 1e-6 for g in got),
       f"DuckDB peste partitii = plans.json: {[(g['source'], g['n'], round(g['r'] or 0, 2)) for g in got]}")
    sizes = sum(os.path.getsize(f) for f in files)
    print(f"     {sizes / 1e6:.2f} MB Parquet fata de {os.path.getsize(MS.PLANS_FILE) / 1e6:.1f} MB JSON "
          f"({sizes / max(1, len(plans)):.0f} octeti/plan)")
    r2 = MS.sync_plans(store)
    ok(r2["partitions_written"] == 0, "a doua rulare nu rescrie nimic")
    store_json = json.load(open(MS.PLANS_FILE))
    live = [x for x in store_json["plans"] if x.get("source") != "backtest"]
    if live:
        live[-1]["realized_r"] = (live[-1].get("realized_r") or 0) + 0.5
        alt = os.path.join(tmp, "plans_mod.json")
        json.dump(store_json, open(alt, "w"))
        orig, MS.PLANS_FILE = MS.PLANS_FILE, alt
        try:
            r3 = MS.sync_plans(store)
        finally:
            MS.PLANS_FILE = orig
        ok(r3["partitions_written"] == 1, "un plan schimbat rescrie doar partitia lui")


def _dataset(tmp):
    from tests import dataset
    out = os.path.join(tmp, "ds.json")
    dataset.build(ROOT, out)
    os.environ["TESTS_DATASET"] = out
    return json.load(open(out))


def check_candles(tmp):
    print("3. lumanari (bursa simulata)")
    ds = _dataset(tmp)
    sys.path.insert(0, os.path.join(ROOT, "tests", "fakes"))
    import ccxt  # noqa: F401  (cel simulat)
    from adapters.exchanges.venues import CcxtVenues
    opener = lambda eid: CcxtVenues(sys.modules["ccxt"]).open(eid)
    store = LocalStore(os.path.join(tmp, "local"))
    runs = []
    for k in range(6):
        res = MS.sync_exchange_candles(store, "okx", opener, time.monotonic() + (0.3 if k == 0 else 120))
        runs.append(res)
        if all(r["complete"] + r["no_data"] == r["universe"] for r in res.values()):
            break
    last = runs[-1]
    ok(len(runs) >= 2 and runs[0]["1d"]["complete"] < runs[-1]["1d"]["complete"],
       f"prima rulare (buget 0.3 s) partiala ({runs[0]['1d']['complete']}), urmatoarea continua de unde a ramas")
    ok(all(r["complete"] + r["no_data"] == r["universe"] and r["complete"] > 0 for r in last.values()),
       f"tot universul la zi dupa {len(runs)} rulari: " + ", ".join(f"{tf} {r['complete']}/{r['universe']} "
                                                                     f"({r['no_data']} fara date pe bursa)"
                                                                     for tf, r in last.items()))
    files = [store.local_path("candles", n) for n in store.names("candles")]
    dd = [PQ.query("SELECT count(*) n, count(DISTINCT symbol || ':' || ts) u FROM {src}", [f for f in files if f"__{tf}__" in f])[0]
          for tf in ("1d", "4h")]
    ok(all(d["n"] == d["u"] > 0 for d in dd), f"fara dubluri: {[d['n'] for d in dd]} lumanari (1d, 4h)")
    ex = opener("okx")
    ms = ex.parse_timeframe("4h") * 1000
    now = ex.milliseconds()
    sym = sorted(ds["series"])[0]
    want = sum(1 for c in ds["series"][sym] if c[0] + ms <= now)
    have = PQ.query(f"SELECT count(*) n FROM {{src}} WHERE symbol = '{sym}/USDT' AND ts % {ms} = 0",
                    [f for f in files if "__4h__" in f])[0]["n"]
    ok(have == want, f"{sym} 4h: {have} lumanari inchise in memorie, {want} pe bursa")


def check_binance(tmp):
    print("4. binance (arhiva simulata)")
    rows = {}

    def mkzip(rows_):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            z.writestr("x.csv", "\n".join(",".join(str(v) for v in r) for r in rows_))
        return b.getvalue()
    t0 = 1704067200000
    good = mkzip([[t0 + i * 86400000, 1, 2, 0.5, 1.5, 10, 0, 0, 0, 0, 0, 0] for i in range(31)])
    us = mkzip([[(t0 + 40 * 86400000) * 1000, 1, 2, 0.5, 1.5, 10, 0, 0, 0, 0, 0, 0]])
    files = {"data/spot/monthly/klines/AAAUSDT/1d/AAAUSDT-1d-2024-01.zip": good,
             "data/spot/monthly/klines/AAAUSDT/1d/AAAUSDT-1d-2025-02.zip": us}
    orig = (BV._get, BV.usdt_symbols, BV.months)

    def fake_get(url, timeout=60):
        key = url.split(BV.BASE + "/", 1)[-1]
        if key.endswith(".CHECKSUM"):
            data = files[key[:-9]]
            return (hashlib.sha256(data).hexdigest() + "  x.zip").encode()
        return files[key]
    BV._get = fake_get
    BV.usdt_symbols = lambda: ["AAAUSDT"]
    def fake_months(sym, tf):
        return sorted(name for name in files if f"/{tf}/" in name)
    BV.months = fake_months
    try:
        store = LocalStore(os.path.join(tmp, "local"))
        res = MS.sync_binance(store, time.monotonic() + 60)
        ok(res["1d"]["months_added"] == 2, f"luni adaugate: {res['1d']}")
        fl = [store.local_path("binance", n) for n in store.names("binance")]
        mx = PQ.query("SELECT max(ts) m FROM {src}", fl)[0]["m"]
        ok(mx == t0 + 40 * 86400000, "timpii in microsecunde (2025+) normalizati la milisecunde")
        res2 = MS.sync_binance(store, time.monotonic() + 60)
        ok(res2["1d"]["months_added"] == 0, "a doua rulare nu descarca din nou lunile terminate")
        files["data/spot/monthly/klines/AAAUSDT/1d/AAAUSDT-1d-2024-01.zip"] = good + b"x"
        bad_ok = False
        try:
            BV._get = lambda url, timeout=60: (b"0" * 64 if url.endswith(".CHECKSUM") else good + b"x")
            BV.download_month("data/spot/monthly/klines/AAAUSDT/1d/AAAUSDT-1d-2024-01.zip")
        except ValueError:
            bad_ok = True
        ok(bad_ok, "arhiva cu checksum gresit e respinsa")
    finally:
        BV._get, BV.usdt_symbols, BV.months = orig


class _GH(BaseHTTPRequestHandler):
    """API GitHub minimal pentru release-uri (doar ce foloseste ReleaseStore)."""
    rel, assets, nid, calls = {}, {}, [100], []

    def log_message(self, *a):
        pass

    def _send(self, code, obj=None, raw=None):
        body = raw if raw is not None else (json.dumps(obj).encode() if obj is not None else b"")
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        self.calls.append(("GET", p))
        if "/releases/tags/" in p:
            r = self.rel.get(p.rsplit("/", 1)[-1])
            return self._send(200, r) if r else self._send(404, {})
        if p.endswith("/assets"):
            rid = int(p.split("/")[-2])
            page = int(dict(x.split("=") for x in self.path.split("?")[1].split("&")).get("page", 1))
            lst = [{k: v for k, v in a.items() if k != "data"} for a in self.assets.values() if a["rid"] == rid]
            return self._send(200, lst[(page - 1) * 100: page * 100])
        if "/releases/assets/" in p:
            a = self.assets.get(int(p.rsplit("/", 1)[-1]))
            return self._send(200, raw=a["data"]) if a else self._send(404, {})
        self._send(404, {})

    def do_POST(self):
        p = self.path.split("?")[0]
        n = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(n)
        self.calls.append(("POST", p))
        host = f"http://{self.server.server_address[0]}:{self.server.server_address[1]}"
        if p.endswith("/releases"):
            tag = json.loads(data)["tag_name"]
            self.nid[0] += 1
            r = {"id": self.nid[0], "tag_name": tag,
                 "upload_url": f"{host}/uploads/releases/{self.nid[0]}/assets{{?name,label}}"}
            self.rel[tag] = r
            return self._send(201, r)
        if p.startswith("/uploads/releases/"):
            rid = int(p.split("/")[3])
            name = self.path.split("name=")[1].split("&")[0]
            self.nid[0] += 1
            aid = self.nid[0]
            a = {"id": aid, "name": name, "rid": rid, "data": data, "url": f"{host}/repos/o/r/releases/assets/{aid}",
                 "browser_download_url": f"{host}/download/{name}"}
            self.assets[aid] = a
            return self._send(201, {k: v for k, v in a.items() if k != "data"})
        self._send(404, {})

    def do_DELETE(self):
        aid = int(self.path.rsplit("/", 1)[-1])
        self.calls.append(("DELETE", aid))
        self.assets.pop(aid, None)
        self._send(204)

    def do_PATCH(self):
        aid = int(self.path.rsplit("/", 1)[-1])
        n = int(self.headers.get("Content-Length") or 0)
        self.assets[aid]["name"] = json.loads(self.rfile.read(n))["name"]
        self.calls.append(("PATCH", aid))
        self._send(200, {k: v for k, v in self.assets[aid].items() if k != "data"})


def check_release(tmp):
    print("5. release-uri GitHub (API simulat)")
    from adapters.memory.release_store import ReleaseStore
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _GH)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    api = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        _GH.assets.clear()
        _GH.rel.clear()
        st = ReleaseStore("o/r", "t", cache_dir=os.path.join(tmp, "cache1"), api=api)
        r1 = MS.sync_plans(st)
        names = {a["name"] for a in _GH.assets.values()}
        ok("index.json" in names and r1["partitions_written"] == len(names) - 1 and not any(n.endswith(".new") for n in names),
           f"{r1['partitions_written']} partitii + index.json urcate in release-ul {M.DATASETS['plans']}, fara resturi .new")
        n_calls = len(_GH.calls)
        st2 = ReleaseStore("o/r", "t", cache_dir=os.path.join(tmp, "cache2"), api=api)
        r2 = MS.sync_plans(st2)
        posts = [c for c in _GH.calls[n_calls:] if c[0] == "POST" and "/uploads/" in c[1]]
        ok(r2["partitions_written"] == 0 and len(posts) == 1, f"a doua rulare (alt runner, cache gol): 0 partitii, doar indexul")
        nm = next(n for n in sorted(names) if n.endswith(".parquet"))
        rows = st2.read("plans", nm)
        ok(len(rows) == st2.index("plans")[nm]["rows"], f"partitia descarcata are randurile din index ({len(rows)})")
        before = {a["id"] for a in _GH.assets.values() if a["name"] == nm}
        st2.write("plans", nm, rows[:-1] if rows else rows, M.plan_schema(A.FEATURES))
        after = [a for a in _GH.assets.values() if a["name"] == nm]
        ok(len(after) == 1 and after[0]["id"] not in before and not any(a["name"].endswith(".new") for a in _GH.assets.values()),
           "inlocuire: fisierul nou urcat sub nume temporar, vechiul sters, noul redenumit")
    finally:
        srv.shutdown()


def main():
    tmp = tempfile.mkdtemp(prefix="memorie-")
    orig_status = MS.STATUS_FILE
    MS.STATUS_FILE = os.path.join(tmp, "memory_status.json")
    try:
        plans = json.load(open(MS.PLANS_FILE))["plans"]
        check_core(plans)
        check_plans(tmp, plans)
        check_candles(tmp)
        check_binance(tmp)
        check_release(tmp)
    finally:
        MS.STATUS_FILE = orig_status
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nDEPOZITUL DE MEMORIE: " + ("RESPINS - " + "; ".join(FAILS) if FAILS else "TRECUT"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
