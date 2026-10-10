#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""memory_sync.py - jobul depozitului de memorie (workflow-ul .github/workflows/memory.yml).

La fiecare rulare, cu buget de timp (MEMORY_BUDGET_MIN, implicit 45 de minute):
  0. SONDA: ce poate face runner-ul (CPU, RAM, disc) si ce surse raspund de aici (bursa principala,
     arhiva Binance, API-ul GitHub) - salvat in data/memory_status.json, vizibil pe dashboard;
  1. PLANURI: oglinda memoriei de planuri (data/plans.json) in Parquet - doar partitiile schimbate;
  2. LUMANARI: istoricul complet 1d si 4h pentru TOT universul USDT spot al bursei principale, de la
     listare, incremental (simbolurile ramase in urma intai; rularea urmatoare continua);
  3. BINANCE: arhiva publica data.binance.vision (1d, 4h), luna cu luna, verificata cu checksum - doar
     daca sonda o gaseste accesibila din runner.
Depozitul: release-urile GitHub ale repo-ului (GITHUB_TOKEN + GITHUB_REPOSITORY, setate de workflow),
altfel un dosar local (MEMORY_LOCAL_DIR, implicit memory_local/ - pentru teste). Nimic din acest job
nu atinge planurile, agentul sau deciziile: scrie doar in depozit si in data/memory_status.json.

