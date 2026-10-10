# -*- coding: utf-8 -*-
"""dashboard.components - Elemente comune: citirea JSON, formatarea preturilor, grafice mici, sanatatea modelului."""

import json
import os

import chart_render
from datetime import datetime, timezone

from dashboard.config import MIN_SAMPLES_FOR_VALIDATION


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


def honest_probability(score, plans_store):
    """Probabilitatea de afisat: cea MASURATA daca exista destule date, altfel
    un semn ca nu stim inca.

    Campul `probability` din scanare e formula `50 + scor * 0.35` - scorul
    rescalat, nu o masuratoare. Afisat ca procent langa cardul de calibrare
    reala, sugera o precizie care nu exista. Acum se afiseaza cifra masurata
    din planurile inchise, cu intervalul ei de incredere, sau "-" cand nu am
    inca destule date pe acel interval de scor.
    """
    cal = (plans_store or {}).get("calibration") or {}
    if score is None:
        return "-", "necunoscut"
    b = str(int(score // 20) * 20)
    e = cal.get(b)
    if not e or not e.get("reliable"):
        n = e.get("total", 0) if e else 0
        return "-", f"necalibrat inca (n={n})"
    return f'{e["win_rate"]}%', f'masurat, IC {e["ci_low"]}-{e["ci_high"]}%, n={e["total"]}'


def get_session_info():
    now = datetime.now(timezone.utc)
    hour = now.hour + now.minute / 60
    windows = [("Tokyo", 0, 9), ("London", 7, 16), ("New York", 13, 22)]
    active = [name for name, s, e in windows if s <= hour < e]
    return {"utc_time": now.strftime("%H:%M UTC"), "active": active or ["-"]}


def compute_model_health(history, min_samples=MIN_SAMPLES_FOR_VALIDATION):
    hits = misses = 0
    for scan in history:
        summ = scan.get("outcome_summary")
        if summ:
            hits += summ.get("hits", 0)
            misses += summ.get("misses", 0)
            continue
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


def compute_hit_rate_curve(history):
    """Hit-rate cumulativ, un punct per scanare - arata cum evolueaza
    precizia semnalelor pe masura ce se acumuleaza date evaluate."""
    points, hits, total = [], 0, 0
    for scan in history:
        # scanarile compactate nu mai au `results`, dar pastreaza rezumatul -
        # altfel curba s-ar rupe retroactiv dupa compactare
        summ = scan.get("outcome_summary")
        if summ:
            hits += summ.get("hits", 0)
            total += summ.get("hits", 0) + summ.get("misses", 0)
        else:
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


def render_sparkline(values, width=200, height=36):
    """Linie de pret minimala, desenata ca SVG. Fara librarie, fara CDN."""
    vals = [v for v in (values or []) if v is not None]
    if len(vals) < 2:
        return ""
    vmax, vmin = max(vals), min(vals)
    rng = (vmax - vmin) or (vmax or 1)
    n = len(vals)
    pts = " ".join(
        f"{(i / (n - 1)) * width:.1f},{height - ((v - vmin) / rng) * height:.1f}"
        for i, v in enumerate(vals))
    up = vals[-1] >= vals[0]
    color = "var(--bull)" if up else "var(--bear)"
    return (f'<svg viewBox="0 0 {width} {height}" class="spark" preserveAspectRatio="none">'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.5"/></svg>')


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


def render_rich_chart(chart, fullscreen_id=None):
    """Graficul curat, cu straturile principale. Randarea propriu-zisa e in
    chart_render.py; functia ramane aici pentru compatibilitate cu apelurile
    existente (graficele per token)."""
    return chart_render.render_chart(chart, chart_render.MAIN_LAYERS, 300,
                                     fullscreen_id=fullscreen_id)


# ------------------------------------------------------------------ sectiuni pliate

def fold(title, body, summary=(), anchor=None, cls=""):
    """O sectiune a dashboard-ului, PLIATA implicit: titlul si cel mult 3 randuri de rezumat
    (dashboard.summaries) raman vizibile; continutul complet se deschide la atingere. Rezumatul
    e text simplu, escapat aici; titlul e HTML (poate contine &middot; si <span class="dim">)."""
    import html as _h
    lines = "".join(f'<span class="fold-line" title="{_h.escape(str(x), quote=True)}">{_h.escape(str(x))}</span>'
                    for x in list(summary or [])[:3] if x)
    ida = f' id="{anchor}"' if anchor else ""
    sum_html = f'<span class="fold-sum">{lines}</span>' if lines else ""
    return (f'<details class="card fold{(" " + cls) if cls else ""}"{ida}>'
            f'<summary class="fold-head"><h2>{title}</h2>{sum_html}</summary>'
            f'<div class="fold-body">{body}</div></details>')


# Bara "deschide / inchide tot" si scriptul care deschide sectiunile pliate cand o legatura
# duce in interiorul lor (ex. #analist, cardul unui token de pe pagina bursei).
FOLD_TOOLS = ('<div class="fold-tools"><button type="button" data-fold="open">Deschide tot</button>'
              '<button type="button" data-fold="close">Închide tot</button></div>')

FOLD_JS = """<script>
(function(){
  function openTo(id){
    var el=document.getElementById(id); if(!el) return;
    for(var p=el;p;p=p.parentElement){ if(p.tagName==='DETAILS') p.open=true; }
    el.scrollIntoView();
  }
  function fromHash(){ if(location.hash.length>1){ try{ openTo(decodeURIComponent(location.hash.slice(1))); }catch(e){} } }
  window.addEventListener('hashchange',fromHash);
  if(document.readyState==='loading'){ document.addEventListener('DOMContentLoaded',fromHash); } else { fromHash(); }
  document.addEventListener('click',function(e){
    var b=e.target.closest && e.target.closest('[data-fold]');
    if(b){ var o=b.getAttribute('data-fold')==='open';
      document.querySelectorAll('details.fold').forEach(function(d){ d.open=o; }); return; }
    var a=e.target.closest && e.target.closest('a[href*="#"]');
    if(a){ var u=new URL(a.getAttribute('href'),location.href);
      if(u.pathname===location.pathname && u.hash.length>1){
        setTimeout(function(){ try{ openTo(decodeURIComponent(u.hash.slice(1))); }catch(err){} },0); } }
  });
})();
</script>"""
