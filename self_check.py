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
import math
import os

import chart_render
from datetime import datetime, timezone

DATA_DIR = "data"
DOCS_DIR = "docs"
HISTORY_FILE = os.path.join(DATA_DIR, "scan_history.json")
WEIGHTS_FILE = os.path.join(DATA_DIR, "weights.json")
WEIGHTS_HISTORY_FILE = os.path.join(DATA_DIR, "weights_history.json")
AGENT_MODEL_FILE = os.path.join(DATA_DIR, "agent_model.json")
PLANS_FILE = os.path.join(DATA_DIR, "plans.json")
BRIEFING_FILE = os.path.join(DATA_DIR, "briefing.json")
DETAILS_FILE = os.path.join(DATA_DIR, "latest_details.json")
EXCHANGES_FILE = os.path.join(DATA_DIR, "exchanges.json")
EXCHANGE_SCANS_FILE = os.path.join(DATA_DIR, "exchange_scans.json")
ALTSEASON_FILE = os.path.join(DATA_DIR, "altseason.json")
ALTSEASON_HISTORY_FILE = os.path.join(DATA_DIR, "altseason_history.json")
CHART_FILE = os.path.join(DATA_DIR, "latest_chart.json")
OUTPUT_FILE = os.path.join(DOCS_DIR, "index.html")

MIN_SAMPLES_FOR_VALIDATION = 100


TOKEN_METADATA_FILE = os.path.join(DATA_DIR, "token_metadata.json")
TOKEN_META_UPDATED = {}   # data ultimei actualizari a metadatelor, pentru mesajul din card


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


def render_self_check(diag, repairs):
    """Auto-diagnosticul si auto-repararea: ce verifica agentul singur la fiecare
    scanare, ce a gasit, ce atenuari a aplicat automat si ce reparatii de cod a
    propus - vizibil fara ca cineva sa trimita date."""
    if not diag:
        return '<p class="dim">Primul auto-diagnostic ruleaza la urmatoarea scanare.</p>'
    st = diag.get("status", "OK")
    cls = {"OK": "tag-bull", "WARN": "tag-info", "ERROR": "tag-bear"}.get(st, "tag-info")
    label = {"OK": "TOTUL COERENT", "WARN": "AVERTISMENTE", "ERROR": "ERORI DETECTATE"}.get(st, st)
    checks = diag.get("checks") or []
    bad = [c for c in checks if c["level"] != "OK"]
    good = [c for c in checks if c["level"] == "OK"]
    rows = []
    for c in bad:
        ex = "".join(f"<li>{e}</li>" for e in c.get("examples") or [])
        rows.append(f'<details class="sc-item sc-{c["level"].lower()}"><summary><span class="tag '
                    f'{"tag-bear" if c["level"] == "ERROR" else "tag-info"}">{c["level"]}</span> '
                    f'<strong>{c["title"]}</strong></summary><p>{c["detail"]}</p>'
                    + (f'<ul class="as-ul">{ex}</ul>' if ex else "") + "</details>")
    ok_list = " &middot; ".join(c["title"] for c in good)
    m = diag.get("mitigations") or {}
    q = m.get("quarantine") or {}
    qtxt = ("".join(f'<li><code>{f}</code> - {v.get("reason", "")} (din {v.get("since", "?")}, '
                    f'{v.get("clean_runs", 0)}/3 rulari curate pana la eliberare)</li>' for f, v in q.items())
            or "<li>nicio caracteristica in carantina</li>")
    mit = (f'<h4 class="scan-h">Atenuari automate active</h4><ul class="as-ul">{qtxt}'
           f'<li>Filtrul de conflict Elliott: <strong>{"ACTIV" if m.get("elliott_filter", True) else "OPRIT"}</strong>'
           f' - {m.get("elliott_filter_reason", "")}</li>'
           + (f'<li><strong>MOD DE SIGURANTA ACTIV</strong> - {m.get("safe_reason")}</li>' if m.get("safe_mode") else "")
           + "</ul>")
    rep = ""
    if repairs:
        items = []
        for r in repairs[-5:][::-1]:
            link = f' &middot; <a href="{r["pr"]}">Pull Request</a>' if r.get("pr") else ""
            items.append(f'<li><code>{r.get("when")}</code> {r.get("check")}: <strong>{r.get("outcome")}</strong>'
                         f' - {r.get("reason", "")}{link}</li>')
        rep = f'<h4 class="scan-h">Reparatii de cod propuse (ultimele 5)</h4><ul class="as-ul">{"".join(items)}</ul>'
    chips = "".join(f'<span class="sc-chip sc-{h.get("status", "OK").lower()}"></span>'
                    for h in (diag.get("history") or [])[-48:])
    return (f'<div class="sc-head"><span class="tag {cls}">{label}</span> <span class="dim">verificat la '
            f'{diag.get("when")} &middot; {len(checks)} verificari</span></div>'
            + "".join(rows)
            + (f'<p class="dim sc-ok">In regula: {ok_list}</p>' if good else "")
            + mit + rep
            + (f'<h4 class="scan-h">Istoric (ultimele {min(48, len(diag.get("history") or []))} scanari)</h4>'
               f'<div class="sc-tl">{chips}</div>' if chips else "")
            + '<p class="dim as-note">Agentul invata PONDERI din rezultate; logica de calcul o verifica '
              'aceste invariante la fiecare scanare. O eroare noua deschide automat un Issue, iar '
              'auto-repararea propune un patch validat automat - merge-ul ramane al tau.</p>')


def render_altseason_history(h):
    """Contextul istoric pe 10 ani: grafic, pozitia de acum fata de ciclurile
    trecute, ce a urmat istoric dupa momente similare si predictiile invatate."""
    if not h or not h.get("position"):
        return ('<h4 class="scan-h">Context istoric &middot; 10 ani</h4><p class="dim">Istoricul se construieste '
                'la urmatoarea scanare (Coin Metrics + bursa, ~1 minut, o singura data).</p>')
    import altseason as _A
    pos, lr, pred = h["position"], h.get("learn") or {}, h.get("prediction") or {}
    pn = lambda k: _A.PHASES[k][1] if isinstance(k, int) and 0 <= k <= 8 else "?"
    pct = lambda v: "n/d" if v is None else f"{v * 100:.0f}%"
    # --- grafic: indicele istoric (0-100) si BTC (scara log), ferestrele altseason, ciclurile
    tl = h.get("timeline_tail") or []
    svg = ""
    if len(tl) > 20:
        W, H, L, T, B = 760, 170, 34, 10, 22
        pw, ph = W - L - 10, H - T - B
        days = [r[0] for r in tl]
        xi = lambda i: L + pw * i / (len(tl) - 1)
        def xd(d):
            import bisect
            return xi(min(len(days) - 1, bisect.bisect_left(days, d)))
        yai = lambda v: T + ph * (1 - v / 100)
        lp = [math.log10(r[2]) for r in tl if r[2]]
        lo, hi = min(lp), max(lp)
        yb = lambda v: T + ph * (1 - (math.log10(v) - lo) / ((hi - lo) or 1))
        parts = [f'<svg viewBox="0 0 {W} {H}" class="ah-svg" role="img" aria-label="Indice altseason istoric si BTC">']
        for w in h.get("windows") or []:
            x0, x1 = xd(w["start"]), xd(w["end"])
            parts.append(f'<rect x="{x0:.1f}" y="{T}" width="{max(2, x1 - x0):.1f}" height="{ph}" fill="#089981" opacity="0.18"/>')
        for lvl in (25, 75):
            parts.append(f'<line x1="{L}" x2="{L + pw}" y1="{yai(lvl):.1f}" y2="{yai(lvl):.1f}" stroke="#CBD5E1" stroke-dasharray="3 3"/>'
                         f'<text x="{L - 4}" y="{yai(lvl) + 3:.1f}" text-anchor="end" class="ah-t">{lvl}</text>')
        pts = " ".join(f"{xi(i):.1f},{yb(r[2]):.1f}" for i, r in enumerate(tl) if r[2])
        parts.append(f'<polyline points="{pts}" fill="none" stroke="#94A3B8" stroke-width="1.2"/>')
        pts = " ".join(f"{xi(i):.1f},{yai(r[1]):.1f}" for i, r in enumerate(tl) if r[1] is not None)
        parts.append(f'<polyline points="{pts}" fill="none" stroke="#7E57C2" stroke-width="1.6"/>')
        # etichetele: varfurile sus, minimele jos; cand doua etichete de acelasi
        # tip sunt prea apropiate, a doua coboara/urca un rand (nu se suprapun)
        last_x = {"V": [], "M": []}
        for c in h.get("cycles") or []:
            for d, col, lab in ((c.get("top"), "#F23645", "V"), (c.get("low"), "#089981", "M")):
                if d and d >= days[0]:
                    x = xd(d)
                    lvl = sum(1 for px_ in last_x[lab] if abs(x - px_) < 62)
                    last_x[lab].append(x)
                    y = (T + 9 + 10 * lvl) if lab == "V" else (T + ph - 4 - 10 * lvl)
                    anchor = "end" if x > L + pw - 60 else "start"
                    dx = -2 if anchor == "end" else 2
                    parts.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{T}" y2="{T + ph}" stroke="{col}" stroke-width="0.8" opacity="0.7"/>'
                                 f'<text x="{x + dx:.1f}" y="{y:.1f}" text-anchor="{anchor}" class="ah-t" style="fill:{col}">{lab} {d[:7]}</text>')
        for y in range(int(days[0][:4]) + 1, int(days[-1][:4]) + 1):
            x = xd(f"{y}-01-01")
            parts.append(f'<text x="{x:.1f}" y="{H - 6}" text-anchor="middle" class="ah-t">{y}</text>')
        lx, ly = xi(len(tl) - 1), yai(tl[-1][1] or 0)
        parts.append(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3" fill="#7E57C2"/>'
                     f'<text x="{lx - 5:.1f}" y="{max(T + 30, ly - 6):.1f}" text-anchor="end" class="ah-t" '
                     f'style="fill:#7E57C2;font-weight:700">ACUM {tl[-1][1]:.0f}</text></svg>')
        svg = ('<div class="ah-legend"><span style="color:#7E57C2">&#9632; indice altseason istoric (0-100)</span> '
               '<span style="color:#94A3B8">&#9632; BTC (scara log)</span> <span style="color:#089981">&#9632; altseason '
               '(indice &ge; 75)</span> <span style="color:#F23645">V</span> varf ciclu &middot; <span style="color:#089981">M</span> minim</div>'
               + "".join(parts))
    # --- pozitia de acum
    pc = "".join(f'<li>dupa minimul din {c["low"][:7]}: in {c["same_point"][:7]} indicele era {c["ai"]:.0f}, faza {c["phase"]} '
                 f'({pn(c["phase"])})' + (f'; primul altseason a venit in {c["first_altseason_after_low"][:7]}'
                                          if c.get("first_altseason_after_low") else "; fara altseason pana la varful urmator")
                 + "</li>" for c in pos.get("past_cycles") or [])
    cyc = (h.get("cycles") or [{}])[-1]
    left = ('<h4 class="scan-h">Unde suntem fata de istoric</h4><ul class="as-ul">'
            + (f'<li>{pos["days_since_top"]} zile de la varful ciclului (${cyc.get("top_price", 0):,.0f}, {cyc.get("top", "")})</li>'
               if pos.get("days_since_top") is not None else "")
            + (f'<li>{pos["days_since_low"]} zile de la minim ({cyc.get("low", "")}, +{pos.get("up_from_low")}% de atunci)</li>'
               if pos.get("days_since_low") is not None else "")
            + f'<li>indicele istoric comparabil e la percentila {pos.get("ai_percentile")} din 10 ani</li>'
            + f'<li>faza comparabila istoric: {pos.get("phase_days")} zile in faza curenta (durata mediana istorica: '
              f'{pos.get("phase_median_days")} zile)</li></ul>'
            + (f'<p class="dim">In acelasi punct dupa minim, in ciclurile trecute:</p><ul class="as-ul">{pc}</ul>' if pc else ""))
    w = lr.get("weights") or {}
    nx = ", ".join(f"faza {q} ({pn(q)}) {p * 100:.0f}%" for q, p in pos.get("next30") or [])
    sk = lambda v: "n/d" if v is None else (f"+{v * 100:.0f}%" if v >= 0 else f"{v * 100:.0f}%")
    right = ('<h4 class="scan-h">Ce urmeaza, invatat din istoric</h4><ul class="as-ul">'
             f'<li>probabilitatea ca indicele sa atinga 75 in 90 de zile: <strong>{pct(pred.get("p"))}</strong> '
             '(in acumulare ar insemna o rotatie larga; un altseason de ciclu cere si BTC aproape de ATH) '
             f'(analogii {pct(pred.get("pa"))}, tranzitii {pct(pred.get("pt"))}, frecventa istorica {pct(pred.get("pb"))})</li>'
             f'<li>putere predictiva, evaluata walk-forward pe {lr.get("evaluations", 0)} momente din 10 ani, fata de '
             f'frecventa istorica: analogii {sk(lr.get("skill_analog"))}, tranzitii {sk(lr.get("skill_trans"))}</li>'
             f'<li>ponderi invatate: analogii {w.get("analog", 0):.2f} &middot; tranzitii {w.get("trans", 0):.2f} &middot; '
             f'frecventa {w.get("base", 0):.2f}; predictii verificate de la lansare: {lr.get("matured", 0)}</li>'
             + (f'<li>peste 30 de zile, istoric: {nx}</li>' if nx else "") + "</ul>")
    def _alts(c):
        # construit separat: un f-string imbricat cu aceleasi ghilimele e valid doar
        # din Python 3.12, iar workflow-ul ruleaza pe 3.11 (prins de check_py311)
        return ", ".join("{} (varf {:.0f})".format(a["start"][:7], a["peak"]) for a in c.get("altseasons") or []) or "-"
    rows = "".join(
        f'<tr><td>{c["top"]}</td><td>${c["top_price"]:,.0f}</td><td>{c.get("low") or "-"}</td>'
        f'<td>{c.get("drawdown") or "-"}%</td><td>{c.get("days_top_to_low") or "-"}</td>'
        f'<td>{_alts(c)}</td></tr>'
        for c in h.get("cycles") or [])
    an = "".join(f'<tr><td>{a["day"]}</td><td>{a["ai"]:.0f}</td><td>{a["phase"]}</td>'
                 f'<td>{"DA" if a["alt90"] else "nu"}</td><td>{a["btc90"]:+.0f}%</td></tr>'
                 for a in (pos.get("analogs") or [])[:6])
    tables = ('<div class="as-cols"><div><h4 class="scan-h">Fazele bear majore (&ge; 45%) si altseason-urile de dupa</h4>'
              '<div class="as-table-wrap"><table class="as-table"><tr><th>Varf</th><th>Pret</th><th>Minim</th><th>Scadere</th>'
              f'<th>Zile</th><th>Altseason dupa minim</th></tr>{rows}</table></div></div>'
              '<div><h4 class="scan-h">Cele mai asemanatoare momente si ce a urmat</h4><div class="as-table-wrap">'
              '<table class="as-table"><tr><th>Moment</th><th>Indice</th><th>Faza</th><th>Altseason in 90z</th>'
              f'<th>BTC 90z</th></tr>{an}</table></div></div></div>')
    note = ('<p class="dim as-note">Istoric: Coin Metrics community data (CC BY-NC 4.0, pana la '
            f'{h.get("cm_last_day")}) prelungit din bursa pana la {h.get("last_day")}; {h.get("assets")} active. '
            'Indicele istoric foloseste altcoins cu istoric lung (~45), nu top 100 de azi: nivelul difera de '
            'CoinMarketCap, dar ciclurile coincid (ian. 2018, apr. 2021, dec. 2024). Dominanta BTC istorica e folosita '
            'doar ca directie. Fazele 5-7 (small caps) nu se pot reconstrui istoric - le detecteaza doar evaluarea live.</p>')
    return ('<h4 class="scan-h">Context istoric &middot; 10 ani</h4>' + svg
            + f'<div class="as-cols">{left}<div>{right}</div></div>' + tables + note)


