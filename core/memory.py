# -*- coding: utf-8 -*-
"""core.memory - depozitul de memorie al agentului: schema, partitii, index (NUCLEU, fara I/O).

DE CE UN DEPOZIT SEPARAT
------------------------
Memoria agentului traia toata in data/plans.json, in git. GitHub refuza fisierele peste 100 MiB
si recomanda repo-uri sub 1-5 GB, deci memoria nu putea creste dincolo de ~30 de simboluri si cativa
ani: un backtest pe tot universul bursei (~15x mai multe simboluri) sau istoricul complet de lumanari
nu incap. Depozitul tine datele mari in fisiere Parquet (coloane comprimate), in afara git-ului,
partitionate pe an; git-ul pastreaza doar starea curenta si un rezumat (data/memory_status.json).

UNDE: implicit release-uri GitHub ale repo-ului (adapters/memory/release_store.py) - gratuit, fara
cheie noua (GITHUB_TOKEN al workflow-ului), fiecare fisier < 2 GiB, cel mult 1000 de fisiere pe
release, fara limita de marime totala sau de trafic. Un set de date = un release (tag DATASETS[x]),
cu partitii anuale, deci limita de 1000 de fisiere nu se atinge. Citirea: DuckDB, local sau direct
prin HTTP cu cereri partiale (doar coloanele si blocurile cerute).

SETURI DE DATE
  plans       - fiecare plan (live si backtest): niveluri, stare, R, decizie si VECTORUL DE
                CARACTERISTICI pe care invata agentul (fara textele evidentelor, care sunt afisare);
  candles     - lumanarile bursei principale pentru TOT universul ei USDT spot (nu doar cele 30 de
                simboluri scanate), pe 4h si 1d, de la listare;
  binance     - arhiva publica Binance (data.binance.vision) pe 4h si 1d, cand e accesibila din runner.

Nucleul defineste ce se stocheaza si cum se imparte; adaptoarele fac I/O.
"""

import hashlib
import json
import time

SCHEMA_VERSION = 1

# tag-ul release-ului pentru fiecare set de date
DATASETS = {
    "plans": "memory-plans",
    "candles": "memory-candles",
    "binance": "memory-binance",
}

# campurile scalare ale unui plan pastrate in memorie (restul - evidente text, vecini - sunt afisare)
PLAN_FIELDS = (
    ("id", "BIGINT"), ("symbol", "VARCHAR"), ("direction", "VARCHAR"), ("source", "VARCHAR"),
    ("geometry", "VARCHAR"), ("fv", "VARCHAR"), ("created_ts", "DOUBLE"), ("entered_ts", "DOUBLE"),
    ("closed_ts", "DOUBLE"), ("state", "VARCHAR"), ("entry", "DOUBLE"), ("sl", "DOUBLE"), ("tp1", "DOUBLE"),
    ("tp2", "DOUBLE"), ("risk", "DOUBLE"), ("planned_r_tp2", "DOUBLE"), ("gross_r", "DOUBLE"),
    ("realized_r", "DOUBLE"), ("score_at_entry", "DOUBLE"), ("persistence_at_entry", "DOUBLE"),
    ("decision_action", "VARCHAR"), ("decision_mode", "VARCHAR"), ("agent_prob", "DOUBLE"),
    ("bar_seconds", "DOUBLE"),
)
FEATURE_PREFIX = "f_"
CANDLE_FIELDS = (("symbol", "VARCHAR"), ("ts", "BIGINT"), ("open", "DOUBLE"), ("high", "DOUBLE"),
                 ("low", "DOUBLE"), ("close", "DOUBLE"), ("volume", "DOUBLE"))


def _year(ts):
    try:
        return time.gmtime(float(ts)).tm_year
    except (TypeError, ValueError, OverflowError):
        return 1970


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


# ------------------------------------------------------------------ planuri

def plan_schema(feature_keys):
    return list(PLAN_FIELDS) + [(FEATURE_PREFIX + k, "DOUBLE") for k in feature_keys]


def plan_row(p, feature_keys):
    """Un plan ca rand plat: campurile scalare + caracteristicile (None = lipsa, nu 0 - o caracteristica
    absenta trebuie sa ramana recognoscibila ca absenta)."""
    dec = p.get("decision") or {}
    comp = p.get("components") if isinstance(p.get("components"), dict) else {}
    row = {
        "id": p.get("id"), "symbol": p.get("symbol"), "direction": p.get("direction"),
        "source": p.get("source") or "live", "geometry": p.get("geometry"), "fv": p.get("fv"),
        "created_ts": _num(p.get("created_ts")), "entered_ts": _num(p.get("entered_ts")),
        "closed_ts": _num(p.get("closed_ts")), "state": p.get("state"),
        "entry": _num(p.get("entry")), "sl": _num(p.get("sl")), "tp1": _num(p.get("tp1")),
        "tp2": _num(p.get("tp2")), "risk": _num(p.get("risk")), "planned_r_tp2": _num(p.get("planned_r_tp2")),
        "gross_r": _num(p.get("gross_r")), "realized_r": _num(p.get("realized_r")),
        "score_at_entry": _num(p.get("score_at_entry")), "persistence_at_entry": _num(p.get("persistence_at_entry")),
        "decision_action": dec.get("action"), "decision_mode": dec.get("mode"),
        "agent_prob": _num(dec.get("agent_prob")), "bar_seconds": _num(p.get("bar_seconds")),
    }
    for k in feature_keys:
        row[FEATURE_PREFIX + k] = _num(comp.get(k))
    return row


