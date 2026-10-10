# -*- coding: utf-8 -*-
"""adapters.memory.parquet - scrierea si citirea partitiilor Parquet cu DuckDB (o singura dependinta:
`duckdb`, fara pandas/pyarrow). Randurile trec printr-un fisier NDJSON temporar: DuckDB il citeste
vectorizat, de ordinul milioanelor de randuri pe secunda, fata de INSERT rand cu rand."""

import json
import os
import tempfile


def _duckdb():
    import duckdb                      # importat la nevoie: scanarea orara nu are nevoie de el
    return duckdb


def write(path, rows, schema):
    """Scrie `rows` (dict-uri) in `path` ca Parquet ZSTD, cu tipurile din `schema` [(coloana, tip SQL)].
    Intoarce marimea fisierului."""
    duckdb = _duckdb()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    cols = [c for c, _ in schema]
    fd, tmp = tempfile.mkstemp(suffix=".ndjson")
    try:
        with os.fdopen(fd, "w") as f:
            for r in rows:
                f.write(json.dumps({c: r.get(c) for c in cols}, separators=(",", ":")) + "\n")
        types = ", ".join(f"'{c}': '{t}'" for c, t in schema)
        part = path + ".part"
        con = duckdb.connect()
        if rows:
            con.execute(f"COPY (SELECT {', '.join(_q(c) for c in cols)} FROM read_json('{_esc(tmp)}', "
                        f"format='newline_delimited', columns={{{types}}})) TO '{_esc(part)}' "
                        "(FORMAT PARQUET, COMPRESSION ZSTD)")
        else:
            con.execute(f"COPY (SELECT {', '.join(f'CAST(NULL AS {t}) AS {_q(c)}' for c, t in schema)} "
                        f"WHERE false) TO '{_esc(part)}' (FORMAT PARQUET)")
        con.close()
        os.replace(part, path)
        return os.path.getsize(path)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def read(path):
    """Randurile unui fisier Parquet ca dict-uri ([] daca fisierul lipseste)."""
    if not path or not os.path.exists(path):
        return []
    con = _duckdb().connect()
    cur = con.execute(f"SELECT * FROM read_parquet('{_esc(path)}')")
    cols = [d[0] for d in cur.description]
    out = [dict(zip(cols, row)) for row in cur.fetchall()]
    con.close()
    return out


def query(sql, sources):
    """Interogare SQL peste partitii: in `sql`, {src} e inlocuit cu lista de fisiere sau URL-uri.
    Pentru URL-uri, DuckDB (extensia httpfs) citeste doar coloanele si blocurile necesare."""
    duckdb = _duckdb()
    con = duckdb.connect()
    if any(str(s).startswith("http") for s in sources):
        con.execute("INSTALL httpfs; LOAD httpfs;")
    lst = "[" + ", ".join(f"'{_esc(s)}'" for s in sources) + "]"
    cur = con.execute(sql.replace("{src}", f"read_parquet({lst}, union_by_name=true)"))
    cols = [d[0] for d in cur.description]
    out = [dict(zip(cols, row)) for row in cur.fetchall()]
    con.close()
    return out


def _q(c):
    return '"' + c.replace('"', '""') + '"'


def _esc(s):
    return str(s).replace("'", "''")


def merge(old_path, new_rows, schema, key_cols, out_path, order_cols=None):
    """Uneste o partitie existenta cu randuri noi, in DuckDB (fara a incarca totul in Python): la
    aceeasi cheie castiga randul NOU (core.memory.merge_rows, aceeasi regula). Intoarce (randuri, octeti)."""
    duckdb = _duckdb()
    tmp_new = out_path + ".new.parquet"
    write(tmp_new, new_rows, schema)
    cols = ", ".join(_q(c) for c, _ in schema)
    keys = ", ".join(_q(c) for c in key_cols)
    order = ", ".join(_q(c) for c in (order_cols or key_cols))
    srcs = [f"SELECT {cols}, 1 AS __src FROM read_parquet('{_esc(tmp_new)}')"]
    if old_path and os.path.exists(old_path):
        srcs.append(f"SELECT {cols}, 0 AS __src FROM read_parquet('{_esc(old_path)}', union_by_name=true)")
    part = out_path + ".part"
    con = duckdb.connect()
    con.execute(f"COPY (SELECT {cols} FROM ({' UNION ALL BY NAME '.join(srcs)}) "
                f"QUALIFY row_number() OVER (PARTITION BY {keys} ORDER BY __src DESC) = 1 ORDER BY {order}) "
                f"TO '{_esc(part)}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    n = con.execute(f"SELECT count(*) FROM read_parquet('{_esc(part)}')").fetchone()[0]
    con.close()
    os.remove(tmp_new)
    os.replace(part, out_path)
    return n, os.path.getsize(out_path)


def stats(path, ts_col="ts"):
    """(randuri, simboluri, min ts, max ts, {simbol: max ts}) pentru indexul unei partitii de lumanari."""
    con = _duckdb().connect()
    src = f"read_parquet('{_esc(path)}')"
    n, ns, lo, hi = con.execute(f"SELECT count(*), count(DISTINCT symbol), min({ts_col}), max({ts_col}) FROM {src}").fetchone()
    per = dict(con.execute(f"SELECT symbol, max({ts_col}) FROM {src} GROUP BY symbol").fetchall())
    con.close()
    return n, ns, lo, hi, per