def render_altseason(state, history):
    """Faza ciclului altcoin season, pe date reale: faza curenta si increderea,
    ce o sustine si ce lipseste inca, faza urmatoare cu conditiile concrete de
    confirmare, indicatorii, proiectele cu semnale de crestere si istoricul."""
    if not state or not state.get("classification"):
        return ('<p class="dim">Evaluarea apare dupa prima scanare cu acces la CoinGecko '
                '(dominanta BTC, top 250, lumanari zilnice pentru indicele pe 90 de zile).</p>')
    import altseason as _A
    c, ind = state["classification"], state.get("indicators") or {}
    f1 = lambda v, suf="", nd=1, sign=False: ("n/d" if v is None else
                                              (f"{v:+.{nd}f}{suf}" if sign else f"{v:.{nd}f}{suf}"))
    stale = (' <span class="tag tag-bear">DATE VECHI - CoinGecko indisponibil la ultima '
             'scanare</span>' if state.get("stale") else "")
    trans = (f' &middot; <strong>tranzitie {c["transition"]}</strong>' if c.get("transition") else "")
    ai = ind.get("alt_index_90d")
    season_cls = ("tag-bull" if (ai or 0) >= 75 else "tag-bear" if ai is not None and ai <= 25 else "tag-info")
    head = (f'<div class="as-head"><div class="as-phase">FAZA {c["phase"]}</div>'
            f'<div><strong class="as-name">{c["name"]}</strong><div class="dim">{c["what"]}</div>'
            f'<div class="as-meta">incredere {c["confidence"]*100:.0f}% &middot; '
            + ("semnal relativ altcoins vs BTC: faza " if (c.get("runner_up") or {}).get("kind") == "relativ"
               else "locul 2: faza ")
            + f'{c["runner_up"]["phase"]} ({c["runner_up"]["name"]}){trans}</div></div></div>'
            f'<div class="as-season"><span class="tag {season_cls}">{c["season"].upper()}</span> '
            f'Indice Altcoin Season 90z (definitia CoinMarketCap): <strong>{f1(ai, "", 0)}</strong>'
            f' / 100 &middot; calculat pe {ind.get("alt_index_coverage", 0)} din top 100 altcoins{stale}</div>')

    sc = c.get("scores") or {}
    mx = max(sc.values()) if sc else 1
    steps = []
    for num, name, _w, _t in _A.PHASES:
        v = sc.get(str(num), 0)
        cls = "as-step cur" if num == c["phase"] else ("as-step nxt" if num == c["next"]["phase"] else "as-step")
        steps.append(f'<div class="{cls}"><span class="as-n">{num}</span><span class="as-t">{name}</span>'
                     f'<span class="as-bar"><i style="width:{100*v/(mx or 1):.0f}%"></i></span></div>')
    ladder = '<div class="as-ladder">' + "".join(steps) + "</div>"

    bt, mr = ind.get("breadth30_tier") or {}, ind.get("med_rel30_tier") or {}
    cells = [
        ("Dominanta BTC", f'{f1(ind.get("btc_d"), "%")}',
         f'acum 30z ~{f1(ind.get("btc_d_30d_ago"), "%")} ({f1(ind.get("btc_d_delta30"), " pp", 1, True)}) &middot; '
         f'acum 200z ~{f1(ind.get("btc_d_200d_ago"), "%")}'),
        ("ETH/BTC", f'{f1(ind.get("eth_btc"), "", 5)}',
         f'30z {f1(ind.get("eth_btc_30d"), "%", 1, True)} &middot; 200z {f1(ind.get("eth_btc_200d"), "%", 1, True)}'),
        ("Latime 30z (bat BTC)", f'{f1(ind.get("breadth30"), "%", 0)} din top 100',
         f'large {f1(bt.get("large"), "%", 0)} &middot; mid {f1(bt.get("mid"), "%", 0)} &middot; '
         f'small {f1(bt.get("small"), "%", 0)}'),
        ("Mediana fata de BTC, 30z", f'large {f1(mr.get("large"), "%", 1, True)}',
         f'mid {f1(mr.get("mid"), "%", 1, True)} &middot; small {f1(mr.get("small"), "%", 1, True)}'),
        ("Bitcoin", f'{f1(ind.get("btc_r30"), "%", 1, True)} pe 30z',
         f'200z {f1(ind.get("btc_r200"), "%", 0, True)} &middot; fata de ATH {f1(ind.get("btc_ath_dd"), "%", 1)}'),
        ("Speculatie", f'{f1((ind.get("spec_share") or 0)*100, "%", 0)} small caps +50%/30z',
         f'volum altcoins {f1((ind.get("alt_vol_share") or 0)*100, "%", 0)} din total'),
    ]
    grid = '<div class="as-grid">' + "".join(
        f'<div class="as-cell"><span class="as-lbl">{a}</span><strong>{b}</strong><span class="dim">{d}</span></div>'
        for a, b, d in cells) + "</div>"

    why = "".join(f"<li>{r}</li>" for r in c.get("reasons") or [])
    miss = "".join(f"<li>{r}</li>" for r in c.get("missing") or [])
    trig = "".join(f"<li>{t}</li>" for t in c["next"].get("triggers") or [])
    analysis = (f'<div class="as-cols"><div><h4 class="scan-h">De ce faza {c["phase"]}</h4><ul class="as-ul">{why}</ul>'
                + (f'<h4 class="scan-h">Ce nu se potriveste inca</h4><ul class="as-ul as-miss">{miss}</ul>' if miss else "")
                + f'</div><div><h4 class="scan-h">Predictie: faza {c["next"]["phase"]} - {c["next"]["name"]}</h4>'
                f'<p class="dim">Trecerea se confirma cand:</p><ul class="as-ul">{trig}</ul></div></div>')

    rows = []
    for r in state.get("candidates") or []:
        conf = (f'<span class="tag tag-bull">SCANER {r["scanner_score"]}</span>' if r.get("scanner") else "")
        rows.append(f'<tr><td><strong>{r["symbol"]}</strong> <span class="dim">{r.get("name") or ""}</span> {conf}</td>'
                    f'<td>#{r.get("rank")}</td><td>{r.get("tier")}</td>'
                    f'<td>{f1(r.get("rs7"), "%", 1, True)}</td><td>{f1(r.get("rs30"), "%", 1, True)}</td>'
                    f'<td>{f1(r.get("rs90"), "%", 0, True)}</td></tr>')
    tier = _A.PHASE_TIER.get(c["phase"])
    cand_note = (f"in nivelul favorizat de faza curenta: <strong>{tier} caps</strong>" if tier
                 else "faza curenta nu favorizeaza altcoins - lista e doar informativa")
    cands = ('<h4 class="scan-h">Proiecte cu semnale de crestere</h4>'
             f'<p class="dim">Forta relativa fata de BTC pozitiva pe 7 si 30 de zile, {cand_note}. '
             'SCANER = confirmat si de semnalul LONG al scanerului nostru.</p>'
             + ('<div class="as-table-wrap"><table class="as-table"><tr><th>Proiect</th><th>Rang</th><th>Nivel</th>'
                '<th>vs BTC 7z</th><th>vs BTC 30z</th><th>vs BTC 90z</th></tr>' + "".join(rows) + "</table></div>"
                if rows else '<p class="dim">Niciun proiect nu bate BTC simultan pe 7 si 30 de zile in acest nivel.</p>'))

    tl = ""
    if history:
        chips = "".join(f'<span class="as-chip p{h.get("phase")}" title="{h.get("ts")}">{h.get("phase")}</span>'
                        for h in history[-36:])
        tl = f'<h4 class="scan-h">Istoric faze (ultimele {min(36, len(history))} evaluari)</h4><div class="as-tl">{chips}</div>'

    note = (f'<p class="dim as-note">Surse: CoinGecko (/global, top 250) si lumanari zilnice de pe exchange, '
            f'evaluat la {state.get("when")}. Dominanta de acum 30/200 de zile e ESTIMATA din randamentele '
            'top 250 (ignora schimbarile de oferta). Faza e o clasificare pe reguli transparente, nu o certitudine '
            '- ciclurile nu se repeta identic.</p>')
    # REGIMUL CICLULUI, afisat primul: faza e decisa intai de pozitia BTC in ciclu,
    # apoi de performanta relativa a altcoins. Rotatia din afara pietei bull e
    # aratata separat, ca rotatie - nu ca faza de altseason.
    reg = ""
    if c.get("regime_name"):
        cy = c.get("cycle") or {}
        facts = []
        if cy.get("dd") is not None:
            facts.append(f'BTC {cy["dd"]:+.1f}% fata de ATH (${cy.get("ath", 0):,.0f}, acum {cy.get("days_since_ath")} zile)')
        if cy.get("low"):
            facts.append(f'minim ${cy["low"]:,.0f} ({cy.get("low_day")}), +{cy.get("rec_from_low", 0):.0f}% de atunci')
        if cy.get("ma200"):
            facts.append(f'media de 200 de zile ${cy["ma200"]:,.0f} ({"peste" if cy.get("above_ma200") else "sub"})')
        reg = (f'<div class="as-regime"><span class="as-rtag">REGIM CICLU BTC: {c["regime_name"]}</span> '
               f'<span class="dim">{" &middot; ".join(facts)}</span></div>')
    if c.get("rotation"):
        reg += f'<div class="plan-conflict explore"><strong>ROTATIE, NU ALTSEASON</strong> &middot; {c["rotation"]}</div>'
    elif c.get("regime") is None:
        reg += ('<div class="plan-conflict"><strong>REGIMUL CICLULUI INDISPONIBIL</strong> &middot; istoricul se '
                'construieste; faza de mai jos e doar din metrici relative.</div>')
    return (head + reg + ladder + grid + analysis + render_altseason_history(state.get("history"))
            + cands + tl + note)


def render_similar_projects(token_meta, narrative, symbol=None, updated=None):
    if not token_meta:
        # Mesaj actionabil, inclusiv de pe telefon - "ruleaza scriptul" nu se
        # poate face de acolo. Actiunea dedicata e .github/workflows/metadata.yml.
        who = f" pentru <strong>{symbol}</strong>" if symbol else ""
        when = f" Ultima actualizare: {updated}." if updated else ""
        return (f'<p class="dim">Fara metadata{who} inca.{when} Se completeaza automat la '
                f'urmatoarea scanare; imediat, de pe telefon: aplicatia GitHub &rarr; '
                f'Actions &rarr; <strong>Metadata proiecte (Similar projects)</strong> '
                f'&rarr; Run workflow.</p>')
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