def row_to_plan(row):
    """Inversul lui plan_row: un plan pe care agentul il poate invata (components = caracteristicile)."""
    p = {k: row.get(k) for k, _ in PLAN_FIELDS if row.get(k) is not None}
    p["decision"] = {"action": row.get("decision_action"), "mode": row.get("decision_mode"),
                     "agent_prob": row.get("agent_prob")}
    p["components"] = {k[len(FEATURE_PREFIX):]: v for k, v in row.items()
                       if k.startswith(FEATURE_PREFIX) and v is not None}
    return p


def plan_key(row):
    """Identitatea stabila a unui plan (id-urile se renumeroteaza la integrarea backtest-ului)."""
    return (row.get("symbol"), row.get("direction"), row.get("source"),
            round(row.get("created_ts") or 0, 3), round(row.get("entry") or 0, 10))


def plan_partition(row):
    fam = "-".join(str(row.get("geometry") or "v0").split("-")[:2])
    return f"plans__{row.get('source') or 'live'}__{fam}__{_year(row.get('created_ts'))}.parquet"


# ------------------------------------------------------------------ lumanari

def candle_partition(dataset, exchange, tf, ts_ms):
    return f"{dataset}__{exchange}__{tf}__{_year((ts_ms or 0) / 1000.0)}.parquet"


def candle_rows(symbol, candles):
    out = []
    for c in candles or []:
        try:
            out.append({"symbol": symbol, "ts": int(c[0]), "open": float(c[1]), "high": float(c[2]),
                        "low": float(c[3]), "close": float(c[4]), "volume": float(c[5] or 0.0)})
        except (TypeError, ValueError, IndexError):
            continue
    return out


def candle_key(row):
    return (row.get("symbol"), row.get("ts"))


def valid_candle(row):
    """O lumanare imposibila (high sub low, preturi nepozitive, open/close in afara intervalului) nu intra
    in memorie - acelasi principiu ca la auditul planurilor."""
    h, l, o, c = row.get("high"), row.get("low"), row.get("open"), row.get("close")
    return (all(isinstance(x, float) and x > 0 for x in (h, l, o, c)) and h >= l
            and l <= o <= h and l <= c <= h and isinstance(row.get("ts"), int) and row["ts"] > 0)


# ------------------------------------------------------------------ partitii si index

def group(rows, part_fn):
    out = {}
    for r in rows:
        out.setdefault(part_fn(r), []).append(r)
    return out


def merge_rows(old, new, key_fn):
    """Uniunea fara dubluri; la aceeasi cheie castiga randul nou (un plan inchis intre timp, o lumanare
    revizuita). Ordinea: dupa cheie, ca hash-ul sa nu depinda de ordinea de sosire."""
    seen = {}
    for r in list(old or []) + list(new or []):
        seen[key_fn(r)] = r
    return [seen[k] for k in sorted(seen, key=_sort_key)]


def _sort_key(key):
    return tuple("" if part is None else part for part in key)


def content_hash(rows):
    h = hashlib.sha256()
    for r in rows:
        h.update(json.dumps(r, sort_keys=True, separators=(",", ":"), default=str).encode())
    return h.hexdigest()


def changed_partitions(groups, index, key_fn, existing=None):
    """Partitiile al caror continut difera de cel din index (hash-ul randurilor sortate) sau care
    lipsesc efectiv din depozit (`existing`), desi indexul le listeaza."""
    out = {}
    for name, rows in groups.items():
        rows = merge_rows([], rows, key_fn)
        hsh = content_hash(rows)
        if (index.get(name) or {}).get("hash") != hsh or (existing is not None and name not in existing):
            out[name] = (rows, hsh)
    return out


def index_entry(rows, hsh, nbytes, ts_field, now):
    vals = [r.get(ts_field) for r in rows if r.get(ts_field) is not None]
    return {"rows": len(rows), "hash": hsh, "bytes": nbytes, "updated": now,
            "min_ts": min(vals) if vals else None, "max_ts": max(vals) if vals else None,
            "symbols": len({r.get("symbol") for r in rows})}


def summary(index):
    """Rezumatul unui set de date pentru dashboard (fara hash-uri)."""
    parts = index or {}
    rows = sum(v.get("rows", 0) for v in parts.values())
    nbytes = sum(v.get("bytes", 0) for v in parts.values())
    mins = [v["min_ts"] for v in parts.values() if v.get("min_ts") is not None]
    maxs = [v["max_ts"] for v in parts.values() if v.get("max_ts") is not None]
    return {"partitions": len(parts), "rows": rows, "bytes": nbytes,
            "min_ts": min(mins) if mins else None, "max_ts": max(maxs) if maxs else None}


def stale_first(symbols, last_ts):
    """Ordinea de actualizare: simbolurile fara date intai, apoi cele mai vechi - un job cu buget de
    timp avanseaza mereu pe cele mai ramase in urma, iar rularile urmatoare continua de unde a ramas."""
    return sorted(symbols, key=lambda s: (last_ts.get(s) is not None, last_ts.get(s) or 0, s))