Rulare manuala: python memory_sync.py
"""

import json
import os
import shutil
import sys
import time
import traceback

from adapters.memory import binance_vision as BV
from adapters.memory import market_history as MH
from adapters.memory import parquet as PQ
from core import agent as agent_core
from core import memory as M

STATUS_FILE = os.path.join("data", "memory_status.json")
PLANS_FILE = os.path.join("data", "plans.json")
EXCHANGES_FILE = os.path.join("data", "exchanges.json")
TFS = ("1d", "4h")
FLUSH_ROWS = 300000
FLUSH_SECONDS = 600


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(path + ".tmp", path)


def make_store():
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if repo and token and os.environ.get("MEMORY_STORE", "release") == "release":
        from adapters.memory.release_store import ReleaseStore
        return ReleaseStore(repo, token, cache_dir=os.environ.get("MEMORY_CACHE_DIR")), "github-releases", repo
    from adapters.memory.local_store import LocalStore
    root = os.environ.get("MEMORY_LOCAL_DIR", "memory_local")
    return LocalStore(root), "local", root


# --------------------------------------------------------------------- 0. sonda

def probe(exchange_id, open_exchange):
    out = {"cpu": os.cpu_count(), "disk_free_gb": round(shutil.disk_usage(".").free / 1e9, 1)}
    try:
        with open("/proc/meminfo") as f:
            kb = int(next(l for l in f if l.startswith("MemTotal")).split()[1])
        out["ram_gb"] = round(kb / 1e6, 1)
    except Exception:
        pass
    t = time.monotonic()
    ok, det = BV.probe()
    out["binance_vision"] = {"ok": ok, "detail": det, "s": round(time.monotonic() - t, 1)}
    t = time.monotonic()
    try:
        ex = open_exchange(exchange_id)
        ex.fetch_ohlcv("BTC/USDT", timeframe="1d", limit=3)
        out["exchange"] = {"id": exchange_id, "ok": True, "s": round(time.monotonic() - t, 1)}
    except Exception as e:
        out["exchange"] = {"id": exchange_id, "ok": False, "detail": str(e)[:160]}
    return out


# --------------------------------------------------------------------- 1. planuri

def sync_plans(store):
    plans = (_load(PLANS_FILE, {}) or {}).get("plans") or []
    feats = list(agent_core.FEATURES)
    rows = [M.plan_row(p, feats) for p in plans]
    groups = M.group(rows, M.plan_partition)
    idx = store.index("plans")
    existing = store.names("plans")
    for gone in [n for n in idx if n.endswith(".parquet") and n not in groups]:
        groups[gone] = []                         # oglinda: o partitie disparuta din plans.json se goleste
    changed = M.changed_partitions(groups, idx, M.plan_key, existing)
    now = time.time()
    for name, (prows, hsh) in sorted(changed.items()):
        n = store.write("plans", name, prows, M.plan_schema(feats))
        idx[name] = M.index_entry(prows, hsh, n, "created_ts", now)
    idx["_meta"] = {"schema": M.SCHEMA_VERSION, "features": feats, "updated": now}
    store.save_index("plans", idx)
    return {"plans": len(rows), "partitions_written": len(changed)}


# --------------------------------------------------------------------- 2-3. lumanari

class CandleWriter:
    """Acumuleaza lumanari noi pe partitii si le uneste periodic cu cele publicate (DuckDB). Acoperirea
    per simbol se marcheaza abia DUPA ce partitia a fost publicata - o rulare oprita la jumatate reia."""

    def __init__(self, store, dataset):
        self.store, self.ds = store, dataset
        self.idx = store.index(dataset)
        self.cov = self.idx.setdefault("_coverage", {})
        self.buf, self.pending, self.rows, self.last_flush = {}, {}, 0, time.monotonic()
        self.written = 0

    def add(self, cov_key, cov_sym, symbol, prefix, tf, candles, mark):
        rows = [r for r in M.candle_rows(symbol, candles) if M.valid_candle(r)]
        for r in rows:
            self.buf.setdefault(M.candle_partition(self.ds, prefix, tf, r["ts"]), []).append(r)
        self.rows += len(rows)
        self.pending.setdefault(cov_key, {})[cov_sym] = mark
        if self.rows >= FLUSH_ROWS or time.monotonic() - self.last_flush > FLUSH_SECONDS:
            self.flush()
        return len(rows)

    def flush(self):
        now = time.time()
        for name, rows in sorted(self.buf.items()):
            work = self.store.cache_path(self.ds, name)
            old = self.store.local_path(self.ds, name)
            PQ.merge(old, rows, list(M.CANDLE_FIELDS), ("symbol", "ts"), work)
            nbytes = self.store.put(self.ds, name, work)
            n, ns, lo, hi, _per = PQ.stats(work)
            self.idx[name] = {"rows": n, "symbols": ns, "min_ts": lo, "max_ts": hi, "bytes": nbytes, "updated": now}
            os.remove(work)
            self.written += 1
        for k, d in self.pending.items():
            self.cov.setdefault(k, {}).update(d)
        self.store.save_index(self.ds, self.idx)
        self.buf, self.pending, self.rows, self.last_flush = {}, {}, 0, time.monotonic()


def sync_exchange_candles(store, exchange_id, open_exchange, deadline):
    ex = open_exchange(exchange_id)
    syms = MH.tradable_usdt_spot(ex.load_markets())
    w = CandleWriter(store, "candles")
    done = {}
    now_ms = ex.milliseconds()
    for tf in TFS:
        key = f"{exchange_id}:{tf}"
        cov = w.cov.get(key) or {}
        ms = ex.parse_timeframe(tf) * 1000
        n_sym = n_rows = 0
        for sym in M.stale_first(syms, cov):
            if time.monotonic() > deadline:
                break
            last = cov.get(sym)
            if last is not None and last >= now_ms - 2 * ms:
                continue                              # la zi
            if last is not None and last < 0:
                continue                              # bursa nu are date pe acest timeframe
            since = (last + ms) if last else MH.listing_ts(ex, sym, tf)
            if since is None:
                w.pending.setdefault(key, {})[sym] = -1
                continue
            candles = MH.fetch_since(ex, sym, tf, since, deadline)
            if candles:
                n_rows += w.add(key, sym, sym, exchange_id, tf, candles, candles[-1][0])
                n_sym += 1
        w.flush()
        cov = w.cov.get(key) or {}
        done[tf] = {"symbols_updated": n_sym, "rows_added": n_rows, "universe": len(syms),
                    "complete": sum(1 for s in syms if (cov.get(s) or 0) >= now_ms - 2 * ms),
                    "covered": sum(1 for s in syms if (cov.get(s) or 0) > 0),
                    "no_data": sum(1 for s in syms if cov.get(s) == -1)}
    return done


def sync_binance(store, deadline):
    w = CandleWriter(store, "binance")
    syms = BV.usdt_symbols()
    done = {}
    for tf in TFS:
        key = f"binance:{tf}"
        cov = w.cov.get(key) or {}
        n_months = n_rows = 0
        # simbolurile fara nimic intai, apoi cele cu cea mai veche luna terminata
        order = sorted(syms, key=lambda s: (s in cov, cov.get(s) or "", s))
        g = time.gmtime()
        last_month = f"{g.tm_year - (g.tm_mon == 1)}-{(g.tm_mon - 2) % 12 + 1:02d}"   # ultima luna incheiata
        for s in order:
            if time.monotonic() > deadline:
                break
            if (cov.get(s) or "") >= last_month:
                continue                              # la zi: arhiva lunara nu are nimic mai nou
            try:
                keys = [k for k in BV.months(s, tf) if (BV.month_of(k) or "") > (cov.get(s) or "")]
            except Exception as e:
                print(f"  [!] binance {s} {tf}: {str(e)[:100]}")
                continue
            if not keys:
                w.pending.setdefault(key, {})[s] = cov.get(s) or "0000-00"
                continue
            sym = s[:-4] + "/USDT"
            for k in keys:
                if time.monotonic() > deadline:
                    break
                try:
                    rows = BV.download_month(k)
                except Exception as e:
                    print(f"  [!] {k}: {str(e)[:100]}")
                    break                              # luna lipsa/corupta: reia de aici data viitoare
                n_rows += w.add(key, s, sym, "spot", tf, rows, BV.month_of(k))
                n_months += 1
        w.flush()
        cov = w.cov.get(key) or {}
        done[tf] = {"months_added": n_months, "rows_added": n_rows, "universe": len(syms),
                    "covered": sum(1 for s in syms if cov.get(s))}
    return done


# --------------------------------------------------------------------- statusul

def dataset_status(store, ds):
    idx = store.index(ds)
    parts = {k: v for k, v in idx.items() if k.endswith(".parquet")}
    st = M.summary(parts)
    st["coverage"] = {k: {"symbols": len(v), "with_data": sum(1 for x in v.values() if x not in (-1, None))}
                      for k, v in (idx.get("_coverage") or {}).items()}
    return st


def main():
    t0 = time.monotonic()
    budget = float(os.environ.get("MEMORY_BUDGET_MIN", "45")) * 60
    deadline = t0 + budget
    wanted = [d.strip() for d in os.environ.get("MEMORY_DATASETS", "plans,candles,binance").split(",") if d.strip()]
    store, kind, where = make_store()
    exchange_id = (_load(EXCHANGES_FILE, {}) or {}).get("used") or "okx"

    def open_exchange(eid):
        import ccxt
        from adapters.exchanges.venues import CcxtVenues
        ex = CcxtVenues(ccxt).open(eid)
        if ex is None:
            raise RuntimeError(f"bursa {eid} nu exista in ccxt")
        return ex

    status = _load(STATUS_FILE, {}) or {}
    status.update({"store": kind, "where": where, "schema": M.SCHEMA_VERSION,
                   "releases": {d: M.DATASETS[d] for d in M.DATASETS}})
    run = {"started": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "budget_min": budget / 60,
           "done": {}, "errors": []}
    try:
        status["probe"] = probe(exchange_id, open_exchange)
    except Exception as e:
        run["errors"].append(f"sonda: {e}")
    print("SONDA:", json.dumps(status.get("probe"), ensure_ascii=False))

    steps = [("plans", lambda: sync_plans(store)),
             ("candles", lambda: sync_exchange_candles(store, exchange_id, open_exchange, deadline)),
             ("binance", lambda: (sync_binance(store, deadline)
                                  if (status.get("probe") or {}).get("binance_vision", {}).get("ok")
                                  else {"skipped": "arhiva Binance inaccesibila din runner"}))]
    for name, fn in steps:
        if name not in wanted:
            continue
        if time.monotonic() > deadline and name != "plans":
            run["done"][name] = {"skipped": "buget de timp epuizat"}
            continue
        try:
            run["done"][name] = fn()
            print(f"[{name}] {json.dumps(run['done'][name], ensure_ascii=False)}")
        except Exception as e:
            traceback.print_exc()
            run["errors"].append(f"{name}: {str(e)[:200]}")
    status["datasets"] = {}
    for ds in M.DATASETS:
        try:
            status["datasets"][ds] = dataset_status(store, ds)
        except Exception as e:
            run["errors"].append(f"status {ds}: {str(e)[:120]}")
    run["duration_s"] = round(time.monotonic() - t0, 1)
    run["finished"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    status["last_run"] = run
    status["history"] = ((status.get("history") or []) + [{
        "when": run["finished"], "s": run["duration_s"], "errors": len(run["errors"]),
        "rows": {d: (v or {}).get("rows") for d, v in status["datasets"].items()}}])[-60:]
    _save(STATUS_FILE, status)
    print(f"Depozit ({kind}): " + ", ".join(f"{d} {v.get('rows', 0)} randuri / {v.get('bytes', 0) / 1e6:.1f} MB"
                                            for d, v in status["datasets"].items()))
    return 1 if run["errors"] and not any(run["done"].values()) else 0


def query(dataset, sql, store=None):
    """Interogare SQL peste un set de date din depozit (pentru consumatori: backtest pe tot universul,
    antrenarea agentului pe memoria completa). In `sql`, {src} = toate partitiile setului de date."""
    store = store or make_store()[0]
    files = [p for p in (store.local_path(dataset, n) for n in sorted(store.names(dataset))) if p]
    return PQ.query(sql, files) if files else []


if __name__ == "__main__":
    sys.exit(main())