def render_indicators(deep):
    """VWAP, POC/VAH/VAL, SuperTrend, MACD, EMA - etichetele din poze."""
    ind = (deep or {}).get("indicators")
    if not ind:
        return '<p class="dim">Indicatorii apar dupa prima scanare cu semnal.</p>'

    rows = []
    st = ind.get("supertrend")
    if st:
        cls = "bull" if st["direction"] == "BULLISH" else "bear"
        rows.append(f'<div class="ind-row"><span class="tag tag-{cls}">SuperTrend {st["direction"]}</span>'
                    f'<span>{fmt_price(st["level"])}</span></div>')
    if ind.get("vwap"):
        rows.append(f'<div class="ind-row"><span class="tag tag-info">VWAP</span>'
                    f'<span>{fmt_price(ind["vwap"])}</span></div>')
    vp = ind.get("volume_profile")
    if vp:
        for label, key, cls in (("VAH","vah","warn"), ("POC","poc","warn"), ("VAL","val","info")):
            rows.append(f'<div class="ind-row"><span class="tag tag-{cls}">{label}</span>'
                        f'<span>{fmt_price(vp[key])}</span></div>')
    m = ind.get("macd")
    if m:
        cls = "bull" if m["bullish"] else "bear"
        rows.append(f'<div class="ind-row"><span class="tag tag-{cls}">MACD</span>'
                    f'<span>hist {m["histogram"]:+.4f}</span></div>')

    emas = ind.get("emas") or {}
    ema_txt = " &middot; ".join(
        f'{k.replace("ema","EMA ")} {fmt_price(v)}' for k, v in emas.items() if v is not None)
    pos = ind.get("price_vs_value_area")
    pos_html = f'<div class="ind-pos">Pretul e <strong>{pos}</strong></div>' if pos else ""

    return ("".join(rows) + pos_html +
            (f'<div class="ema-line dim">{ema_txt}</div>' if ema_txt else ""))


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


def elliott_outcome_stats(plans_store):
    """Masurat live din planurile inchise: rezultatul planurilor luate CONTRA
    numaratorii Elliott, fata de restul. Arata daca filtrul de conflict e inca
    justificat de date - se recalculeaza la fiecare generare."""
    con, rest = [], []
    for p in (plans_store or {}).get("plans") or []:
        if p.get("realized_r") is None or p.get("state") == "NO_ENTRY":
            continue
        v = (p.get("components") or {}).get("ev_elliott")
        if v is None:
            continue
        (con if v <= -0.1 else rest).append(p["realized_r"])
    if len(con) < 50 or len(rest) < 50:
        return None
    return {"n_con": len(con), "r_con": sum(con) / len(con),
            "n_rest": len(rest), "r_rest": sum(rest) / len(rest)}


