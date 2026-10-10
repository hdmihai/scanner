# -*- coding: utf-8 -*-
"""dashboard.sections.memory - Depozitul de memorie al agentului (data/memory_status.json, scris de memory_sync.py)."""

import html as _html
import time

NAMES = {"plans": "Planuri (live + backtest, cu caracteristici)", "candles": "Lumanari bursa principala (1d, 4h)",
         "binance": "Arhiva Binance (1d, 4h)"}


def _d(ts, ms=False):
    if ts is None:
        return "-"
    try:
        return time.strftime("%Y-%m-%d", time.gmtime(ts / 1000.0 if ms else ts))
    except (TypeError, ValueError, OverflowError):
        return "-"


def _mb(b):
    b = b or 0
    return f"{b / 1e9:.2f} GB" if b >= 1e9 else f"{b / 1e6:.1f} MB"


def render_memory(st, repo=None):
    if not st:
        return ('<p class="mem-note">Depozitul nu a rulat inca. Actions &rarr; <strong>Memorie agent</strong> &rarr; '
                'Run workflow (ruleaza si zilnic, automat).</p>')
    rows = []
    dsets = st.get("datasets") or {}
    for ds in [d for d in ("plans", "candles", "binance") if d in dsets] + [d for d in dsets if d not in NAMES]:
        v = dsets[ds]
        ms = ds != "plans"
        cov = "; ".join(f"{k}: {c.get('with_data', 0)} simboluri" for k, c in sorted((v.get("coverage") or {}).items()))
        tag = (st.get("releases") or {}).get(ds)
        link = (f' <a href="https://github.com/{_html.escape(repo)}/releases/tag/{_html.escape(tag)}">release</a>'
                if repo and tag and st.get("store") == "github-releases" else "")
        rows.append(f'<tr><td>{_html.escape(NAMES.get(ds, ds))}{link}</td><td>{v.get("rows", 0):,}</td>'
                    f'<td>{_mb(v.get("bytes"))}</td><td>{v.get("partitions", 0)}</td>'
                    f'<td>{_d(v.get("min_ts"), ms)} &ndash; {_d(v.get("max_ts"), ms)}</td>'
                    f'<td>{_html.escape(cov) or "-"}</td></tr>')
    pr = st.get("probe") or {}
    bv, ex = pr.get("binance_vision") or {}, pr.get("exchange") or {}
    run = st.get("last_run") or {}
    errs = run.get("errors") or []
    probe = (f'runner: {pr.get("cpu", "?")} CPU, {pr.get("ram_gb", "?")} GB RAM, {pr.get("disk_free_gb", "?")} GB disc liber'
             f' &middot; bursa {_html.escape(str(ex.get("id", "?")))}: {"raspunde" if ex.get("ok") else "NU raspunde"}'
             f' &middot; arhiva Binance: {"accesibila" if bv.get("ok") else "inaccesibila din runner"}'
             + (f' ({_html.escape(str(bv.get("detail"))[:90])})' if not bv.get("ok") and bv.get("detail") else ""))
    done = "; ".join(_done(k, v) for k, v in (run.get("done") or {}).items())
    where = ("release-uri GitHub (gratuit, fara limita totala; fisiere sub 2 GiB, 1000 pe release)"
             if st.get("store") == "github-releases" else _html.escape(str(st.get("store"))))
    return (f'<p class="mem-note" style="margin:0 0 8px;">Stocare: {where}'
            f' &middot; format Parquet (DuckDB) &middot; ultima rulare {_html.escape(str(run.get("finished", "-")))} '
            f'in {run.get("duration_s", "?")} s din bugetul de {run.get("budget_min", "?")} min</p>'
            '<div class="as-table-wrap"><table class="mem-table"><tr><th>Set de date</th><th>Randuri</th><th>Marime</th>'
            '<th>Partitii</th><th>Perioada</th><th>Acoperire</th></tr>' + "".join(rows) + '</table></div>'
            f'<p class="mem-note">{probe}</p>'
            + (f'<p class="mem-note">Ultima rulare: {done}</p>' if done else "")
            + (f'<p class="plan-conflict" style="margin-top:8px;">Erori: {_html.escape("; ".join(errs))[:400]}</p>' if errs else ""))


def _done(name, v):
    v = v or {}
    if v.get("skipped"):
        return f"{_html.escape(name)}: sarit ({_html.escape(str(v['skipped']))})"
    if name == "plans":
        return f"planuri: {v.get('plans', 0):,} oglindite, {v.get('partitions_written', 0)} partitii rescrise"
    parts = []
    for tf, d in sorted(v.items()):
        if not isinstance(d, dict):
            continue
        if "months_added" in d:
            parts.append(f"{tf} +{d.get('months_added', 0)} luni ({d.get('covered', 0)}/{d.get('universe', 0)} simboluri)")
        else:
            parts.append(f"{tf} {d.get('complete', 0)}/{d.get('universe', 0)} la zi, +{d.get('rows_added', 0):,} lumanari"
                         + (f", {d['no_data']} fara date" if d.get("no_data") else ""))
    return f"{_html.escape(name)}: " + ", ".join(parts)
