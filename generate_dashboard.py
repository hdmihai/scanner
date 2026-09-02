#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_dashboard.py
======================
Citeste data/scan_history.json, data/weights.json si data/latest_chart.json
(scrise de crypto_ai_scanner.py) si genereaza docs/index.html - o pagina
statica, fara javascript extern, cu grafic candlestick (SVG desenat direct
in Python), planul AI curent, structura/Fibonacci si diagnosticul modelului.

Ruleaza DUPA crypto_ai_scanner.py:
    python3 crypto_ai_scanner.py && python3 generate_dashboard.py

GitHub Pages serveste automat continutul din docs/, daca activezi din
Settings -> Pages -> "Deploy from a branch" -> branch "main" -> folder "/docs".

De ce SVG generat pe server si nu o librarie JS de grafice? Ca sa nu
depinda de niciun CDN extern - pagina se incarca instant si functioneaza
chiar si offline, o data descarcata.
"""

import json
import os
from datetime import datetime, timezone

DATA_DIR = "data"
DOCS_DIR = "docs"
HISTORY_FILE = os.path.join(DATA_DIR, "scan_history.json")
WEIGHTS_FILE = os.path.join(DATA_DIR, "weights.json")
WEIGHTS_HISTORY_FILE = os.path.join(DATA_DIR, "weights_history.json")
CHART_FILE = os.path.join(DATA_DIR, "latest_chart.json")
OUTPUT_FILE = os.path.join(DOCS_DIR, "index.html")

MIN_SAMPLES_FOR_VALIDATION = 100


TOKEN_METADATA_FILE = os.path.join(DATA_DIR, "token_metadata.json")


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r") as f:
        return json.load(f)


def fmt_price(v):
    if v is None:
        return "-"
    if v >= 100:
        return f"{v:,.2f}"
    if v >= 1:
        return f"{v:.4f}"
    return f"{v:.6f}"


def compute_model_health(history, min_samples=MIN_SAMPLES_FOR_VALIDATION):
    hits = misses = 0
    for scan in history:
        for r in scan.get("results", []):
            outcome = r.get("outcome")
            if outcome == "hit":
                hits += 1
            elif outcome == "miss":
                misses += 1
    total = hits + misses
    hit_rate = round(100 * hits / total, 1) if total else None
    health = min(round(100 * total / min_samples), 100) if min_samples else 0
    if total == 0:
        status = "INSUFFICIENT DATA"
    elif total < min_samples:
        status = "DEVELOPING"
    else:
        status = "VALIDATED"
    return {"evaluated": total, "hits": hits, "misses": misses,
            "hit_rate": hit_rate, "health": health, "status": status,
            "min_samples": min_samples}


def get_session_info():
    now = datetime.now(timezone.utc)
    hour = now.hour + now.minute / 60
    windows = [("Tokyo", 0, 9), ("London", 7, 16), ("New York", 13, 22)]
    active = [name for name, s, e in windows if s <= hour < e]
    return {"utc_time": now.strftime("%H:%M UTC"), "active": active or ["-"]}


# ------------------------------- GRAFIC SVG --------------------------------

def render_svg_chart(chart, width=680, height=280, pad=16):
    if not chart or not chart.get("candles"):
        return ('<div class="chart-empty">Fara date de grafic inca &mdash; '
                'ruleaza scanerul macar o data.</div>')

    candles = chart["candles"]
    ema20 = chart.get("ema20") or []
    ema50 = chart.get("ema50") or []

    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    values = highs + lows + [v for v in ema20 if v is not None] + [v for v in ema50 if v is not None]
    vmax, vmin = max(values), min(values)
    vrange = (vmax - vmin) or (vmax * 0.01 or 1)

    n = len(candles)
    plot_w = width - 2 * pad
    plot_h = height - 2 * pad
    step = plot_w / n
    body_w = max(step * 0.55, 1.2)

    def y(v):
        return pad + (vmax - v) / vrange * plot_h

    def x(i):
        return pad + i * step + step / 2

    parts = [f'<svg viewBox="0 0 {width} {height}" class="chart-svg" '
             f'role="img" aria-label="Grafic {chart.get("symbol", "")}">']

    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        gy = pad + frac * plot_h
        parts.append(f'<line x1="{pad}" y1="{gy:.1f}" x2="{width - pad}" y2="{gy:.1f}" class="grid-line"/>')

    for i, c in enumerate(candles):
        o, h, l, cl = c[1], c[2], c[3], c[4]
        bull = cl >= o
        cls = "candle-bull" if bull else "candle-bear"
        cx = x(i)
        parts.append(f'<line x1="{cx:.1f}" y1="{y(h):.1f}" x2="{cx:.1f}" y2="{y(l):.1f}" class="{cls}" stroke-width="1"/>')
        top, bot = (o, cl) if bull else (cl, o)
        y1, y2 = y(top), y(bot)
        rect_h = max(abs(y2 - y1), 1)
        parts.append(f'<rect x="{cx - body_w / 2:.1f}" y="{min(y1, y2):.1f}" width="{body_w:.1f}" height="{rect_h:.1f}" class="{cls}"/>')

    def polyline(series, css_class):
        pts = [(x(i), y(v)) for i, v in enumerate(series) if v is not None]
        if len(pts) < 2:
            return ""
        path = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
        return f'<polyline points="{path}" class="{css_class}"/>'

    parts.append(polyline(ema20, "ema-20"))
    parts.append(polyline(ema50, "ema-50"))
    parts.append("</svg>")
    return "\n".join(parts)


# ------------------------------ COMPONENTE HTML -----------------------------

def render_weight_bars(weights):
    order = ["trend", "momentum", "volatility", "volume"]
    rows = []
    for k in order:
        v = weights.get(k, 1.0)
        pct = max(3, min(100, round((v / 2.0) * 100)))
        state = "up" if v > 1.02 else ("down" if v < 0.98 else "flat")
        rows.append(f'''<div class="weight-row">
      <span class="weight-label">{k}</span>
      <div class="weight-track"><div class="weight-baseline"></div>
        <div class="weight-fill weight-{state}" style="width:{pct}%"></div></div>
      <span class="weight-value">{v:.2f}&times;</span>
    </div>''')
    return "\n".join(rows)


def render_opportunity_rows(rows):
    if not rows:
        return '<tr><td colspan="4" class="empty">(niciun semnal)</td></tr>'
    out = []
    for r in rows:
        cls = "row-long" if r["direction"] == "LONG" else "row-short"
        out.append(
            f'<tr class="{cls}"><td>{r["symbol"]}</td><td>{r["risk_adjusted"]}</td>'
            f'<td>{r["probability"]}%</td><td>{r["persistence"]}</td></tr>'
        )
    return "\n".join(out)


def render_liquidity(deep):
    liq = (deep or {}).get("liquidity")
    if not liq:
        return '<p class="dim">Fara date de order book inca.</p>'
    bid_rows = "".join(
        f'<div class="liq-row liq-bid"><span>BID</span><span>{fmt_price(b["price"])}</span><span class="dim">{b["amount"]:g}</span></div>'
        for b in liq["bids"]
    )
    ask_rows = "".join(
        f'<div class="liq-row liq-ask"><span>ASK</span><span>{fmt_price(a["price"])}</span><span class="dim">{a["amount"]:g}</span></div>'
        for a in liq["asks"]
    )
    return f'<div class="liq-list">{bid_rows}{ask_rows}</div>'


def render_similar_projects(token_meta, narrative):
    if not token_meta:
        return '<p class="dim">Fara date inca &mdash; ruleaza update_token_metadata.py.</p>'
    labs_badge = ' <span class="badge-labs">BINANCE LABS</span>' if token_meta.get("binance_labs") else ""
    cats = ", ".join(token_meta.get("categories", [])[:4]) or "-"
    similar = token_meta.get("similar") or []
    sim_html = "".join(
        f'<div class="sim-row"><span>{s}</span><span class="dim">{round(v * 100)}% overlap</span></div>'
        for s, v in similar
    ) or '<p class="dim">Niciun proiect similar gasit inca in universul scanat.</p>'
    narrative_html = ""
    if narrative:
        narrative_html = f'<p class="narrative">{narrative["text"]}</p>'
    return f'''<div class="cats">{cats}{labs_badge}</div>
    <div class="sim-list">{sim_html}</div>
    {narrative_html}'''


def render_levels(deep):
    if not deep:
        return '<p class="dim">Fara analiza detaliata inca &mdash; apare dupa prima scanare cu semnal.</p>'
    s, fib = deep["structure"], deep["fibonacci"]
    res = " &middot; ".join(fmt_price(v) for v in s["resistance"]) or "-"
    sup = " &middot; ".join(fmt_price(v) for v in s["support"]) or "-"
    fib_rows = "".join(
        f'<div class="fib-row"><span>{k}</span><span>{fmt_price(v)}</span></div>'
        for k, v in fib["retracement"].items()
    )
    return f'''<div class="levels-grid">
      <div><span class="dim">RESISTANCE</span><br>{res}</div>
      <div><span class="dim">SUPPORT</span><br>{sup}</div>
    </div>
    <div class="fib-list">{fib_rows}</div>'''


def render_plan(best, deep):
    if not best or not deep:
        return '<p class="dim">Niciun candidat cu semnal clar in scanarea curenta.</p>'
    plan = deep["plan"]
    direction_cls = "long" if best["direction"] == "LONG" else "short"
    return f'''
    <div class="plan-head">
      <span class="symbol">{best["symbol"]}</span>
      <span class="badge badge-{direction_cls}">{best["direction"]}</span>
    </div>
    <div class="confidence-row">
      <span class="dim">CONFIDENCE</span>
      <span class="confidence-value">{best["probability"]}%</span>
    </div>
    <div class="plan-grid">
      <div><span class="dim">ENTRY</span><br>{fmt_price(plan["entry"])}</div>
      <div><span class="dim">SL</span><br class="sl">{fmt_price(plan["sl"])}</div>
      <div><span class="dim">TP1</span><br>{fmt_price(plan["tp1"])}</div>
      <div><span class="dim">TP2</span><br>{fmt_price(plan["tp2"])}</div>
    </div>
    <div class="expected-r">Expected R &middot; <strong>{plan["expected_r"]}R</strong></div>
    '''


# --------------------------------- PAGINA -----------------------------------

def compute_hit_rate_curve(history):
    """Hit-rate cumulativ, un punct per scanare - arata cum evolueaza
    precizia semnalelor pe masura ce se acumuleaza date evaluate."""
    points, hits, total = [], 0, 0
    for scan in history:
        for r in scan.get("results", []):
            if r.get("outcome") == "hit":
                hits += 1; total += 1
            elif r.get("outcome") == "miss":
                total += 1
        points.append(round(100 * hits / total, 2) if total else None)
    return points


def render_line_chart_svg(series_dict, width=640, height=150, pad=12, y_min=None, y_max=None):
    """Mini-grafic de linii generic (fara lumanari) - reutilizat pentru
    hit-rate si pentru evolutia ponderilor."""
    all_vals = [v for s in series_dict.values() for v in s if v is not None]
    if len(all_vals) < 2:
        return '<div class="chart-empty">Inca nu sunt destule date acumulate.</div>'
    vmax = y_max if y_max is not None else max(all_vals)
    vmin = y_min if y_min is not None else min(all_vals)
    vrange = (vmax - vmin) or 1
    n = max(len(s) for s in series_dict.values())
    plot_w, plot_h = width - 2 * pad, height - 2 * pad

    def x(i):
        return pad + (i / max(n - 1, 1)) * plot_w

    def y(v):
        return pad + (vmax - v) / vrange * plot_h

    colors = ["var(--ema20)", "var(--bull)", "var(--amber)", "var(--bear)"]
    parts = [f'<svg viewBox="0 0 {width} {height}" class="chart-svg-sm">']
    for frac in (0, 0.5, 1.0):
        gy = pad + frac * plot_h
        parts.append(f'<line x1="{pad}" y1="{gy:.1f}" x2="{width - pad}" y2="{gy:.1f}" class="grid-line"/>')
    for idx, series in enumerate(series_dict.values()):
        pts = [(x(i), y(v)) for i, v in enumerate(series) if v is not None]
        if len(pts) < 2:
            continue
        path = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
        parts.append(f'<polyline points="{path}" fill="none" stroke="{colors[idx % len(colors)]}" stroke-width="1.6" opacity="0.9"/>')
    parts.append("</svg>")
    return "\n".join(parts)


def render_learning_curve(history, weights_history, health):
    hit_curve = compute_hit_rate_curve(history)
    hit_svg = render_line_chart_svg({"hit_rate": hit_curve}, y_min=0, y_max=100)

    comps = ["trend", "momentum", "volatility", "volume"]
    w_series = {c: [w.get(c) for w in weights_history] for c in comps}
    w_svg = render_line_chart_svg(w_series, y_min=0.3, y_max=2.0) if len(weights_history) >= 2 else (
        '<div class="chart-empty">Se acumuleaza de-abia de acum - revino peste cateva zile.</div>'
    )

    legend = "".join(
        f'<span><i class="dot" style="background:{c}"></i>{n}</span>'
        for n, c in zip(comps, ["var(--ema20)", "var(--bull)", "var(--amber)", "var(--bear)"])
    )

    return f'''
    <div class="lc-block">
      <h3>Hit-rate cumulativ <span class="dim">({health["evaluated"]} semnale evaluate)</span></h3>
      {hit_svg}
    </div>
    <div class="lc-block">
      <h3>Evolutia ponderilor adaptive</h3>
      {w_svg}
      <div class="legend">{legend}</div>
    </div>
    '''


def build_html(scan, best, deep, chart, health, weights, session, token_meta, narrative, history, weights_history):
    plan_html = render_plan(best, deep)
    levels_html = render_levels(deep)
    liquidity_html = render_liquidity(deep)
    similar_html = render_similar_projects(token_meta, narrative)
    learning_curve_html = render_learning_curve(history, weights_history, health)
    chart_svg = render_svg_chart(chart)
    weight_bars = render_weight_bars(weights)
    long_rows = render_opportunity_rows(scan.get("top_long", []))
    short_rows = render_opportunity_rows(scan.get("top_short", []))
    scan_time = scan.get("scan_time", "-")
    universe = scan.get("universe_size", 0)
    sessions_txt = ", ".join(session["active"])

    return f'''<!doctype html>
<html lang="ro">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SCANLINE &middot; AI market scanner</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>
:root{{
  --bg:#0B0F14; --panel:#131920; --panel-2:#1A222B; --border:#232C36;
  --text:#E7E4DD; --text-dim:#8A93A0;
  --bull:#34D399; --bear:#FB7A6C; --amber:#E6B450;
  --ema20:#6FB7FF; --ema50:#C792EA;
  --font-sans:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;
  --font-mono:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
}}
*{{box-sizing:border-box;}}
body{{margin:0;background:var(--bg);color:var(--text);font-family:var(--font-sans);
  -webkit-font-smoothing:antialiased;}}
.wrap{{max-width:1100px;margin:0 auto;padding:20px 16px 60px;}}
header{{display:flex;justify-content:space-between;align-items:baseline;
  padding-bottom:16px;margin-bottom:18px;border-bottom:1px solid var(--border);}}
.brand{{font-family:var(--font-mono);font-size:13px;letter-spacing:.14em;
  color:var(--amber);text-transform:uppercase;}}
.brand small{{display:block;font-family:var(--font-sans);letter-spacing:0;
  color:var(--text-dim);font-size:12px;margin-top:3px;text-transform:none;}}
.meta{{text-align:right;font-family:var(--font-mono);font-size:12px;color:var(--text-dim);}}
.dim{{color:var(--text-dim);font-size:11px;letter-spacing:.06em;text-transform:uppercase;}}

.grid{{display:grid;gap:14px;grid-template-columns:1fr;}}
@media(min-width:900px){{.grid{{grid-template-columns:1.4fr 1fr;align-items:start;}}}}

.card{{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:16px 18px;}}
.card + .card{{margin-top:14px;}}
.card h2{{font-size:12px;letter-spacing:.1em;text-transform:uppercase;
  color:var(--text-dim);margin:0 0 12px;font-weight:600;}}

.plan-head{{display:flex;align-items:center;gap:10px;margin-bottom:10px;}}
.symbol{{font-family:var(--font-mono);font-size:22px;font-weight:700;}}
.badge{{font-family:var(--font-mono);font-size:12px;padding:3px 9px;border-radius:5px;font-weight:700;}}
.badge-long{{background:rgba(52,211,153,.15);color:var(--bull);}}
.badge-short{{background:rgba(251,122,108,.15);color:var(--bear);}}
.confidence-row{{display:flex;justify-content:space-between;align-items:baseline;
  padding:10px 0;border-top:1px solid var(--border);border-bottom:1px solid var(--border);margin-bottom:12px;}}
.confidence-value{{font-family:var(--font-mono);font-size:20px;font-weight:700;color:var(--amber);}}
.plan-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;
  font-family:var(--font-mono);font-size:14px;}}
.plan-grid div{{background:var(--panel-2);border-radius:7px;padding:8px 10px;}}
.expected-r{{margin-top:12px;font-size:13px;color:var(--text-dim);}}
.expected-r strong{{color:var(--text);font-family:var(--font-mono);}}

.chart-svg{{width:100%;height:auto;display:block;}}
.chart-empty{{color:var(--text-dim);font-size:13px;padding:40px 0;text-align:center;}}
.grid-line{{stroke:var(--border);stroke-width:1;}}
.candle-bull{{fill:var(--bull);stroke:var(--bull);}}
.candle-bear{{fill:var(--bear);stroke:var(--bear);}}
.ema-20{{fill:none;stroke:var(--ema20);stroke-width:1.4;opacity:.9;}}
.ema-50{{fill:none;stroke:var(--ema50);stroke-width:1.4;opacity:.9;}}
.legend{{display:flex;gap:16px;margin-top:8px;font-size:11px;color:var(--text-dim);}}
.legend span{{display:inline-flex;align-items:center;gap:5px;}}
.dot{{width:8px;height:8px;border-radius:50%;display:inline-block;}}

.levels-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;
  font-family:var(--font-mono);font-size:13px;margin-bottom:12px;}}
.fib-list{{display:grid;grid-template-columns:repeat(auto-fit,minmax(90px,1fr));
  gap:6px;font-family:var(--font-mono);font-size:12px;}}
.fib-row{{display:flex;justify-content:space-between;background:var(--panel-2);
  border-radius:6px;padding:5px 8px;color:var(--text-dim);}}
.fib-row span:last-child{{color:var(--text);}}

.liq-list{{display:flex;flex-direction:column;gap:5px;font-family:var(--font-mono);font-size:12px;}}
.liq-row{{display:grid;grid-template-columns:36px 1fr 60px;background:var(--panel-2);
  border-radius:6px;padding:5px 8px;}}
.liq-bid span:first-child{{color:var(--bull);}}
.liq-ask span:first-child{{color:var(--bear);}}

.cats{{font-size:12px;color:var(--text-dim);margin-bottom:10px;}}
.badge-labs{{font-family:var(--font-mono);font-size:10px;background:rgba(230,180,80,.15);
  color:var(--amber);padding:2px 7px;border-radius:5px;margin-left:6px;}}
.sim-list{{display:flex;flex-direction:column;gap:5px;font-family:var(--font-mono);font-size:12px;}}
.sim-row{{display:flex;justify-content:space-between;background:var(--panel-2);
  border-radius:6px;padding:6px 9px;}}
.narrative{{margin-top:12px;padding-top:12px;border-top:1px solid var(--border);
  font-size:13px;line-height:1.6;color:var(--text);}}

.lc-block + .lc-block{{margin-top:16px;padding-top:16px;border-top:1px solid var(--border);}}
.lc-block h3{{font-size:11px;text-transform:uppercase;letter-spacing:.06em;
  color:var(--text-dim);font-weight:500;margin:0 0 8px;}}
.chart-svg-sm{{width:100%;height:auto;display:block;}}

.weight-row{{display:grid;grid-template-columns:80px 1fr 54px;align-items:center;
  gap:10px;margin-bottom:10px;font-size:12px;}}
.weight-label{{text-transform:uppercase;letter-spacing:.06em;color:var(--text-dim);}}
.weight-track{{position:relative;height:6px;background:var(--panel-2);border-radius:3px;}}
.weight-baseline{{position:absolute;left:50%;top:-3px;width:1px;height:12px;background:var(--border);}}
.weight-fill{{height:100%;border-radius:3px;}}
.weight-up{{background:var(--bull);}}
.weight-down{{background:var(--bear);}}
.weight-flat{{background:var(--text-dim);}}
.weight-value{{font-family:var(--font-mono);text-align:right;color:var(--text-dim);}}

.health-row{{display:flex;justify-content:space-between;align-items:center;
  margin-top:12px;padding-top:12px;border-top:1px solid var(--border);}}
.health-status{{font-family:var(--font-mono);font-size:11px;letter-spacing:.06em;
  padding:3px 8px;border-radius:5px;background:var(--panel-2);color:var(--amber);}}

table{{width:100%;border-collapse:collapse;font-family:var(--font-mono);font-size:13px;}}
th{{text-align:left;color:var(--text-dim);font-weight:500;font-size:11px;
  text-transform:uppercase;letter-spacing:.06em;padding-bottom:8px;}}
td{{padding:7px 0;border-top:1px solid var(--border);}}
tr.row-long td:first-child{{color:var(--bull);}}
tr.row-short td:first-child{{color:var(--bear);}}
td.empty{{color:var(--text-dim);text-align:center;padding:16px 0;}}

.sessions{{display:flex;gap:18px;font-family:var(--font-mono);font-size:12px;flex-wrap:wrap;}}
.sessions .dim{{display:block;margin-bottom:2px;}}

footer{{margin-top:26px;color:var(--text-dim);font-size:11px;line-height:1.6;}}

@media(prefers-reduced-motion:reduce){{*{{transition:none!important;}}}}
</style>
</head>
<body>
<div class="wrap">

  <header>
    <div class="brand">SCANLINE<small>AI market scanner &middot; self-learning</small></div>
    <div class="meta">{scan_time}<br>universe {universe}</div>
  </header>

  <div class="grid">
    <div>
      <div class="card">
        <h2>Chart &middot; {(best or {}).get("symbol", "-")}</h2>
        {chart_svg}
        <div class="legend">
          <span><i class="dot" style="background:var(--ema20)"></i>EMA 20</span>
          <span><i class="dot" style="background:var(--ema50)"></i>EMA 50</span>
        </div>
      </div>

      <div class="card">
        <h2>Market structure &middot; Fibonacci</h2>
        {levels_html}
      </div>

      <div class="card">
        <h2>Liquidity levels &middot; order book</h2>
        {liquidity_html}
      </div>

      <div class="card">
        <h2>Top long</h2>
        <table><tr><th>Symbol</th><th>Score</th><th>Prob</th><th>Pers</th></tr>{long_rows}</table>
      </div>
      <div class="card">
        <h2>Top short</h2>
        <table><tr><th>Symbol</th><th>Score</th><th>Prob</th><th>Pers</th></tr>{short_rows}</table>
      </div>
    </div>

    <div>
      <div class="card">
        <h2>AI plan &middot; best candidate</h2>
        {plan_html}
      </div>

      <div class="card">
        <h2>Similar projects</h2>
        {similar_html}
      </div>

      <div class="card">
        <h2>Adaptive weights &middot; model health</h2>
        {weight_bars}
        <div class="health-row">
          <span class="dim">{health["evaluated"]}/{health["min_samples"]} evaluated &middot; hit-rate {health["hit_rate"] if health["hit_rate"] is not None else "-"}%</span>
          <span class="health-status">{health["status"]}</span>
        </div>
      </div>

      <div class="card">
        <h2>Learning curve &middot; progresul agentului</h2>
        {learning_curve_html}
      </div>

      <div class="card">
        <h2>Sessions</h2>
        <div class="sessions">
          <div><span class="dim">UTC now</span>{session["utc_time"]}</div>
          <div><span class="dim">Active</span>{sessions_txt}</div>
        </div>
      </div>
    </div>
  </div>

  <footer>
    Generat automat de crypto_ai_scanner.py + generate_dashboard.py, prin GitHub Actions.
    Scorurile si planul AI sunt euristici proprii, nu recomandari financiare &mdash;
    verifica intotdeauna pe cont propriu inainte de orice decizie de trading.
  </footer>
</div>
</body>
</html>'''


def main():
    history = load_json(HISTORY_FILE, [])
    weights = load_json(WEIGHTS_FILE, {"trend": 1.0, "momentum": 1.0, "volatility": 1.0, "volume": 1.0})
    chart = load_json(CHART_FILE, None)
    token_metadata = load_json(TOKEN_METADATA_FILE, {})
    weights_history = load_json(WEIGHTS_HISTORY_FILE, [])

    scan = history[-1] if history else {"scan_time": "-", "universe_size": 0, "top_long": [], "top_short": []}
    best = scan.get("best_candidate")
    deep = scan.get("deep_analysis")
    health = compute_model_health(history)
    session = get_session_info()

    token_meta = None
    narrative = token_metadata.get("narrative")
    if best and token_metadata.get("tokens"):
        token_meta = token_metadata["tokens"].get(best["symbol"])
        if narrative and narrative.get("symbol") != best["symbol"]:
            narrative = None  # narativul e vechi, pt alt candidat - nu-l arat ca fiind curent

    os.makedirs(DOCS_DIR, exist_ok=True)
    html = build_html(scan, best, deep, chart, health, weights, session, token_meta, narrative, history, weights_history)
    with open(OUTPUT_FILE, "w") as f:
        f.write(html)
    print(f"Dashboard generat: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