def render_plan(best, deep, calibration=None, decision=None, ew_stats=None):
    """Cardul "AI plan". CONFIDENCE afisa formula din scor (ex. 76.2%), desi
    probabilitatea MASURATA pe acelasi interval de scor era mult mai mica - iar
    cardul nu spunea nimic cand Elliott contrazicea planul. Acum: probabilitatea
    masurata, decizia efectiva a agentului si conflictul, cu nivelul urmarit."""
    if not best or not deep:
        return '<p class="dim">Niciun candidat cu semnal clar in scanarea curenta.</p>'
    plan = deep["plan"]
    direction_cls = "long" if best["direction"] == "LONG" else "short"
    b = str(int((best.get("risk_adjusted") or best.get("score") or 0) // 20) * 20)
    cal = (calibration or {}).get(b) or {}
    if cal.get("reliable"):
        conf_html = (f'<span class="confidence-value">{cal["win_rate"]}%</span>'
                     f'<div class="dim conf-note">masurat pe {cal["total"]} planuri cu scor {b}-{int(b)+19} '
                     f'(IC {cal["ci_low"]}-{cal["ci_high"]}%) &middot; formula din scor: {best["probability"]}%</div>')
    else:
        conf_html = (f'<span class="confidence-value">{best["probability"]}%</span>'
                     f'<div class="dim conf-note">formula din scor - necalibrat inca pe acest interval</div>')
    dec_html = ""
    if decision:
        mode = decision.get("mode") or ""
        if mode == "ASTEAPTA_CORECTIA":
            nxt = decision.get("next_entry")
            dec_html = ('<div class="plan-conflict"><strong>ASTEAPTA CORECTIA</strong> &middot; '
                        f'Elliott contrazice planul {best["direction"]}. '
                        + (f'Nivel urmarit pentru intrare: <strong>{fmt_price(nxt)}</strong>. ' if nxt else "")
                        + "Planul nu se deschide acum.</div>")
        elif mode in ("FILTRU_AGENT",):
            dec_html = ('<div class="plan-conflict"><strong>FILTRAT DE AGENT</strong> &middot; '
                        f'{decision.get("reason") or ""}</div>')
        elif mode.startswith("EXPLORARE"):
            dec_html = ('<div class="plan-conflict explore"><strong>EMIS PENTRU EXPLORARE</strong> &middot; '
                        f'{decision.get("reason") or ""}</div>')
        else:
            dec_html = '<div class="plan-ok">Plan emis &middot; nicio contradictie Elliott</div>'
    stats_html = ""
    if ew_stats:
        stats_html = (f'<div class="dim conf-note">Istoric masurat: contra Elliott '
                      f'{ew_stats["r_con"]:+.3f}R/plan (n={ew_stats["n_con"]}) &middot; fara conflict '
                      f'{ew_stats["r_rest"]:+.3f}R/plan (n={ew_stats["n_rest"]})</div>')
    return f'''
    <div class="plan-head">
      <span class="symbol">{best["symbol"]}</span>
      <span class="badge badge-{direction_cls}">{best["direction"]}</span>
    </div>
    {dec_html}
    <div class="confidence-row">
      <span class="dim">PROBABILITATE</span>
      <div>{conf_html}</div>
    </div>
    <div class="plan-grid">
      <div><span class="dim">ENTRY</span><br>{fmt_price(plan["entry"])}</div>
      <div><span class="dim">SL</span><br class="sl">{fmt_price(plan["sl"])}</div>
      <div><span class="dim">TP1</span><br>{fmt_price(plan["tp1"])}</div>
      <div><span class="dim">TP2</span><br>{fmt_price(plan["tp2"])}</div>
    </div>
    <div class="expected-r">Expected R &middot; <strong>{plan["expected_r"]}R</strong></div>
    {stats_html}
    '''


# --------------------------------- PAGINA -----------------------------------

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


STATE_STYLE = {
    "OPEN": ("open", "OPEN &middot; WAITING"),
    "TP1_HIT": ("tp1", "TP1 HIT &middot; RUNNING"),
    "TP2_HIT": ("tp2", "TP2 HIT &middot; CLOSED"),
    "SL_HIT": ("sl", "SL &middot; INVALIDATED"),
    "EXPIRED": ("exp", "EXPIRED"),
}


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


CAP_LABELS = {
    "ohlcv": "Lumanari istorice",
    "orderbook_live": "Adancime live",
    "trades_live": "Flux de tranzactii live",
    "orderbook_history": "Order book istoric",
}


def render_token_chart(sym, d, plans_by_symbol):
    """Grafic mic pentru un token scanat, desenat din date DEJA salvate.

    Foloseste `sparkline` (40 de preturi de inchidere) plus nivelurile planului
    si indicatorii care sunt oricum in latest_details.json. Costa zero bytes in
    plus: daca as fi salvat lumanari complete pentru toti cei 28 de tokeni, ar
    fi insemnat ~500 KB pe scanare, comise la fiecare ora.
    """
    vals = [v for v in (d.get("sparkline") or []) if v is not None]
    if len(vals) < 3:
        return '<div class="tk-na">N/A</div>'

    W, H, PAD_R = 420, 110, 96
    plot_w = W - PAD_R
    plan = d.get("plan") or {}
    ind = d.get("indicators") or {}
    vp = ind.get("volume_profile") or {}

    levels = []
    for key, lbl, cls in (("tp2", "TP2", "lv-tp"), ("tp1", "TP1", "lv-tp"),
                          ("entry", "ENTRY", "lv-entry"), ("sl", "SL", "lv-sl")):
        if plan.get(key) is not None:
            levels.append((plan[key], lbl, cls))
    for key, src, lbl in (("vwap", ind, "VWAP"), ("poc", vp, "POC"),
                          ("vah", vp, "VAH"), ("val", vp, "VAL")):
        if src.get(key):
            levels.append((src[key], lbl, "lv-va"))

    lo = min(vals + [l[0] for l in levels])
    hi = max(vals + [l[0] for l in levels])
    rng = (hi - lo) or (hi or 1)
    lo -= rng * 0.05
    hi += rng * 0.05
    rng = hi - lo

    def y(v):
        return H - ((v - lo) / rng) * H

    n = len(vals)
    pts = " ".join(f"{(i/(n-1))*plot_w:.1f},{y(v):.1f}" for i, v in enumerate(vals))
    up = vals[-1] >= vals[0]
    body = [f'<polyline class="{"tk-up" if up else "tk-dn"}" points="{pts}" fill="none"/>']

    levels.sort(key=lambda t: t[0], reverse=True)
    last = -99
    for price, lbl, cls in levels:
        ly = y(price)
        ty = max(ly, last + 10)
        last = ty
        body.append(f'<line class="{cls}" x1="0" y1="{ly:.1f}" x2="{plot_w:.1f}" '
                    f'y2="{ly:.1f}" stroke-dasharray="3 3"/>')
        body.append(f'<text class="lv-t {cls}-t" x="{plot_w + 4}" y="{ty + 3:.1f}">'
                    f'{lbl} {fmt_price(price)}</text>')

    return (f'<svg viewBox="0 0 {W} {H}" class="tk-chart" '
            f'preserveAspectRatio="xMidYMid meet">{"".join(body)}</svg>')


def render_rich_chart(chart, fullscreen_id=None):
    """Graficul curat, cu straturile principale. Randarea propriu-zisa e in
    chart_render.py; functia ramane aici pentru compatibilitate cu apelurile
    existente (graficele per token)."""
    return chart_render.render_chart(chart, chart_render.MAIN_LAYERS, 300,
                                     fullscreen_id=fullscreen_id)

def render_elliott(ew):
    """Ipotezele Elliott concurente, in forma din capturi: Primary /
    Alternative / Secondary, fiecare cu increderea si nivelul de invalidare.

    Ipotezele deja invalidate de pret sunt ARATATE, dar taiate - a le ascunde
    ar face sistemul sa para mai sigur decat e, iar a le lasa neschimbate ar
    sugera ca inca sunt in joc.
    """
    if not ew or not ew.get("counts"):
        return '<div class="tk-na">N/A</div>'
    pr = ew.get("primary")
    head = ""
    if pr:
        cls = "nb-ok" if pr["direction"] == "LONG" else "nb-bad"
        # DIRECTIA ASTEPTATA pe stadiu, nu directia statica a structurii: un
        # impuls in W5 dincolo de tinte anunta corectia, nu continuarea.
        exp = pr.get("expected", pr["direction"])
        w = pr.get("expected_weight", 1.0)
        exp_txt = ("NEUTRU" if w == 0 else exp)
        cls = "nb-neu" if w == 0 else ("nb-ok" if exp == "LONG" else "nb-bad")
        head = ('<div class="ev-neighbors {}"><strong>{}</strong> &middot; '
                'incredere {:.0f}% &middot; {} din {} inca valide</div>'
                '<div class="ew-stage">Urmatoarea miscare asteptata: <strong>{}</strong>'
                ' &middot; {}</div>').format(
                    cls, pr.get("headline") or pr["pattern"], pr["confidence"] * 100,
                    ew.get("alive", 0), ew.get("total", 0), exp_txt,
                    pr.get("stage_text", ""))
    else:
        head = ('<div class="ev-neighbors nb-neu">Toate numaratorile au fost '
                'invalidate de pret - nicio structura in joc.</div>')

    rows = []
    for c in ew["counts"]:
        dead = c.get("invalidated")
        tone = "tag-info" if dead else ("tag-bull" if c["direction"] == "LONG" else "tag-bear")
        rows.append(
            '<div class="ew-row{}"><span class="ew-rank">{}</span>'
            '<span>{}</span><span class="tag {}">{}</span>'
            '<span>{:.0f}%</span><span class="dim">INV {}</span></div>'.format(
                " ew-dead" if dead else "", c["rank"], c["pattern"], tone,
                c["direction"], c["confidence"] * 100, fmt_price(c["invalidation"])))

    tgt = ""
    if pr and pr.get("targets"):
        hit = pr.get("targets_hit") or {}
        parts = " &middot; ".join(
            (f'<s>{k.upper()} {fmt_price(v)}</s> atins' if hit.get(k)
             else f"{k.upper()} {fmt_price(v)}")
            for k, v in pr["targets"].items())
        tgt = f'<div class="ew-targets">Tinte Elliott: {parts}</div>'
    if pr and pr.get("prz"):
        tgt += ('<div class="ew-targets">PRZ: {} - {}</div>'.format(
            fmt_price(pr["prz"]["low"]), fmt_price(pr["prz"]["high"])))

    return head + '<div class="ew-list">' + "".join(rows) + "</div>" + tgt


def render_structure_panel(st):
    """Panoul de structura de piata, in forma din capturile de referinta:
    regim, cross, Ichimoku, aliniere multi-timeframe si zidurile de lichiditate.
    """
    if not st:
        return '<div class="tk-na">N/A</div>'
    out = []

    rg, cr = st.get("regime"), st.get("cross")
    if rg or cr:
        cells = []
        if rg:
            cls = "nb-ok" if rg["vote"] > 0 else ("nb-bad" if rg["vote"] < 0 else "nb-neu")
            cells.append('<div class="ms-cell {}"><span class="ms-lbl">REGIM</span>'
                         '<strong>{}</strong><span class="dim">putere {}{}</span></div>'.format(
                             cls, rg["label"], rg["strength"],
                             f' &middot; ATR {rg["atr_pct"]}%' if rg.get("atr_pct") else ""))
        if cr:
            cls = "nb-ok" if cr["bullish"] else "nb-bad"
            cells.append('<div class="ms-cell {}"><span class="ms-lbl">CROSS EMA 50/200</span>'
                         '<strong>{}</strong><span class="dim">{} vs {}</span></div>'.format(
                             cls, cr["type"], fmt_price(cr["fast"]), fmt_price(cr["slow"])))
        out.append('<div class="ms-grid">' + "".join(cells) + "</div>")

    ich = st.get("ichimoku")
    if ich:
        cls = "nb-ok" if ich["vote"] > 0 else ("nb-bad" if ich["vote"] < 0 else "nb-neu")
        out.append('<div class="ms-cell {} ms-wide"><span class="ms-lbl">ICHIMOKU 9/26/52</span>'
                   '<strong>{}</strong><span class="dim">Tenkan {} &middot; Kijun {} '
                   '&middot; vot {:+.2f}</span></div>'.format(
                       cls, ich["position"], fmt_price(ich["tenkan"]),
                       fmt_price(ich["kijun"]), ich["vote"]))

    mtf = st.get("mtf") or {}
    if mtf.get("rows"):
        rows = []
        for r in mtf["rows"]:
            if r["score"] is None:
                rows.append('<div class="mtf-row"><span>{}</span>'
                            '<span class="dim">N/A</span><span class="dim">-</span></div>'.format(
                                r["timeframe"]))
                continue
            tone = "tag-bull" if r["score"] > 0 else ("tag-bear" if r["score"] < 0 else "tag-info")
            rows.append('<div class="mtf-row"><span>{}</span>'
                        '<span class="tag {}">{}</span><span>{:+d}</span></div>'.format(
                            r["timeframe"], tone, r["trend"], r["score"]))
        head = ""
        if mtf.get("alignment") is not None:
            a = mtf["alignment"]
            cls = "nb-ok" if a > 0.3 else ("nb-bad" if a < -0.3 else "nb-neu")
            head = ('<div class="ev-neighbors {}">Aliniere <strong>{:+.0f}%</strong> '
                    '&middot; {} sus / {} jos din {} timeframe-uri</div>').format(
                        cls, a * 100, mtf["bullish"], mtf["bearish"], mtf["counted"])
        out.append('<h4 class="scan-h">Trend multi-timeframe</h4>' + head
                   + '<div class="mtf-list">' + "".join(rows) + "</div>")

    lq = st.get("liquidity")
    if lq and lq.get("walls"):
        rows = []
        for w in lq["walls"]:
            tone = "tag-bull" if w["side"] == "BID" else "tag-bear"
            rows.append('<div class="liq-row"><span class="tag {}">{} WALL</span>'
                        '<span>{} &middot; {:+.2f}%</span>'
                        '<span class="liq-bar"><i style="width:{}%"></i></span></div>'.format(
                            tone, w["side"], fmt_price(w["price"]),
                            w["distance_pct"] or 0, w["strength"]))
        out.append('<h4 class="scan-h">Zone de lichiditate &middot; order book</h4>'
                   '<div class="ev-neighbors nb-neu">bid {}% / ask {}%</div>'
                   '<div class="liq-list">{}</div>'.format(
                       lq.get("bid_pct"), lq.get("ask_pct"), "".join(rows)))

    return "".join(out) or '<div class="tk-na">N/A</div>'


def render_liquidation(liq, price=None):
    """Zonele magnet, in cuvinte.

    Clusterele dense atrag pretul: lichidarile fortate genereaza ordine in acea
    directie. Deasupra pretului sunt lichidari de SHORT, dedesubt de LONG.
    Cifrele sunt o APROXIMARE construita din profilul de volum si nivelurile de
    levier uzuale - nu open interest real - deci se citesc ca tendinta, nu ca
    masuratoare exacta.
    """
    if not liq or liq.get("bias") is None:
        return '<div class="tk-na">N/A</div>'
    a, b = liq.get("above"), liq.get("below")
    bias = liq["bias"]
    cls = "nb-ok" if bias > 0.05 else ("nb-bad" if bias < -0.05 else "nb-neu")
    rows = []
    if a:
        rows.append('<div class="liq-row"><span class="tag tag-bear">SHORT</span>'
                    '<span>{:+.1f}% &middot; {}</span>'
                    '<span class="liq-bar"><i style="width:{}%"></i></span></div>'.format(
                        a["distance_pct"], fmt_price(a["price"]),
                        int(round(100 * a.get("intensity", 0)))))
    if b:
        rows.append('<div class="liq-row"><span class="tag tag-bull">LONG</span>'
                    '<span>{:+.1f}% &middot; {}</span>'
                    '<span class="liq-bar"><i style="width:{}%"></i></span></div>'.format(
                        b["distance_pct"], fmt_price(b["price"]),
                        int(round(100 * b.get("intensity", 0)))))
    note = ("magnetul dominant e DEASUPRA" if bias > 0.05 else
            ("magnetul dominant e DEDESUBT" if bias < -0.05 else "magneti echilibrati"))
    scaled = " &middot; scalat cu open interest" if liq.get("oi_scaled") else ""
    return ('<div class="ev-neighbors {}">Magnet: <strong>{}</strong> '
            '&middot; bias {:+.2f}{}</div>'
            '<div class="liq-list">{}</div>').format(
                cls, note, bias, scaled, "".join(rows))


def render_exchange_scan(scan, label):
    """Ce vede o bursa ACUM: lista ei de simboluri, semnalele si indicatorii.

    Planurile se creeaza doar pe bursa activa - vezi nota din scanner. Aici e
    strict afisare, ca sa poti compara ce arata fiecare bursa fara sa imparti
    memoria agentului in cinci.
    """
    if not scan:
        return '<p class="dim">Nicio scanare inregistrata pentru aceasta bursa.</p>'
    if scan.get("error"):
        return ('<div class="ex-fail">Scanarea nu a putut rula.</div>'
                '<div class="ex-err">{}</div>'.format(scan["error"]))

    res = scan.get("results") or []
    resolved = scan.get("resolved") or []
    missing = scan.get("missing") or []
    head = ('<div class="scan-head"><strong>{}</strong> simboluri gasite aici'
            '{} &middot; <strong>{}</strong> cu semnal{}</div>').format(
        len(resolved),
        (' &middot; <span class="dim">{} lipsesc: {}</span>'.format(
            len(missing), ", ".join(missing[:8]) + ("..." if len(missing) > 8 else "")))
        if missing else "",
        len(res),
        ' <span class="tab-used">activa - aici se creeaza planuri</span>'
        if scan.get("primary") else
        ' <span class="dim">(doar afisare)</span>')

    if scan.get("truncated"):
        head += ('<div class="ex-note">Scanare trunchiata la limita de timp - '
                 'bursele secundare au buget fix ca sa nu intinda rularea.</div>')
    if not res:
        return head + '<p class="dim">Niciun semnal pe aceasta bursa acum.</p>'

    det = scan.get("details") or {}
    rows = []
    for r in res[:20]:
        d = det.get(r["symbol"]) or {}
        st = d.get("supertrend")
        rows.append(
            '<div class="scan-row">'
            '<span class="scan-sym">{}</span>'
            '<span class="badge badge-{}">{}</span>'
            '<span class="scan-score">{}</span>'
            '<span class="scan-px">{}</span>'
            '<span class="tag tag-{}">{}</span>'
            '<span class="dim scan-pos">{}</span>'
            '</div>'.format(
                r["symbol"], "bull" if r["direction"] == "LONG" else "bear",
                r["direction"], r["score"], fmt_price(r["price"]),
                "bull" if st == "BULLISH" else ("bear" if st == "BEARISH" else "info"),
                st or "-", d.get("position") or ""))
    return head + '<div class="scan-list">' + "".join(rows) + "</div>"


def render_exchange_tabs(store, scans_store=None):
    """Tab-uri per bursa, cu starea fiecareia.

    Fara JavaScript: radio ascuns + label, cu selectorul :checked din CSS. Merge
    in orice browser si pe telefon, si nu depinde de niciun CDN.
    """
    cards = (store or {}).get("exchanges") or []
    if not cards:
        return '<p class="dim">Bursele apar dupa prima scanare.</p>'

    active_sig = (store or {}).get("active_signature")
    used = (store or {}).get("used")

    tabs, panels = [], []
    for i, c in enumerate(cards):
        eid = c.get("id", f"ex{i}")
        ok = c.get("connected")
        checked = " checked" if i == 0 else ""
        dot = "ok" if ok else "bad"
        badge = ' <span class="tab-used">activa</span>' if eid == used else ""
        tabs.append(
            '<input type="radio" name="extab" id="tab-{0}" class="tab-radio"{1}>'
            '<label for="tab-{0}" class="tab-label"><i class="dot-{2}"></i>{3}{4}</label>'.format(
                eid, checked, dot, c.get("label", eid), badge))

        if not ok:
            body = ('<div class="ex-fail">Nu m-am putut conecta.</div>'
                    '<div class="ex-err">{}</div>'.format(c.get("error") or "motiv necunoscut"))
        else:
            avail = set(c.get("available") or [])
            rows = []
            for cap in c.get("declared") or []:
                has = cap in avail
                rows.append(
                    '<div class="cap-row"><span class="tag tag-{}">{}</span>'
                    '<span>{}</span></div>'.format(
                        "bull" if has else "bear",
                        "disponibil" if has else "indisponibil",
                        CAP_LABELS.get(cap, cap)))
            missing = [CAP_LABELS.get(cap, cap) for cap in c.get("declared") or []
                       if cap not in avail]
            note = ""
            if missing:
                note = ('<div class="ex-note">Evidentele care depind de: {} '
                        'sunt OMISE pentru aceasta bursa - nu inlocuite cu valori '
                        'neutre, care ar minti modelul despre ce a vazut.</div>').format(
                            ", ".join(missing))
            body = ('<div class="ex-meta"><div><span class="dim">PIETE</span><br>{}</div>'
                    '<div><span class="dim">VERIFICAT</span><br>{}</div></div>'
                    '<div class="cap-list">{}</div>{}').format(
                        c.get("markets", 0), c.get("checked_at", "-"), "".join(rows), note)
        # scanul bursei, sub fisa de capabilitati
        scan = ((scans_store or {}).get("scans") or {}).get(eid)
        body += ('<h4 class="scan-h">Ce vede aceasta bursa acum</h4>'
                 + render_exchange_scan(scan, c.get("label", eid)))
        panels.append('<div class="tab-panel tab-panel-{}">{}</div>'.format(eid, body))
    # Regula CSS care leaga fiecare radio de panoul lui. Generata dinamic, ca
    # sa nu depinda de o lista fixa de burse.
    rules = "".join(
        "#tab-{0}:checked ~ .tab-panel-{0}{{display:block;}}".format(c.get("id", f"ex{i}"))
        for i, c in enumerate(cards))
    panels.append("<style>" + rules + "</style>")

    sig = ""
    if active_sig:
        # Textul vechi ("se invata separat") descria comportamentul de dinainte de
        # familia de geometrie. Acum planurile cu capabilitati diferite se invata
        # IMPREUNA; evidentele indisponibile sunt doar omise din vector.
        sig = ('<div class="ex-sig">Semnatura activa: <strong>{}</strong> '
               '&middot; planurile cu capabilitati diferite se invata impreuna, pe '
               'aceeasi familie de geometrie; evidentele indisponibile sunt omise, '
               'nu inlocuite</div>').format(active_sig)
    return '<div class="tabs">' + "".join(tabs) + "".join(panels) + "</div>" + sig


def render_evidence(plan):
    """Lista de evidente, exact cum a fost la momentul deciziei.

    Citeste din `plan["evidence"]` - memoria agentului - nu recalculeaza nimic.
    Daca dashboard-ul si-ar calcula propriile valori, ar arata starea de ACUM,
    nu pe cea din care agentul a invatat, iar cele doua ar diverge in timp.
    """
    ev = (plan or {}).get("evidence") or []
    if not ev:
        return '<p class="dim">Fara evidente inregistrate (plan din geometrie anterioara).</p>'
    d = plan.get("direction")
    rows = []
    for e in ev:
        if e["direction"] == d:
            cls, mark = "ok", "sustine"
        elif e["direction"] == "NEUTRU":
            cls, mark = "neu", "context"
        else:
            cls, mark = "bad", "contrazice"
        bar = int(round(e["strength"] * 100))
        rows.append(
            '<div class="ev-row ev-{}"><span class="ev-mark">{}</span>'
            '<span class="ev-label">{}</span>'
            '<span class="ev-bar"><i style="width:{}%"></i></span></div>'.format(
                cls, mark, e["label"], bar))

    f = plan.get("fusion") or {}
    head = ""
    if f.get("score") is not None:
        head = ('<div class="ev-fusion">Evidente: <strong>{}</strong> sustin, '
                '<strong>{}</strong> contrazic, {} context '
                '&middot; aliniere <strong>{}%</strong></div>').format(
                    f["support"], f["oppose"], f["neutral"], f["score"])

    n = plan.get("neighbors") or {}
    nb = ""
    if n.get("verdict"):
        nb = ('<div class="ev-neighbors nb-{}">Intrari comparabile: <strong>{}</strong> '
              '&middot; {}</div>').format(
                  "ok" if n["verdict"] == "FAVORABIL" else
                  ("bad" if n["verdict"] == "NEFAVORABIL" else "neu"),
                  n["verdict"], n["reason"])
    elif n.get("reason"):
        nb = '<div class="ev-neighbors nb-neu dim">{}</div>'.format(n["reason"])

    return head + '<div class="ev-list">' + "".join(rows) + "</div>" + nb


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


def render_token_details(details, plans_store, watchlist=None):
    """Un panou pliabil per simbol scanat. Foloseste <details>/<summary> nativ:
    zero JavaScript, merge in orice browser, se deschide cu un tap pe telefon,
    si ramane inchis implicit ca pagina sa nu devina grea."""
    symbols = (details or {}).get("symbols") or {}
    if not symbols and not watchlist:
        return '<p class="dim">Detaliile apar dupa prima scanare cu semnale.</p>'

    # istoricul de planuri pe simbol, ca sa vezi ce a facut agentul pe fiecare
    by_symbol = {}
    for p in (plans_store or {}).get("plans", []):
        by_symbol.setdefault(p["symbol"], []).append(p)

    blocks = []
    for sym, d in sorted(symbols.items(), key=lambda kv: -(kv[1].get("score") or 0)):
        direction = d.get("direction", "")
        dcls = "bull" if direction == "LONG" else "bear"
        ind = d.get("indicators") or {}
        st = ind.get("supertrend") or {}
        vp = ind.get("volume_profile") or {}
        macd = ind.get("macd") or {}
        plan = d.get("plan") or {}
        prob_txt, prob_note = honest_probability(d.get("score"), plans_store)
        # Grafic BOGAT pentru fiecare token cu semnal, nu doar pentru cel mai
        # bun candidat: aceleasi lumanari, niveluri, unde Elliott si lichiditate.
        # Cade inapoi pe graficul mic din sparkline daca lumanarile lipsesc -
        # de exemplu pentru un token adus de pe o bursa secundara.
        if d.get("candles"):
            _tc = {"symbol": sym, "direction": d.get("direction", ""),
                   "timeframe": (details or {}).get("timeframe", ""),
                   "candles": d["candles"], "ema20": d.get("ema20"),
                   "ema50": d.get("ema50"),
                   "indicators": {
                       "vwap": (d.get("indicators") or {}).get("vwap"),
                       "poc": ((d.get("indicators") or {}).get("volume_profile") or {}).get("poc"),
                       "vah": ((d.get("indicators") or {}).get("volume_profile") or {}).get("vah"),
                       "val": ((d.get("indicators") or {}).get("volume_profile") or {}).get("val"),
                       "supertrend": (d.get("indicators") or {}).get("supertrend")},
                   "current": d.get("plan"),
                   "macd": (d.get("indicators") or {}).get("macd"),
                   "elliott": d.get("elliott"),
                   "forecast": d.get("forecast"),
                   "liq_structure": d.get("liq_structure")}
            # FARA maximizare pe graficele per token: suprapunerea duplica
            # intregul SVG, iar la 30 de tokenuri asta inseamna ~2.4 MB de
            # pagina, descarcati chiar daca panourile sunt pliate. Graficul
            # principal pastreaza maximizarea; astea sunt oricum in panouri
            # care se deschid la cerere.
            chart_html = render_rich_chart(_tc)
        else:
            chart_html = render_token_chart(sym, d, by_symbol)
        liq_html = render_liquidation(d.get("liquidation"), d.get("price"))
        struct_html = render_structure_panel(d.get("structure_panel"))
        ew_html = render_elliott(d.get("elliott"))

        rows = []
        if st:
            rows.append(("SuperTrend", f'{st["direction"]} @ {fmt_price(st["level"])}',
                         "bull" if st["direction"] == "BULLISH" else "bear"))
        if ind.get("vwap"):
            rows.append(("VWAP", fmt_price(ind["vwap"]), "info"))
        if vp:
            rows.append(("POC / VAH / VAL",
                         f'{fmt_price(vp["poc"])} / {fmt_price(vp["vah"])} / {fmt_price(vp["val"])}', "warn"))
        if macd:
            rows.append(("MACD hist", f'{macd["histogram"]:+.6f}',
                         "bull" if macd.get("bullish") else "bear"))
        if ind.get("price_vs_value_area"):
            rows.append(("Pozitie", ind["price_vs_value_area"], "info"))
        ind_html = "".join(
            f'<div class="ind-row"><span class="tag tag-{c}">{k}</span><span>{v}</span></div>'
            for k, v, c in rows)

        emas = ind.get("emas") or {}
        ema_html = " &middot; ".join(f'{k.replace("ema", "EMA ")} {fmt_price(v)}'
                                     for k, v in emas.items() if v is not None)

        comp_html = "".join(
            f'<div class="fib-row"><span>{k}</span><span>{v:.2f}</span></div>'
            for k, v in (d.get("components") or {}).items())

        plan_html = ""
        if plan:
            risk = abs(plan["entry"] - plan["sl"]) or 1
            plan_html = f'''<div class="plan-grid" style="margin-top:8px;">
          <div><span class="dim">ENTRY</span><br>{fmt_price(plan["entry"])}</div>
          <div><span class="dim">SL</span><br>{fmt_price(plan["sl"])}</div>
          <div><span class="dim">TP1</span><br>{fmt_price(plan["tp1"])}</div>
          <div><span class="dim">TP2</span><br>{fmt_price(plan["tp2"])}</div>
        </div>
        <div class="dim" style="margin-top:6px;">
          TP1 la {abs(plan["tp1"]-plan["entry"])/risk:.2f}R &middot;
          TP2 la {abs(plan["tp2"]-plan["entry"])/risk:.2f}R
        </div>'''

        hist = by_symbol.get(sym, [])
        closed = [p for p in hist if p.get("realized_r") is not None]
        hist_html = '<p class="dim">Niciun plan inca pe acest simbol.</p>'
        if hist:
            tot = sum(p["realized_r"] for p in closed)
            wins = [p for p in closed if p["realized_r"] > 0]
            # Valorile se calculeaza INAINTE, in variabile simple. F-string-uri
            # imbricate cu aceleasi ghilimele sunt valide doar din Python 3.12
            # (PEP 701); workflow-ul ruleaza pe 3.11, unde sunt eroare de sintaxa.
            row_parts = []
            for pl_ in sorted(hist, key=lambda x: -x["id"])[:6]:
                r_val = pl_.get("realized_r")
                r_txt = "-" if r_val is None else "{:+.2f}R".format(r_val)
                r_cls = "r-pos" if (r_val or 0) > 0 else "r-neg"
                state_txt = pl_.get("state_detail") or pl_.get("state", "")
                row_parts.append(
                    '<div class="liq-row">'
                    '<span>#{}</span><span>{}</span>'
                    '<span class="dim">{}</span>'
                    '<span class="{}">{}</span></div>'.format(
                        pl_["id"], pl_["direction"], state_txt[:26], r_cls, r_txt))
            rows_h = "".join(row_parts)
            summary_h = (f'{len(closed)} inchise &middot; {100*len(wins)/len(closed):.0f}% castig '
                         f'&middot; {tot:+.2f}R') if closed else f'{len(hist)} deschise'
            hist_html = f'<div class="dim" style="margin-bottom:6px;">{summary_h}</div>' \
                        f'<div class="liq-list">{rows_h}</div>'

        blocks.append(f'''<details class="tok">
      <summary>
        <span class="tok-sym">{sym}</span>
        <span class="badge badge-{dcls}">{direction}</span>
        <span class="tok-score">{d.get("score")}/100</span>
        <span class="tok-spark">{render_sparkline(d.get("sparkline"))}</span>
      </summary>
      <div class="tok-body">
        <div class="tok-meta">
          <div><span class="dim">PRET</span><br>{fmt_price(d.get("price"))}</div>
          <div><span class="dim">ATR</span><br>{fmt_price(d.get("atr"))}</div>
          <div><span class="dim">PERSISTENTA</span><br>{d.get("persistence")}</div>
          <div><span class="dim">PROBABILITATE</span><br>{prob_txt}<br><span class="dim" style="font-size:10px;">{prob_note}</span></div>
        </div>
        <h4>Grafic</h4>{chart_html}
        <h4>Elliott Wave</h4>{ew_html}
        <h4>Structura de piata</h4>{struct_html}
        <h4>Zone de lichidare</h4>{liq_html}
        <h4>Plan propus</h4>{plan_html}
        <h4>Indicatori</h4>{ind_html}
        <div class="ema-line dim">{ema_html}</div>
        <h4>Componentele scorului</h4><div class="fib-list">{comp_html}</div>
        <h4>Istoricul planurilor pe acest simbol</h4>{hist_html}
      </div>
    </details>''')

    # Simbolurile din watchlist pentru care agentul NU a putut aduce date:
    # panou gol cu N/A, fara alte informatii. A inventa valori sau a le omite
    # tacut ar ascunde faptul ca acoperirea e incompleta.
    scanned_bases = set()
    for sym in symbols:
        scanned_bases.add(sym.split("/")[0])
    for base in (watchlist or []):
        if base in scanned_bases:
            continue
        blocks.append(
            '<details class="tok tok-na"><summary>'
            '<span class="tok-sym">{}</span>'
            '<span class="badge badge-na">N/A</span>'
            '<span class="tok-score">-</span></summary>'
            '<div class="tok-body"><div class="tk-na">N/A</div></div>'
            '</details>'.format(base))

    return "".join(blocks)


def render_briefing(brief):
    if not brief or not brief.get("text"):
        return ""
    src = brief.get("source", "determinist")
    tag = "Gemini" if src == "gemini" else "generat din statistici"
    return f'''<div class="card briefing-card">
    <h2>Briefing &middot; <span class="dim">{tag}</span></h2>
    <p class="briefing-text">{brief["text"]}</p>
  </div>'''


def render_plan_memory(store):
    """Memoria de planuri: fiecare plan numerotat, cu starea lui si R realizat."""
    plans = (store or {}).get("plans") or []
    if not plans:
        return '<p class="dim">Niciun plan inca &mdash; apar la prima scanare cu semnale.</p>'

    summary = store.get("summary") or {}
    head = ""
    if summary.get("closed"):
        pf = summary.get("profit_factor")
        head = f'''<div class="plan-summary">
      <div><span class="dim">R TOTAL</span><br>{summary["total_r"]:+.2f}R</div>
      <div><span class="dim">RATA</span><br>{summary["win_rate"]}%</div>
      <div><span class="dim">R MEDIU</span><br>{summary["avg_r"]:+.3f}R</div>
      <div><span class="dim">PROFIT FACTOR</span><br>{pf if pf is not None else "&mdash;"}</div>
    </div>'''
        # Cifrele de sus amesteca LIVE si BACKTEST (in productie >99% backtest).
        # Afisez separat rezultatul live, cu incertitudinea lui, ca sa nu para ca
        # sistemul a produs live R-ul cumulat al backtest-ului.
        lv, bt = summary.get("live") or {}, summary.get("backtest") or {}
        if lv.get("closed") or bt.get("closed"):
            def _ln(tag, d):
                if not d.get("closed"):
                    return f"<strong>{tag}</strong>: niciun plan inchis inca"
                ci = (f' (IC95 {d["avg_r_ci_low"]:+.3f}..{d["avg_r_ci_high"]:+.3f}R)'
                      if d.get("avg_r_ci_low") is not None else "")
                return (f'<strong>{tag}</strong>: {d["closed"]} inchise'
                        + (f' din {d["since"]}' if d.get("since") else "")
                        + f' &middot; castig {d["win_rate"]}% (IC {d["wr_ci_low"]}-{d["wr_ci_high"]}%)'
                        f' &middot; R mediu {d["avg_r"]:+.3f}R{ci} &middot; total {d["total_r"]:+.2f}R')
            head += (f'<p class="dim" style="margin:-6px 0 12px;">Totalul de mai sus include backtest-ul. '
                     f'{_ln("LIVE", lv)}<br>{_ln("BACKTEST", bt)}</p>')
            # ALARMA DE DIVERGENTA live - backtest (test statistic pe diferenta)
            dv = summary.get("divergence") or {}
            if dv.get("status") == "sub_backtest":
                head += ('<div class="plan-conflict"><strong>ALARMA: REZULTATELE LIVE SUNT SUB BACKTEST</strong> '
                         f'&middot; diferenta {dv["diff"]:+.3f}R/plan (IC95 {dv["ci_low"]:+.3f}..{dv["ci_high"]:+.3f}) '
                         f'pe {dv["n_live"]} planuri live - semnificativa statistic. Agentul ramane in SHADOW pana la '
                         'confirmarea live; cauza se investigheaza pe datele live, nu prin filtre nevalidate.</div>')
            elif dv.get("status") == "in_marja":
                head += (f'<p class="dim" style="margin:-6px 0 12px;">Live fata de backtest: {dv["diff"]:+.3f}R/plan '
                         f'(IC95 {dv["ci_low"]:+.3f}..{dv["ci_high"]:+.3f}) - in marja statistica.</p>')

    cards = []
    for p in sorted(plans, key=lambda x: x["id"], reverse=True)[:12]:
        cls, label = STATE_STYLE.get(p["state"], ("open", p["state"]))
        r = p.get("realized_r")
        # Un plan care a atins TP1 si a iesit la breakeven are starea SL_HIT dar
        # R POZITIV. Colorarea dupa stare il arata rosu desi a facut bani - deci
        # culoarea urmeaza semnul lui R, care e adevarul economic.
        if r is not None:
            cls = "tp2" if r > 0 else "sl"
        r_txt = f'<span class="plan-r r-{"pos" if r and r > 0 else "neg"}">{r:+.2f}R</span>' if r is not None else ""
        mode = (p.get("decision") or {}).get("mode", "")
        cards.append(f'''<div class="plan-card plan-{cls}">
      <div class="plan-head-row"><strong>PLAN #{p["id"]} &middot; {p["symbol"]}</strong>{r_txt}</div>
      <div class="plan-state">{p["direction"]} &middot; {label}</div>
      <div class="plan-lv">entry {fmt_price(p["entry"])} &middot; SL {fmt_price(p["sl"])} &middot; TP1 {fmt_price(p["tp1"])}</div>
      <div class="dim" style="margin-top:4px;">{p["created_time"]} &middot; {mode}</div>
    </div>''')
    return head + '<div class="plan-grid-cards">' + "".join(cards) + "</div>"


def render_calibration(store):
    """Probabilitatea MASURATA pe intervale de scor, nu formula."""
    cal = (store or {}).get("calibration") or {}
    if not cal:
        return '<p class="dim">Se calibreaza dupa primele planuri inchise.</p>'
    rows = []
    for b in sorted(cal, key=int):
        e = cal[b]
        badge = "reliable" if e["reliable"] else "thin"
        txt = (f'{e["win_rate"]}% <span class="dim">(IC {e["ci_low"]}-{e["ci_high"]}%)</span>'
               if e["reliable"] else f'<span class="dim">n={e["total"]}, prea putine date</span>')
        rows.append(f'''<div class="cal-row cal-{badge}">
      <span>scor {b}-{int(b)+19}</span><span>{txt}</span>
      <span class="dim">{e["avg_r"]:+.2f}R</span></div>''')
    return '<div class="cal-list">' + "".join(rows) + "</div>"


def render_agent_card(agent_state):
    if not agent_state or not agent_state.get("agent", {}).get("total"):
        return ('<p class="dim">Agentul nu s-a antrenat inca &mdash; ruleaza ai_agent.py '
                'dupa prima scanare cu rezultate evaluate.</p>')

    a = agent_state["agent"]
    status = agent_state.get("status", "SHADOW")
    reason = agent_state.get("status_reason", "")
    ba = agent_state.get("balanced_agent")
    bb = agent_state.get("balanced_baseline")
    status_cls = "ok" if status == "ACTIVE" else "warn"

    dir_rows = ""
    for d in ("LONG", "SHORT"):
        s = (agent_state.get("by_direction") or {}).get(d, {})
        if s.get("total"):
            dir_rows += (
                f'<div class="liq-row"><span>{d}</span>'
                f'<span>agent {100*s["agent"]/s["total"]:.0f}%</span>'
                f'<span class="dim">eur. {100*s["baseline"]/s["total"]:.0f}%</span></div>'
            )

    weights = (agent_state.get("model") or {}).get("weights", {})
    w_rows = "".join(
        f'<div class="fib-row"><span>{k}</span><span>{v:+.2f}</span></div>'
        for k, v in weights.items()
    )

    # "euristica" = min(50 + 0.35*scor, 88)%, mereu >= 50% -> prezice mereu castig,
    # deci acuratetea ei e doar rata de castig. Pragul care conteaza e clasa
    # majoritara (mereu pierdere); ordonarea se vede in AUC fata de scorul brut.
    maj = agent_state.get("majority_baseline")
    auc_a, auc_s = agent_state.get("auc"), agent_state.get("auc_score_baseline")
    bal_line = ((f'{ba:.1f}% vs euristica {bb:.1f}% (euristica prezice mereu castig)'
                 + (f' &middot; prag real, mereu pierdere: {maj:.1f}%' if maj is not None else ""))
                if ba is not None else "in curs de acumulare")
    if auc_a is not None and auc_s is not None:
        bal_line += (f'<br><span class="dim">ordonare AUC pe evaluarea dominata de backtest: agent {auc_a:.3f} '
                     f'vs scor brut {auc_s:.3f} (0.5 = hazard)</span>')
    # ORDONAREA LIVE: probabilitatea data de agent la momentul deciziei fata de
    # rezultatul real. Statusul ACTIVE cere confirmarea ei, nu doar backtest-ul.
    lv = agent_state.get("live") or {}
    if lv.get("auc") is not None:
        conf_live = lv.get("ci_low") is not None and lv["ci_low"] > 0.5 and lv.get("n", 0) >= 100
        bal_line += (f'<br><strong>LIVE</strong>: AUC {lv["auc"]:.3f} (IC95 {lv.get("ci_low")}-{lv.get("ci_high")}) '
                     f'pe {lv["n"]} planuri live &middot; R mediu {lv.get("avg_r", 0):+.3f}R &middot; '
                     + ("ordonare confirmata live" if conf_live else
                        "neconfirmata inca: filtrul cere 100 de planuri live si IC peste 0.5"))
    elif lv.get("n") is not None:
        bal_line += f'<br><strong>LIVE</strong>: {lv["n"]} planuri live evaluate - prea putine pentru AUC'
    ex = agent_state.get("skew_excluded") or []
    if ex:
        sk = agent_state.get("skew") or {}
        bal_line += ('<br><span class="dim">excluse din model (nu exista la fel in backtest): '
                     + ", ".join(f'{k}{" (" + str(sk[k][0]) + "% live / " + str(sk[k][1]) + "% backtest)" if k in sk else ""}'
                                 for k in ex) + '</span>')

    return f'''
    <div class="agent-status agent-{status_cls}">
      <strong>{status}</strong> &middot; {reason}
    </div>
    <div class="agent-metrics">
      <div><span class="dim">Exemple invatate</span><br>{a["total"]}</div>
      <div><span class="dim">Zile acoperite</span><br>{agent_state.get("days_covered", 0)}</div>
    </div>
    <p class="dim" style="margin:12px 0 6px;">Acuratete echilibrata (media LONG/SHORT)</p>
    <div class="agent-balanced">{bal_line}</div>
    <div class="liq-list" style="margin-top:10px;">{dir_rows}</div>
    <p class="dim" style="margin:14px 0 6px;">Greutati invatate din date</p>
    <div class="fib-list">{w_rows}</div>
    '''


def render_learning_curve(history, weights_history, health, agent_state=None):
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

    agent_block = ""
    curve = (agent_state or {}).get("curve") or []
    if len(curve) >= 2:
        agent_svg = render_line_chart_svg({
            "agent": [p["agent"] for p in curve],
            "baseline": [p["baseline"] for p in curve],
        }, y_min=0, y_max=100)
        agent_block = f'''
    <div class="lc-block">
      <h3>Agent AI vs euristica <span class="dim">(acuratete cumulativa)</span></h3>
      {agent_svg}
      <div class="legend">
        <span><i class="dot" style="background:var(--ema20)"></i>agent AI</span>
        <span><i class="dot" style="background:var(--bull)"></i>euristica</span>
      </div>
    </div>'''

    return f'''
    <div class="lc-block">
      <h3>Hit-rate cumulativ <span class="dim">({health["evaluated"]} semnale evaluate)</span></h3>
      {hit_svg}
    </div>
    <div class="lc-block">
      <h3>Evolutia ponderilor adaptive</h3>
      {w_svg}
      <div class="legend">{legend}</div>
    </div>{agent_block}
    '''


def build_html(scan, best, deep, chart, health, weights, session, token_meta, narrative, history, weights_history, agent_state, plans_store, briefing, details, exchanges_store,
               exchange_scans=None):
    _dec = (load_json(os.path.join(DATA_DIR, "decisions.json"), {}).get("decisions") or {})
    plan_html = render_plan(best, deep, (plans_store or {}).get("calibration"),
                            _dec.get((best or {}).get("symbol")),
                            elliott_outcome_stats(plans_store))
    levels_html = render_levels(deep)
    liquidity_html = render_liquidity(deep)
    selfcheck_html = render_self_check(load_json(os.path.join(DATA_DIR, "self_check.json"), None),
                                       load_json(os.path.join(DATA_DIR, "repair_log.json"), []))
    altseason_html = render_altseason(load_json(ALTSEASON_FILE, None),
                                      load_json(ALTSEASON_HISTORY_FILE, []))
    similar_html = render_similar_projects(
        token_meta, narrative, symbol=(best or {}).get("symbol"),
        updated=(TOKEN_META_UPDATED or {}).get("when"))
    learning_curve_html = render_learning_curve(history, weights_history, health, agent_state)
    agent_html = render_agent_card(agent_state)
    plans_html = render_plan_memory(plans_store)
    _all = (plans_store or {}).get("plans") or []
    _latest = max(_all, key=lambda p: p.get("id", 0)) if _all else None
    evidence_html = render_evidence(_latest)
    exchanges_html = render_exchange_tabs(exchanges_store, exchange_scans)
    evidence_symbol = (_latest or {}).get("symbol", "-")
    calibration_html = render_calibration(plans_store)
    indicators_html = render_indicators(deep)
    briefing_html = render_briefing(briefing)
    # Watchlist-ul cerut: din scanarea bursei principale, care stie si ce a
    # gasit si ce lipseste. Asa panourile N/A reflecta ce s-a cerut efectiv,
    # nu o lista fixata in dashboard care ar putea ramane in urma.
    _pri = (exchange_scans or {}).get("primary")
    _pscan = ((exchange_scans or {}).get("scans") or {}).get(_pri) or {}
    watchlist = sorted({s_.split("/")[0] for s_ in (_pscan.get("resolved") or [])}
                       | set(_pscan.get("missing") or []))
    tokens_html = render_token_details(details, plans_store, watchlist)
    chart_svg = chart_render.render_components(chart, "main")
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
{chart_render.CSS}
:root{{
  /* FUNDAL ALB, ca in capturile de referinta. Culorile de semnal urmeaza
     conventia TradingView (verde #089981, rosu #F23645), iar tonurile de
     "amber" sunt inchise, ca textul sa ramana lizibil pe alb. */
  --bg:#F5F7FA; --panel:#FFFFFF; --panel-2:#F1F4F8; --border:#E3E8EF;
  --text:#0F172A; --text-dim:#64748B;
  --bull:#089981; --bear:#F23645; --amber:#B45309;
  --ema9:#26A69A; --ema20:#F57C00; --ema50:#E53935; --ema200:#1565C0;
  --ew0:#7E57C2; --ew1:#1E88E5; --ew2:#FB8C00;
  --info:#0288D1;
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

.agent-status{{font-family:var(--font-mono);font-size:12px;padding:8px 10px;
  border-radius:6px;margin-bottom:12px;line-height:1.5;}}
.agent-ok{{background:rgba(52,211,153,.12);color:var(--bull);}}
.agent-warn{{background:rgba(230,180,80,.12);color:var(--amber);}}
.agent-metrics{{display:grid;grid-template-columns:1fr 1fr;gap:10px;
  font-family:var(--font-mono);font-size:14px;}}
.agent-metrics div{{background:var(--panel-2);border-radius:7px;padding:8px 10px;}}
.tok{{background:var(--panel-2);border-radius:8px;margin-bottom:8px;
  border-left:3px solid var(--border);overflow:hidden;}}
.tok[open]{{border-left-color:var(--amber);}}
.tok summary{{display:flex;align-items:center;gap:10px;padding:11px 13px;cursor:pointer;
  list-style:none;font-family:var(--font-mono);font-size:13px;}}
.tok summary::-webkit-details-marker{{display:none;}}
.tok summary::before{{content:"+";color:var(--text-dim);font-weight:700;width:10px;flex:none;}}
.tok[open] summary::before{{content:"-";}}
.tok-sym{{font-weight:700;flex:none;}}
.tok-score{{color:var(--text-dim);margin-left:auto;flex:none;}}
.tok-spark{{width:70px;height:24px;flex:none;display:block;}}
.spark{{width:100%;height:100%;display:block;}}
.tok-body{{padding:0 13px 14px;border-top:1px solid var(--border);}}
.tok-body h4{{font-size:10px;text-transform:uppercase;letter-spacing:.07em;
  color:var(--text-dim);margin:14px 0 6px;font-weight:600;}}
.tok-meta{{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-top:12px;
  font-family:var(--font-mono);font-size:13px;}}
@media(min-width:520px){{.tok-meta{{grid-template-columns:repeat(4,1fr);}}}}
.tok-meta div{{background:var(--panel);border-radius:6px;padding:7px 9px;}}
.briefing-card{{margin-bottom:14px;border-left:3px solid var(--amber);}}
.briefing-text{{font-size:14px;line-height:1.7;margin:0;}}
.fs-btn{{margin-left:auto;font-size:10px;font-family:var(--font-mono);
  padding:3px 9px;border-radius:5px;background:var(--panel-2);color:var(--amber);
  text-decoration:none;border:1px solid var(--border);}}
.chart-head{{display:flex;align-items:center;gap:6px;}}
.fs-overlay{{display:none;}}
.fs-overlay:target{{display:flex;position:fixed;inset:0;z-index:99;
  background:var(--bg);flex-direction:column;padding:12px;overflow:auto;}}
.fs-inner{{flex:1;display:flex;align-items:center;justify-content:center;}}
.fs-inner svg{{width:100%;height:auto;max-height:92vh;}}
.fs-close{{align-self:flex-end;font-size:12px;font-family:var(--font-mono);
  padding:7px 14px;border-radius:6px;background:var(--panel-2);
  color:var(--amber);text-decoration:none;border:1px solid var(--border);
  margin-bottom:8px;}}
.proj-line{{stroke:var(--ew0);stroke-width:1.5;stroke-dasharray:5 4;opacity:.85;}}
.proj-dot{{fill:var(--panel);stroke:var(--ew0);stroke-width:1.3;}}
.proj-t{{font-family:var(--font-mono);font-size:7.5px;font-weight:700;fill:var(--ew0);}}
.proj-tag{{font-family:var(--font-mono);font-size:7px;fill:var(--ew0);opacity:.9;}}
.proj-now{{stroke:var(--text-dim);stroke-width:.8;stroke-dasharray:2 3;opacity:.6;}}
.lv-liqb{{stroke:var(--amber);stroke-width:1.1;}}
.lv-liqs{{stroke:var(--amber);stroke-width:1.1;opacity:.8;}}
.lv-liqb-t,.lv-liqs-t{{fill:var(--amber);font-size:6.5px;}}
.lv-ew{{stroke:var(--ew0);stroke-width:1;}}
.lv-ew-t{{fill:var(--ew0);}}
.ew-line-0{{stroke:var(--ew0);stroke-width:1.6;}}
.ew-line-1{{stroke:var(--ew1);stroke-width:1.3;stroke-dasharray:4 3;}}
.ew-line-2{{stroke:var(--ew2);stroke-width:1.1;stroke-dasharray:2 4;}}
.ew-dead-line{{stroke:var(--text-dim);stroke-width:.9;stroke-dasharray:1 5;opacity:.45;}}
.ew-line-0-dot{{fill:var(--ew0);}} .ew-line-1-dot{{fill:var(--ew1);}}
.ew-line-2-dot{{fill:var(--ew2);}} .ew-dead-line-dot{{fill:var(--text-dim);opacity:.45;}}
.ew-line-0-t{{fill:var(--ew0);}} .ew-line-1-t{{fill:var(--ew1);}}
.ew-line-2-t{{fill:var(--ew2);}} .ew-dead-line-t{{fill:var(--text-dim);opacity:.5;}}
.ew-pt{{font-family:var(--font-mono);font-size:7.5px;font-weight:700;}}
.ew-tag{{font-family:var(--font-mono);font-size:7px;}}
.ew-stage{{font-size:12px;margin:6px 0 2px;padding:6px 10px;background:var(--panel-2);
  border-radius:6px;}}
.sc-head{{margin-bottom:8px;font-size:12px;}}
.sc-item{{border:1px solid var(--border);border-radius:7px;padding:6px 10px;margin:6px 0;background:var(--panel-2);}}
.sc-item summary{{cursor:pointer;font-size:12.5px;}} .sc-item p{{font-size:12px;margin:6px 0;}}
.sc-error{{border-left:3px solid #F23645;}} .sc-warn{{border-left:3px solid #1E88E5;}}
.sc-ok{{font-size:11px;}} .sc-tl{{display:flex;flex-wrap:wrap;gap:2px;}}
.sc-chip{{width:9px;height:14px;border-radius:2px;background:#089981;display:inline-block;}}
.sc-chip.sc-warn{{background:#1E88E5;}} .sc-chip.sc-error{{background:#F23645;}}
.plan-conflict{{margin:8px 0;padding:8px 10px;border-radius:6px;font-size:12px;
  background:#FFF4E5;border-left:3px solid #E67E22;color:#7C3A00;}}
.plan-conflict.explore{{background:#EEF4FF;border-left-color:#1E88E5;color:#0B3B78;}}
.plan-ok{{margin:8px 0;padding:6px 10px;border-radius:6px;font-size:12px;background:#E6F4F1;color:#086B5B;}}
.conf-note{{font-size:10.5px;margin-top:2px;}}
.ah-svg{{width:100%;height:auto;margin:6px 0 4px;}} .ah-t{{font:9px var(--font-mono);fill:#64748B;}}
.ah-legend{{font-size:10.5px;display:flex;flex-wrap:wrap;gap:10px;margin-top:6px;}}
.as-regime{{margin:4px 0 8px;font-size:12px;}}
.as-rtag{{font:700 11px var(--font-mono);color:#FFFFFF;background:#0F766E;border-radius:4px;padding:3px 7px;}}
.as-head{{display:flex;gap:14px;align-items:center;margin-bottom:8px;}}
.as-phase{{font:800 20px var(--font-mono);color:#FFFFFF;background:#7E57C2;border-radius:8px;
  padding:10px 12px;white-space:nowrap;}}
.as-name{{font-size:16px;}} .as-meta{{font-size:11px;color:var(--text-dim);margin-top:3px;}}
.as-season{{font-size:12px;margin:6px 0 10px;}}
.as-ladder{{display:grid;grid-template-columns:repeat(9,1fr);gap:4px;margin:8px 0 12px;}}
@media(max-width:700px){{.as-ladder{{grid-template-columns:repeat(3,1fr);}}}}
.as-step{{border:1px solid var(--border);border-radius:6px;padding:5px 6px;background:var(--panel-2);
  display:flex;flex-direction:column;gap:2px;min-width:0;}}
.as-step.cur{{border-color:#7E57C2;background:#F3EEFB;}} .as-step.nxt{{border-style:dashed;border-color:#7E57C2;}}
.as-n{{font:700 13px var(--font-mono);}} .as-t{{font-size:9.5px;line-height:1.2;color:var(--text-dim);}}
.as-step.cur .as-t{{color:#5E35B1;font-weight:700;}}
.as-bar{{height:3px;background:var(--border);border-radius:2px;overflow:hidden;}}
.as-bar i{{display:block;height:100%;background:#7E57C2;}}
.as-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:10px;}}
@media(max-width:700px){{.as-grid{{grid-template-columns:1fr 1fr;}}}}
.as-cell{{padding:8px 10px;border-radius:7px;background:var(--panel-2);display:flex;flex-direction:column;gap:2px;}}
.as-cell .dim{{font-size:10px;font-family:var(--font-mono);}}
.as-lbl{{font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:var(--text-dim);}}
.as-cols{{display:grid;grid-template-columns:1fr 1fr;gap:14px;}}
@media(max-width:700px){{.as-cols{{grid-template-columns:1fr;}}}}
.as-ul{{margin:4px 0 8px 18px;padding:0;font-size:12px;}} .as-miss li{{color:var(--amber);}}
.as-table-wrap{{overflow-x:auto;}}
.as-table{{width:100%;border-collapse:collapse;font-size:11.5px;font-family:var(--font-mono);}}
.as-table th,.as-table td{{padding:5px 6px;border-bottom:1px solid var(--border);text-align:left;white-space:nowrap;}}
.as-tl{{display:flex;flex-wrap:wrap;gap:3px;margin:4px 0 8px;}}
.as-chip{{font:700 10px var(--font-mono);width:18px;height:18px;border-radius:4px;display:inline-flex;
  align-items:center;justify-content:center;color:#FFFFFF;background:#94A3B8;}}
.as-chip.p0,.as-chip.p8{{background:#F23645;}} .as-chip.p1,.as-chip.p2{{background:#F57C00;}}
.as-chip.p3{{background:#1E88E5;}} .as-chip.p4,.as-chip.p5{{background:#089981;}}
.as-chip.p6,.as-chip.p7{{background:#7E57C2;}}
.as-note{{font-size:10.5px;margin-top:8px;}}
.ew-list{{display:flex;flex-direction:column;gap:3px;margin-top:6px;}}
.ew-row{{display:grid;grid-template-columns:70px 1fr 54px 40px 92px;gap:6px;
  align-items:center;font-size:10.5px;font-family:var(--font-mono);
  padding:4px 0;border-bottom:1px solid var(--border);}}
.ew-row span:last-child{{text-align:right;font-size:9px;}}
.ew-dead{{opacity:.42;text-decoration:line-through;}}
.ew-rank{{font-weight:700;color:var(--amber);}}
.ew-targets{{font-family:var(--font-mono);font-size:10px;color:var(--text-dim);
  margin-top:5px;padding:5px 8px;background:var(--panel-2);border-radius:5px;}}
@media(max-width:560px){{.ew-row{{grid-template-columns:62px 1fr 46px 36px;}}
  .ew-row span:last-child{{display:none;}}}}
.ms-grid{{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:6px 0;}}
@media(max-width:560px){{.proj-line{{stroke:var(--ew0);stroke-width:1.5;stroke-dasharray:5 4;opacity:.85;}}
.proj-dot{{fill:var(--panel);stroke:var(--ew0);stroke-width:1.3;}}
.proj-t{{font-family:var(--font-mono);font-size:7.5px;font-weight:700;fill:var(--ew0);}}
.proj-tag{{font-family:var(--font-mono);font-size:7px;fill:var(--ew0);opacity:.9;}}
.proj-now{{stroke:var(--text-dim);stroke-width:.8;stroke-dasharray:2 3;opacity:.6;}}
.lv-liqb{{stroke:var(--amber);stroke-width:1.1;}}
.lv-liqs{{stroke:var(--amber);stroke-width:1.1;opacity:.8;}}
.lv-liqb-t,.lv-liqs-t{{fill:var(--amber);font-size:6.5px;}}
.lv-ew{{stroke:var(--ew0);stroke-width:1;}}
.lv-ew-t{{fill:var(--ew0);}}
.ew-line-0{{stroke:var(--ew0);stroke-width:1.6;}}
.ew-line-1{{stroke:var(--ew1);stroke-width:1.3;stroke-dasharray:4 3;}}
.ew-line-2{{stroke:var(--ew2);stroke-width:1.1;stroke-dasharray:2 4;}}
.ew-dead-line{{stroke:var(--text-dim);stroke-width:.9;stroke-dasharray:1 5;opacity:.45;}}
.ew-line-0-dot{{fill:var(--ew0);}} .ew-line-1-dot{{fill:var(--ew1);}}
.ew-line-2-dot{{fill:var(--ew2);}} .ew-dead-line-dot{{fill:var(--text-dim);opacity:.45;}}
.ew-line-0-t{{fill:var(--ew0);}} .ew-line-1-t{{fill:var(--ew1);}}
.ew-line-2-t{{fill:var(--ew2);}} .ew-dead-line-t{{fill:var(--text-dim);opacity:.5;}}
.ew-pt{{font-family:var(--font-mono);font-size:7.5px;font-weight:700;}}
.ew-tag{{font-family:var(--font-mono);font-size:7px;}}
.ew-stage{{font-size:12px;margin:6px 0 2px;padding:6px 10px;background:var(--panel-2);
  border-radius:6px;}}
.ew-list{{display:flex;flex-direction:column;gap:3px;margin-top:6px;}}
.ew-row{{display:grid;grid-template-columns:70px 1fr 54px 40px 92px;gap:6px;
  align-items:center;font-size:10.5px;font-family:var(--font-mono);
  padding:4px 0;border-bottom:1px solid var(--border);}}
.ew-row span:last-child{{text-align:right;font-size:9px;}}
.ew-dead{{opacity:.42;text-decoration:line-through;}}
.ew-rank{{font-weight:700;color:var(--amber);}}
.ew-targets{{font-family:var(--font-mono);font-size:10px;color:var(--text-dim);
  margin-top:5px;padding:5px 8px;background:var(--panel-2);border-radius:5px;}}
@media(max-width:560px){{.ew-row{{grid-template-columns:62px 1fr 46px 36px;}}
  .ew-row span:last-child{{display:none;}}}}
.ms-grid{{grid-template-columns:1fr;}}}}
.ms-cell{{padding:9px 11px;border-radius:7px;background:var(--panel-2);
  display:flex;flex-direction:column;gap:2px;border-left:3px solid var(--border);}}
.ms-cell strong{{font-size:14px;letter-spacing:.01em;}}
.ms-cell .dim{{font-size:10px;font-family:var(--font-mono);}}
.ms-lbl{{font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:var(--text-dim);}}
.ms-wide{{margin:6px 0;}}
.mtf-list{{display:flex;flex-direction:column;gap:3px;margin-top:6px;}}
.mtf-row{{display:grid;grid-template-columns:44px 1fr 52px;gap:8px;align-items:center;
  font-size:11px;font-family:var(--font-mono);padding:4px 0;
  border-bottom:1px solid var(--border);}}
.mtf-row span:last-child{{text-align:right;}}
.liq-short{{fill:var(--bear);}}
.liq-long{{fill:var(--bull);}}
.liq-list{{display:flex;flex-direction:column;gap:4px;margin-top:6px;}}
.liq-row{{display:grid;grid-template-columns:58px 1fr 70px;gap:8px;
  align-items:center;font-size:11px;font-family:var(--font-mono);}}
.liq-bar{{height:5px;background:var(--panel-2);border-radius:3px;overflow:hidden;}}
.liq-bar i{{display:block;height:100%;background:var(--amber);}}
.tk-chart{{width:100%;height:auto;display:block;margin:4px 0 2px;}}
.tk-up{{stroke:var(--bull);stroke-width:1.4;}}
.tk-dn{{stroke:var(--bear);stroke-width:1.4;}}
.tk-na{{font-family:var(--font-mono);font-size:13px;color:var(--text-dim);
  padding:14px 0;text-align:center;}}
.tok-na summary{{opacity:.55;}}
.badge-na{{background:var(--panel-2);color:var(--text-dim);}}
.scan-head{{font-family:var(--font-mono);font-size:12px;margin:8px 0 10px;
  padding:7px 10px;background:var(--panel-2);border-radius:6px;line-height:1.6;}}
.scan-h{{font-size:10px;text-transform:uppercase;letter-spacing:.07em;
  color:var(--text-dim);margin:16px 0 4px;font-weight:600;}}
.scan-list{{display:flex;flex-direction:column;gap:3px;}}
.scan-row{{display:grid;grid-template-columns:96px 52px 34px 1fr 64px 84px;
  gap:6px;align-items:center;font-size:11px;font-family:var(--font-mono);
  padding:4px 0;border-bottom:1px solid var(--border);}}
.scan-sym{{font-weight:700;}}
.scan-score{{color:var(--text-dim);}}
.scan-pos{{font-size:9px;}}
@media(max-width:560px){{.scan-row{{grid-template-columns:90px 48px 32px 1fr;}}
  .scan-row .tag,.scan-pos{{display:none;}}}}
.rich-chart{{width:100%;height:auto;display:block;}}
.chart-head{{font-family:var(--font-mono);font-size:12px;margin-bottom:8px;}}
.cnd-up{{stroke:var(--bull);}} .cnd-dn{{stroke:var(--bear);}}
.cnd-up-f{{fill:var(--bull);}} .cnd-dn-f{{fill:var(--bear);}}
.ema9{{stroke:var(--ema9);stroke-width:1.1;}}
.ema20{{stroke:var(--ema20);stroke-width:1.1;}}
.ema50{{stroke:var(--ema50);stroke-width:1.1;}}
.ema200{{stroke:var(--ema200);stroke-width:1.1;}}
.lv-tp{{stroke:var(--bull);stroke-width:1;}}
.lv-sl{{stroke:var(--bear);stroke-width:1;}}
.lv-entry{{stroke:var(--info);stroke-width:1;}}
.lv-vwap,.lv-poc,.lv-va{{stroke:var(--amber);stroke-width:.8;opacity:.75;}}
.lv-t{{font-family:var(--font-mono);font-size:7.5px;}}
.lv-tp-t{{fill:var(--bull);}} .lv-sl-t{{fill:var(--bear);}}
.lv-entry-t{{fill:var(--info);}}
.lv-vwap-t,.lv-poc-t,.lv-va-t{{fill:var(--amber);}}
.pnl-t{{font-family:var(--font-mono);font-size:8px;fill:var(--text-dim);}}
.rsi-l{{stroke:var(--info);stroke-width:1;}}
.gridline{{stroke:var(--border);stroke-width:.6;}}
.tabs{{display:flex;flex-wrap:wrap;gap:6px;}}
.tab-radio{{position:absolute;opacity:0;pointer-events:none;}}
.tab-label{{display:inline-flex;align-items:center;gap:6px;padding:7px 12px;
  border-radius:7px;background:var(--panel-2);cursor:pointer;font-size:12px;
  font-family:var(--font-mono);border:1px solid transparent;}}
.tab-radio:checked + .tab-label{{border-color:var(--amber);color:var(--amber);}}
.tab-used{{font-size:9px;background:rgba(230,180,80,.2);color:var(--amber);
  padding:1px 5px;border-radius:4px;}}
.dot-ok{{width:7px;height:7px;border-radius:50%;background:var(--bull);display:inline-block;}}
.dot-bad{{width:7px;height:7px;border-radius:50%;background:var(--bear);display:inline-block;}}
.tab-panel{{display:none;width:100%;margin-top:12px;}}
.ex-meta{{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px;
  font-family:var(--font-mono);font-size:13px;}}
.ex-meta div{{background:var(--panel-2);border-radius:6px;padding:7px 9px;}}
.cap-list{{display:flex;flex-direction:column;gap:5px;}}
.cap-row{{display:grid;grid-template-columns:100px 1fr;gap:8px;align-items:center;font-size:12px;}}
.ex-fail{{color:var(--bear);font-family:var(--font-mono);font-size:13px;margin-bottom:6px;}}
.ex-err{{font-size:11px;color:var(--text-dim);font-family:var(--font-mono);
  background:var(--panel-2);padding:7px 9px;border-radius:6px;word-break:break-all;}}
.ex-note{{margin-top:10px;font-size:11px;color:var(--text-dim);line-height:1.5;}}
.ex-sig{{margin-top:12px;font-size:11px;color:var(--text-dim);}}
.ev-fusion{{font-family:var(--font-mono);font-size:12px;margin-bottom:10px;
  padding:7px 10px;background:var(--panel-2);border-radius:6px;}}
.ev-list{{display:flex;flex-direction:column;gap:4px;}}
.ev-row{{display:grid;grid-template-columns:74px 1fr 60px;align-items:center;gap:8px;
  font-size:12px;padding:4px 0;border-bottom:1px solid var(--border);}}
.ev-mark{{font-family:var(--font-mono);font-size:10px;text-transform:uppercase;
  letter-spacing:.05em;}}
.ev-ok .ev-mark{{color:var(--bull);}}
.ev-bad .ev-mark{{color:var(--bear);}}
.ev-neu .ev-mark{{color:var(--text-dim);}}
.ev-bar{{height:5px;background:var(--panel-2);border-radius:3px;overflow:hidden;}}
.ev-bar i{{display:block;height:100%;background:var(--text-dim);}}
.ev-ok .ev-bar i{{background:var(--bull);}}
.ev-bad .ev-bar i{{background:var(--bear);}}
.ev-neighbors{{margin-top:10px;padding:8px 10px;border-radius:6px;font-size:12px;
  background:var(--panel-2);}}
.nb-ok{{border-left:3px solid var(--bull);}}
.nb-bad{{border-left:3px solid var(--bear);}}
.nb-neu{{border-left:3px solid var(--text-dim);}}
.plan-summary{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:14px;
  font-family:var(--font-mono);font-size:14px;}}
.plan-summary div{{background:var(--panel-2);border-radius:7px;padding:8px 10px;}}
.plan-grid-cards{{display:grid;gap:8px;}}
@media(min-width:600px){{.plan-grid-cards{{grid-template-columns:1fr 1fr;}}}}
.plan-card{{background:var(--panel-2);border-radius:7px;padding:10px 12px;
  border-left:3px solid var(--text-dim);font-size:12px;}}
.plan-open{{border-left-color:var(--ema20);}}
.plan-tp1{{border-left-color:var(--amber);}}
.plan-tp2{{border-left-color:var(--bull);}}
.plan-sl{{border-left-color:var(--bear);}}
.plan-exp{{border-left-color:var(--text-dim);}}
.plan-head-row{{display:flex;justify-content:space-between;align-items:baseline;
  font-family:var(--font-mono);margin-bottom:3px;}}
.plan-r{{font-weight:700;}}
.r-pos{{color:var(--bull);}}
.r-neg{{color:var(--bear);}}
.plan-state{{font-family:var(--font-mono);font-size:11px;color:var(--text-dim);}}
.plan-lv{{font-family:var(--font-mono);font-size:11px;margin-top:4px;}}
.ind-row{{display:flex;justify-content:space-between;align-items:center;
  padding:5px 0;font-family:var(--font-mono);font-size:12px;border-bottom:1px solid var(--border);}}
.tag{{font-size:10px;padding:2px 7px;border-radius:4px;font-weight:700;letter-spacing:.04em;}}
.tag-bull{{background:rgba(52,211,153,.15);color:var(--bull);}}
.tag-bear{{background:rgba(251,122,108,.15);color:var(--bear);}}
.tag-warn{{background:rgba(230,180,80,.15);color:var(--amber);}}
.tag-info{{background:rgba(111,183,255,.15);color:var(--ema20);}}
.ind-pos{{margin-top:10px;font-size:12px;color:var(--text-dim);}}
.ema-line{{margin-top:8px;font-family:var(--font-mono);font-size:11px;line-height:1.6;}}
.cal-list{{display:flex;flex-direction:column;gap:5px;font-family:var(--font-mono);font-size:12px;}}
.cal-row{{display:grid;grid-template-columns:100px 1fr 60px;background:var(--panel-2);
  border-radius:6px;padding:6px 9px;align-items:center;}}
.cal-reliable{{border-left:2px solid var(--bull);}}
.cal-thin{{border-left:2px solid var(--text-dim);}}
.agent-balanced{{font-family:var(--font-mono);font-size:15px;font-weight:700;color:var(--text);}}

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

  <div class="card">
    <h2>Burse &middot; <span class="dim">capabilitati si stare</span></h2>
    {exchanges_html}
  </div>

  {briefing_html}

  <div class="grid">
    <div>
      <div class="card">
        <h2>Grafic &middot; {(best or {}).get("symbol", "-")}</h2>
        {chart_svg}
        <div class="legend">
          <span><i class="dot" style="background:#26A69A"></i>EMA 9</span>
          <span><i class="dot" style="background:#F57C00"></i>EMA 20</span>
          <span><i class="dot" style="background:#E53935"></i>EMA 50</span>
          <span><i class="dot" style="background:#1565C0"></i>EMA 200</span>
          <span><i class="dot" style="background:#7E57C2"></i>Elliott</span>
          <span><i class="dot" style="background:#089981"></i>TP</span>
          <span><i class="dot" style="background:#F23645"></i>SL / E INV</span>
          <span><i class="dot" style="background:#0288D1"></i>entry</span>
        </div>
      </div>

      <div class="card">
        <h2>Indicatori &middot; VWAP, Volume Profile, SuperTrend, MACD</h2>
        {indicators_html}
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
        <h2>Detalii per token &middot; <span class="dim">apasa pentru a deschide</span></h2>
        {tokens_html}
      </div>

      <div class="card">
        <h2>Top long <span class="dim">* Prob = formula din scor, nu masuratoare - vezi cardul de calibrare</span></h2>
        <table><tr><th>Symbol</th><th>Score</th><th title="formula, nu masuratoare">Prob*</th><th>Pers</th></tr>{long_rows}</table>
      </div>
      <div class="card">
        <h2>Top short <span class="dim">* idem</span></h2>
        <table><tr><th>Symbol</th><th>Score</th><th title="formula, nu masuratoare">Prob*</th><th>Pers</th></tr>{short_rows}</table>
      </div>
    </div>

    <div>
      <div class="card">
        <h2>AI plan &middot; best candidate</h2>
        {plan_html}
      </div>

      <div class="card">
        <h2>Auto-diagnostic &middot; auto-reparare agent</h2>
        {selfcheck_html}
      </div>

      <div class="card">
        <h2>Altcoin season &middot; faza ciclului (date reale de piata)</h2>
        {altseason_html}
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
        <h2>Evidente &middot; <span class="dim">{evidence_symbol}, din memoria agentului</span></h2>
        {evidence_html}
      </div>

      <div class="card">
        <h2>Autonomous plan memory</h2>
        {plans_html}
      </div>

      <div class="card">
        <h2>Calibrare &middot; probabilitate masurata</h2>
        {calibration_html}
      </div>

      <div class="card">
        <h2>Agent AI &middot; invatare online</h2>
        {agent_html}
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
    agent_state = load_json(AGENT_MODEL_FILE, {})
    plans_store = load_json(PLANS_FILE, {})
    briefing = load_json(BRIEFING_FILE, {})
    details = load_json(DETAILS_FILE, {})
    exchanges_store = load_json(EXCHANGES_FILE, {})
    exchange_scans = load_json(EXCHANGE_SCANS_FILE, {})

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
    TOKEN_META_UPDATED["when"] = (token_metadata or {}).get("_updated")

    os.makedirs(DOCS_DIR, exist_ok=True)
    html = build_html(scan, best, deep, chart, health, weights, session, token_meta, narrative, history, weights_history, agent_state, plans_store, briefing, details, exchanges_store,
                      exchange_scans)
    with open(OUTPUT_FILE, "w") as f:
        f.write(html)
    print(f"Dashboard generat: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
