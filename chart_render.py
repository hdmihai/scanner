#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chart_render.py
================
Randarea graficelor dashboard-ului, pe STRATURI si pe COMPONENTE.

DE CE UN MODUL SEPARAT
-----------------------
Graficul unic desena tot deodata: trei numaratori Elliott cu etichete proprii,
benzile heatmap-ului de lichidare, lichiditatea structurala, nivelurile planului
LOCKED si CURRENT, indicatorii, tintele si invalidarile Elliott. Rezultatul,
vazut pe telefon: etichete de unde suprapuse, benzi colorate peste tot pretul,
etichete de nivel taiate la marginea din dreapta.

Acum fiecare element e un STRAT, iar fiecare componenta alege ce straturi
desenaza. Graficul principal ramane curat - lumanari, EMA, numaratoarea Elliott
principala, proiectia si planul - iar restul are componente proprii, pliabile.
Suprapunerea nu mai poate aparea din intamplare: nicio componenta nu primeste
mai multe straturi decat poate afisa lizibil.

CONVENTII VIZUALE, CA IN CAPTURILE DE REFERINTA
------------------------------------------------
  - fundal alb, lumanari verde/rosu, EMA colorate
  - nivelurile ca "pastile" pe axa din dreapta: eticheta colorata + pretul
  - punctele Elliott cu stare: MAJOR START, W1 CONFIRMED, W3 FORMING
  - scenariul numaratorii ca pastila la pretul ultimului punct

Zero JavaScript: pliere cu <details>, maximizare cu ancora + :target.
"""

W = 760            # latimea viewBox-ului
PAD_R = 84         # coloana de preturi din dreapta
PILL_H = 11        # inaltimea unei pastile de nivel
CHAR_W = 5.1       # latimea medie a unui caracter monospace la 8.5px
MAX_LABEL = 44     # etichete mai lungi se scurteaza (cea mai lunga eticheta
                   # de lichiditate are 40 de caractere - trebuie sa incapa intreaga)

# Straturile disponibile. Fiecare componenta foloseste un subset.
ALL_LAYERS = {"ema", "plan", "locked", "ew_primary", "ew_all", "ew_levels",
              "ew_inv_primary", "projection", "indicators", "liq_heat",
              "liq_struct", "price_axis"}

MAIN_LAYERS = {"ema", "plan", "ew_primary", "ew_inv_primary", "projection",
               "price_axis"}


def fmt(v):
    """Pret cu precizie adaptata marimii: BTC cu 2 zecimale, PEPE cu 8."""
    if v is None:
        return "-"
    a = abs(v)
    if a >= 1000:
        return f"{v:,.2f}"
    if a >= 1:
        return f"{v:.4f}".rstrip("0").rstrip(".") if a < 100 else f"{v:.2f}"
    if a >= 0.01:
        return f"{v:.5f}"
    return f"{v:.8f}"


def _short(label):
    return label if len(label) <= MAX_LABEL else label[:MAX_LABEL - 1] + "…"


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ---------------------------------------------------------------------------
# colectarea nivelurilor, per strat
# ---------------------------------------------------------------------------
def _collect_levels(chart, layers):
    """Lista (pret, eticheta, tip, prioritate). Prioritatea decide ce ramane
    cand spatiul vertical nu ajunge pentru toate pastilele."""
    lv = []
    cur = chart.get("current") or {}
    lock = chart.get("locked") or {}
    ind = chart.get("indicators") or {}
    ew = chart.get("elliott") or {}
    pr = ew.get("primary") or {}

    if "plan" in layers:
        for k, lbl, kind, pri in (("tp2", "CURRENT TP2", "tp", 9),
                                  ("tp1", "CURRENT TP1", "tp", 9),
                                  ("entry", "CURRENT ENTRY", "entry", 10),
                                  ("sl", "CURRENT SL", "sl", 10)):
            if cur.get(k) is not None:
                lv.append((cur[k], lbl, kind, pri))
    if "locked" in layers and lock:
        pid = lock.get("id", "?")
        for k, lbl in (("tp2", "TP2"), ("tp1", "TP1"), ("entry", "ENTRY"), ("sl", "SL")):
            if lock.get(k) is not None:
                lv.append((lock[k], f"PLAN #{pid} {lbl} · LOCKED", "locked", 8))

    if "ew_levels" in layers and pr:
        for k, lbl in (("tp3", "ELLIOTT TP3"), ("tp2", "ELLIOTT TP2"), ("tp1", "ELLIOTT TP1")):
            v = (pr.get("targets") or {}).get(k)
            if v:
                lv.append((v, lbl, "ew", 7))
    if "ew_levels" in layers:
        for c in (ew.get("counts") or []):
            if c.get("invalidation") and not c.get("invalidated"):
                lv.append((c["invalidation"], f"{str(c.get('rank', '')).upper()} E INV",
                           "inv", 8))
    elif "ew_inv_primary" in layers and pr.get("invalidation"):
        lv.append((pr["invalidation"], "PRIMARY E INV", "inv", 8))

    if "indicators" in layers:
        for k, lbl, kind in (("vah", "VAH", "vah"), ("vwap", "VWAP", "vwap"),
                             ("poc", "POC", "poc"), ("val", "VAL", "val")):
            if ind.get(k):
                lv.append((ind[k], lbl, kind, 6))
        st = ind.get("supertrend") or {}
        if st.get("level"):
            bull = st.get("direction") == "BULLISH"
            lv.append((st["level"], f"SuperTrend {'Bullish' if bull else 'Bearish'}",
                       "stb" if bull else "sts", 6))

    if "liq_struct" in layers:
        for l in ((chart.get("liq_structure") or {}).get("levels") or [])[:6]:
            side = "BUY" if l["side"] == "BUY" else "SELL"
            lv.append((l["price"], f"{side}-SIDE LIQ · {l['kind']} · {l['role']}",
                       "liqb" if side == "BUY" else "liqs", 5))
    return lv


# ---------------------------------------------------------------------------
# desenarea unui grafic
# ---------------------------------------------------------------------------
def render_chart(chart, layers=None, height=300, fullscreen_id=None, show_head=True):
    """Un grafic SVG cu straturile cerute. Returneaza HTML."""
    layers = MAIN_LAYERS if layers is None else set(layers)
    candles = (chart or {}).get("candles") or []
    if len(candles) < 5:
        return '<p class="dim">Graficul apare dupa prima scanare cu semnale.</p>'

    H = height
    plot_w = W - PAD_R
    n = len(candles)
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]

    ew = chart.get("elliott") or {}
    off = ew.get("offset", 0)
    pr = ew.get("primary") or {}

    proj = (pr.get("projection") or {}) if "projection" in layers else {}
    ppath = [p for p in (proj.get("path") or []) if p.get("projected")]
    ext = max(0, max(p["idx"] for p in ppath) - off - (n - 1)) if ppath else 0

    levels = _collect_levels(chart, layers)
    heat = []
    if "liq_heat" in layers:
        heat = [c for c in ((chart.get("liquidation") or {}).get("clusters") or [])
                if c.get("price") and c.get("intensity")]

    # SCENARIILE ELLIOTT ca etichete in coltul stang sus, nu la nivelul de pret:
    # la nivelul de pret cadeau exact peste zona proiectiei.
    chips = []
    if ("ew_primary" in layers or "ew_all" in layers) and pr.get("headline"):
        chips.append((pr["headline"], "ewh"))
    if "ew_all" in layers:
        for c in (ew.get("counts") or [])[:3]:
            if c is not pr and c.get("headline") and not c.get("invalidated"):
                chips.append((c["headline"], "ewa"))
    chips_h = len(chips) * (PILL_H + 2) + (4 if chips else 0)

    # COLOANA PASTILELOR: latimea celei mai late etichete de nivel. Lumanarile
    # si proiectia se deseneaza doar in stanga ei, deci nicio pastila nu mai
    # poate acoperi pretul sau proiectia.
    lvl_w = [len(_short(l[1])) * CHAR_W + 8 for l in levels]
    pillcol = min(max(lvl_w) + 6, 250) if lvl_w else 0
    xmax = plot_w - pillcol

    # intervalul de pret: lumanari + niveluri + proiectie. Nivelurile foarte
    # departate (peste 60% de pret) nu intind axa - le-ar turti lumanarile.
    px = candles[-1][4]
    near = [l[0] for l in levels if abs(l[0] - px) / px < 0.6]
    lo = min(lows + near + [p["price"] for p in ppath])
    hi = max(highs + near + [p["price"] for p in ppath])
    rng = (hi - lo) or (abs(hi) or 1)
    lo -= rng * 0.05
    hi += rng * 0.05
    rng = hi - lo
    if chips_h:
        # banda de sus ramane goala: etichetele de scenariu nu acopera date
        hi += rng * chips_h / max(H - chips_h, 1)
        rng = hi - lo

    def y(v):
        return H - ((v - lo) / rng) * H

    def x(i):
        return (i / max(n - 1 + ext, 1)) * xmax

    b = []

    # axa de pret: 6 diviziuni rotunde, gri discret
    pill_ys = [y(l[0]) for l in levels]
    if "price_axis" in layers:
        for k in range(1, 6):
            v = lo + rng * k / 6
            yy = y(v)
            if any(abs(yy - py) < PILL_H + 2 for py in pill_ys):
                continue    # o pastila de pret ocupa deja locul
            b.append(f'<line class="grid" x1="0" y1="{yy:.0f}" x2="{plot_w}" y2="{yy:.0f}"/>')
            b.append(f'<text class="axis-t" x="{W - 4}" y="{yy + 3:.0f}" '
                     f'text-anchor="end">{fmt(v)}</text>')

    # heatmap-ul de lichidare: fundal, sub lumanari
    for c in heat:
        cy = y(c["price"])
        if 0 <= cy <= H:
            op = 0.07 + 0.28 * c["intensity"]
            cls = "heat-s" if c["side"] == "SHORT" else "heat-l"
            b.append(f'<rect class="{cls}" x="0" y="{cy - 2.5:.0f}" width="{xmax:.0f}" '
                     f'height="5" opacity="{op:.2f}"/>')

    # liniile nivelurilor, tot sub lumanari, ca sa nu acopere pretul
    for price, _lbl, kind, _p in levels:
        ly = y(price)
        if 0 <= ly <= H:
            b.append(f'<line class="lvl lvl-{kind}" x1="0" y1="{ly:.0f}" '
                     f'x2="{plot_w}" y2="{ly:.0f}"/>')

    # lumanari: doua trasee (sus/jos), nu doua elemente per lumanare
    cw = max(1.2, xmax / (n + ext) * 0.64)
    wick = {"u": [], "d": []}
    body = {"u": [], "d": []}
    for i, c in enumerate(candles):
        o, h, l, cl = c[1], c[2], c[3], c[4]
        k = "u" if cl >= o else "d"
        cx = x(i)
        wick[k].append(f"M{cx:.1f} {y(h):.1f}V{y(l):.1f}")
        top, bot = y(max(o, cl)), y(min(o, cl))
        body[k].append(f"M{cx - cw / 2:.1f} {top:.1f}h{cw:.1f}v{max(bot - top, 0.8):.1f}"
                       f"h{-cw:.1f}z")
    for k, cls in (("u", "cu"), ("d", "cd")):
        if wick[k]:
            b.append(f'<path class="{cls}" d="{"".join(wick[k])}" fill="none"/>')
            b.append(f'<path class="{cls}f" d="{"".join(body[k])}"/>')

    if "ema" in layers:
        for key in ("ema9", "ema20", "ema50", "ema200"):
            s = chart.get(key) or []
            pts = " ".join(f"{x(i):.0f},{y(v):.0f}" for i, v in enumerate(s) if v is not None)
            if pts:
                b.append(f'<polyline class="{key}" points="{pts}" fill="none"/>')

    placed = []     # cutiile etichetelor deja puse, pentru anti-suprapunere
    for k, (text, kind) in enumerate(chips):
        cy = 3 + k * (PILL_H + 2)
        w = len(text) * CHAR_W + 8
        placed.append((0, cy, w + 4, cy + PILL_H))
        b.append(f'<rect class="pill pill-{kind}" x="3" y="{cy}" width="{w:.0f}" '
                 f'height="{PILL_H}" rx="2"/>')
        b.append(f'<text class="pill-t" x="7" y="{cy + 8}">{_esc(text)}</text>')

    def place_label(cx, cy, text, above, cls):
        w = len(text) * CHAR_W * 0.95
        # incerc intai partea preferata, apoi cealalta; o eticheta care ar iesi
        # din grafic (sub un minim aflat jos, peste un varf aflat sus) nu se
        # taie - trece de partea opusa. Centrul se tine in interiorul zonei.
        cx = min(max(cx, w / 2 + 2), xmax - w / 2 - 2) if xmax > w + 4 else cx
        tries = [(above, k) for k in range(4)] + [(not above, k) for k in range(4)]
        for side_above, step in tries:
            dy = -(9 + step * 10) if side_above else (15 + step * 10)
            ty = cy + dy
            if ty - 8 < chips_h or ty + 2 > H:
                continue
            box = (cx - w / 2, ty - 8, cx + w / 2, ty + 2)
            if not any(box[0] < p[2] and box[2] > p[0] and box[1] < p[3] and box[3] > p[1]
                       for p in placed):
                placed.append(box)
                b.append(f'<text class="{cls}" x="{cx:.0f}" y="{ty:.0f}" '
                         f'text-anchor="middle">{_esc(text)}</text>')
                return

    def draw_count(count, ci, with_labels):
        pts = [p for p in (count.get("points") or [])
               if p.get("idx") is not None and 0 <= p["idx"] - off < n]
        if len(pts) < 2:
            return
        # scenariile anulate de pret: gri, punctat - vizibile ca istoric, dar
        # fara sa para active
        cls = "ewx" if count.get("invalidated") else f"ew{ci}"
        coords = [(x(p["idx"] - off), y(p["price"])) for p in pts]
        b.append(f'<polyline class="{cls}-l" points="'
                 + " ".join(f"{a:.0f},{c:.0f}" for a, c in coords) + '" fill="none"/>')
        for j, ((cx, cy), p) in enumerate(zip(coords, pts)):
            b.append(f'<circle class="{cls}-d" cx="{cx:.0f}" cy="{cy:.0f}" r="3.2"/>')
            if with_labels:
                # deasupra daca punctul e varf local, dedesubt daca e minim local -
                # dupa geometrie, nu dupa nume: A e varf intr-o corectie, dar minim
                # in alta, deci regula pe nume dadea etichete peste lumanari
                nb = [pts[k]["price"] for k in (j - 1, j + 1) if 0 <= k < len(pts)]
                above = all(p["price"] >= v for v in nb)
                place_label(cx, cy, p.get("display", p["label"]), above, f"{cls}-t")

    if "ew_all" in layers:
        for ci, c in enumerate((ew.get("counts") or [])[:3]):
            if c is pr:
                continue
            # scenariile alternative: doar linia, fara etichete per punct -
            # etichetele lor pe aceiasi pivoti erau sursa principala de suprapunere
            draw_count(c, 1 + (ci % 2), with_labels=False)
    if ("ew_primary" in layers or "ew_all" in layers) and pr:
        draw_count(pr, 0, with_labels=True)

    # proiectia: dupa linia "acum", punctat, etichete intre paranteze
    if ppath:
        full = proj.get("path") or []
        coords = [(x(p["idx"] - off), y(p["price"])) for p in full]
        nx = x(n - 1)
        b.append(f'<rect class="proj-zone" x="{nx:.0f}" y="0" width="{xmax - nx:.0f}" height="{H}"/>')
        b.append(f'<line class="proj-now" x1="{nx:.0f}" y1="0" x2="{nx:.0f}" y2="{H}"/>')
        b.append('<polyline class="proj-l" points="'
                 + " ".join(f"{a:.0f},{c:.0f}" for a, c in coords) + '" fill="none"/>')
        for (cx, cy), p in zip(coords[1:], full[1:]):
            b.append(f'<circle class="proj-d" cx="{cx:.0f}" cy="{cy:.0f}" r="3"/>')
            prev = coords[full.index(p) - 1][1]
            place_label(cx, cy, p["label"], cy <= prev, "proj-t")
        b.append(f'<text class="proj-tag" x="{nx + 4:.0f}" y="{H - 5}">PROIECTIE · scenariu '
                 f'{(proj.get("confidence") or 0) * 100:.0f}%</text>')

    # PASTILELE: scenariul Elliott + nivelurile, pe axa din dreapta, fara
    # suprapunere. Se sorteaza dupa pret si se impraștie pe verticala; daca nu
    # incap toate, raman cele cu prioritate mare.
    pills = [(lv[0], lv[1], lv[2], lv[3]) for lv in levels if lo <= lv[0] <= hi]
    max_pills = max(3, int(H / (PILL_H + 2)))
    if len(pills) > max_pills:
        pills = sorted(pills, key=lambda p: -p[3])[:max_pills]
    pills.sort(key=lambda p: -p[0])
    last_y = -99
    for price, label, kind, _p in pills:
        ty = max(y(price) - PILL_H / 2, last_y + PILL_H + 1.5)
        ty = min(ty, H - PILL_H)
        last_y = ty
        text = label if kind in ("ewh", "ewa") else _short(label)
        lw = len(text) * CHAR_W + 8
        lx = plot_w - 2 - lw
        b.append(f'<rect class="pill pill-{kind}" x="{lx:.0f}" y="{ty:.0f}" '
                 f'width="{lw:.0f}" height="{PILL_H}" rx="2"/>')
        b.append(f'<text class="pill-t" x="{lx + 4:.0f}" y="{ty + 8:.0f}">{_esc(text)}</text>')
        if kind not in ("ewh", "ewa"):
            b.append(f'<rect class="pill pill-{kind}" x="{plot_w + 2}" y="{ty:.0f}" '
                     f'width="{PAD_R - 4}" height="{PILL_H}" rx="2"/>')
            b.append(f'<text class="pill-t" x="{plot_w + 6}" y="{ty + 8:.0f}">{fmt(price)}</text>')

    svg = (f'<svg viewBox="0 0 {W} {H}" class="rc" preserveAspectRatio="xMidYMid meet">'
           f'{"".join(b)}</svg>')
    head = ""
    if show_head:
        tf = chart.get("timeframe") or ""
        head = (f'<div class="rc-head"><strong>{_esc(chart.get("symbol", ""))}</strong>'
                f'<span class="rc-dir rc-{str(chart.get("direction", "")).lower()}">'
                f'{_esc(chart.get("direction", ""))}</span>'
                f'<span class="dim">{_esc(tf)} · {n} lumanari</span>')
        if fullscreen_id:
            head += f'<a class="fs-btn" href="#{fullscreen_id}">Maximizeaza</a>'
        head += "</div>"
    out = head + f'<div class="rc-scroll">{svg}</div>'
    if fullscreen_id:
        out += (f'<div id="{fullscreen_id}" class="fs-overlay">'
                f'<a class="fs-close" href="#">Inchide</a>'
                f'<div class="fs-inner">{svg}</div></div>')
    return out


def render_subpanels(chart, height=150):
    """Volum, RSI si MACD - componenta proprie, sub graficul principal."""
    candles = (chart or {}).get("candles") or []
    if len(candles) < 5:
        return '<p class="dim">N/A</p>'
    n = len(candles)
    plot_w = W - PAD_R
    vh, rh, mh, gap = 50, 56, 30, 7

    def x(i):
        return (i / max(n - 1, 1)) * plot_w

    b = []
    vols = [c[5] or 0 for c in candles]
    vmax = max(vols) or 1
    cw = max(1.0, plot_w / n * 0.64)
    for k, cls in ((True, "cuf"), (False, "cdf")):
        d = "".join(f"M{x(i) - cw / 2:.1f} {vh - v / vmax * vh:.1f}h{cw:.1f}V{vh}h{-cw:.1f}z"
                    for i, (c, v) in enumerate(zip(candles, vols)) if (c[4] >= c[1]) == k)
        if d:
            b.append(f'<path class="{cls}" d="{d}" opacity="0.55"/>')
    b.append(f'<text class="sub-t" x="2" y="9">VOLUM</text>')

    y0 = vh + gap
    rs = chart.get("rsi") or []
    if any(v is not None for v in rs):
        for lvl in (30, 70):
            ly = y0 + rh - lvl / 100 * rh
            b.append(f'<line class="grid" x1="0" y1="{ly:.0f}" x2="{plot_w}" y2="{ly:.0f}"/>')
        pts = " ".join(f"{x(i):.0f},{y0 + rh - min(max(v, 0), 100) / 100 * rh:.0f}"
                       for i, v in enumerate(rs) if v is not None)
        last = next((v for v in reversed(rs) if v is not None), None)
        b.append(f'<polyline class="rsi" points="{pts}" fill="none"/>')
        b.append(f'<text class="sub-t" x="2" y="{y0 + 9}">RSI 14 · {last:.1f}</text>')
    else:
        b.append(f'<text class="sub-t" x="2" y="{y0 + 9}">RSI 14 · N/A</text>')

    y1 = y0 + rh + gap
    hist = (chart.get("macd") or {}).get("histogram")
    if hist is not None:
        mid = y1 + mh / 2
        bh = mh / 2 - 3
        cls = "cuf" if hist > 0 else "cdf"
        b.append(f'<line class="grid" x1="0" y1="{mid:.0f}" x2="{plot_w}" y2="{mid:.0f}"/>')
        b.append(f'<rect class="{cls}" x="{plot_w - 40}" y="{mid - bh if hist > 0 else mid:.0f}" '
                 f'width="34" height="{bh:.0f}"/>')
        b.append(f'<text class="sub-t" x="2" y="{y1 + 9}">MACD hist {hist:+.6g}</text>')
    H = y1 + mh + 2
    return (f'<div class="rc-scroll"><svg viewBox="0 0 {W} {H:.0f}" class="rc" '
            f'preserveAspectRatio="xMidYMid meet">{"".join(b)}</svg></div>')


# ---------------------------------------------------------------------------
# componentele: fiecare zona de interes, pliabila
# ---------------------------------------------------------------------------
def _component(title, meta, body, open_=False):
    return (f'<details class="cc"{" open" if open_ else ""}><summary>'
            f'<span class="cc-title">{_esc(title)}</span>'
            f'<span class="cc-meta">{_esc(meta)}</span></summary>'
            f'<div class="cc-body">{body}</div></details>')


def render_components(chart, uid="main"):
    """Graficul principal plus o componenta pliabila pentru fiecare zona de
    interes. Principalul si Elliott sunt deschise implicit; restul compacte."""
    if len((chart or {}).get("candles") or []) < 5:
        return '<p class="dim">Graficul apare dupa prima scanare cu semnale.</p>'
    ew = chart.get("elliott") or {}
    pr = ew.get("primary") or {}
    lock = chart.get("locked") or {}
    ind = chart.get("indicators") or {}
    liq = chart.get("liquidation") or {}
    ls = chart.get("liq_structure") or {}
    st = ind.get("supertrend") or {}
    rs = [v for v in (chart.get("rsi") or []) if v is not None]

    parts = [
        _component("Grafic principal",
                   pr.get("headline") or "nicio structura Elliott curenta",
                   render_chart(chart, MAIN_LAYERS, 330, fullscreen_id=f"max-{uid}"),
                   open_=True),
        _component("Elliott Wave · toate scenariile",
                   f"{ew.get('alive', 0)} din {ew.get('total', 0)} scenarii valide",
                   render_chart(chart, {"ew_primary", "ew_all", "ew_levels",
                                        "projection", "price_axis"}, 300, show_head=False),
                   open_=True),
        _component("Plan · LOCKED vs CURRENT",
                   f"plan #{lock['id']} blocat" if lock else "doar niveluri CURRENT",
                   render_chart(chart, {"plan", "locked", "price_axis"}, 260,
                                show_head=False)),
        _component("Lichiditate · heatmap si structura",
                   (f"{len(ls.get('levels') or [])} niveluri structurale"
                    + (" · sweep bilateral" if ls.get("two_sided_sweep") else "")),
                   render_chart(chart, {"liq_heat", "liq_struct", "price_axis"}, 280,
                                show_head=False)),
        _component("Indicatori · VWAP, Volume Profile, SuperTrend",
                   f"SuperTrend {st.get('direction', 'N/A')}",
                   render_chart(chart, {"ema", "indicators", "price_axis"}, 280,
                                show_head=False)),
        _component("Volum · RSI · MACD",
                   f"RSI {rs[-1]:.1f}" if rs else "RSI N/A",
                   render_subpanels(chart)),
    ]
    return '<div class="cc-stack">' + "".join(parts) + "</div>"


# CSS-ul componentelor si al graficelor, pe fundal alb. Culorile urmeaza
# conventiile din capturile de referinta (TradingView): verde #089981 pentru
# cresteri, rosu #F23645 pentru scaderi, pastile colorate cu text alb.
CSS = """
.cc-stack{display:flex;flex-direction:column;gap:10px;}
.cc{border:1px solid #E3E8EF;border-radius:10px;background:#FFFFFF;overflow:hidden;}
.cc>summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:10px;
  padding:10px 14px;background:#F8FAFC;border-bottom:1px solid transparent;}
.cc[open]>summary{border-bottom-color:#E3E8EF;}
.cc>summary::-webkit-details-marker{display:none;}
.cc>summary::before{content:"▸";color:#64748B;font-size:11px;transition:transform .15s;}
.cc[open]>summary::before{transform:rotate(90deg);}
.cc-title{font-weight:700;font-size:13px;color:#0F172A;white-space:nowrap;}
.cc-meta{font-size:11px;color:#64748B;font-family:var(--font-mono);margin-left:auto;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;}
.cc-body{padding:10px 12px 12px;}
.rc-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:12px;
  font-family:var(--font-mono);margin-bottom:6px;color:#0F172A;}
.rc-dir{padding:1px 7px;border-radius:4px;font-weight:700;font-size:11px;}
.rc-long{background:#E6F4F1;color:#089981;} .rc-short{background:#FDECEE;color:#F23645;}
.rc-scroll{overflow-x:auto;display:flex;flex-direction:row-reverse;}
.rc{display:block;width:100%;min-width:640px;height:auto;background:#FFFFFF;}
.grid{stroke:#EEF1F5;stroke-width:1;}
.axis-t{font:8px var(--font-mono);fill:#94A3B8;}
.cu{stroke:#089981;stroke-width:1;} .cd{stroke:#F23645;stroke-width:1;}
.cuf{fill:#089981;} .cdf{fill:#F23645;}
.ema9{stroke:#26A69A;stroke-width:1.2;} .ema20{stroke:#F57C00;stroke-width:1.2;}
.ema50{stroke:#E53935;stroke-width:1.2;} .ema200{stroke:#1565C0;stroke-width:2.2;}
.lvl{stroke-width:.9;stroke-dasharray:4 3;opacity:.75;}
.lvl-tp{stroke:#089981;} .lvl-sl,.lvl-inv,.lvl-sts{stroke:#F23645;}
.lvl-entry,.lvl-vwap,.lvl-val{stroke:#0288D1;} .lvl-locked{stroke:#455A64;}
.lvl-ew{stroke:#7E57C2;} .lvl-vah,.lvl-poc,.lvl-liqb,.lvl-liqs{stroke:#E67E22;}
.lvl-stb{stroke:#089981;}
.heat-s{fill:#F23645;} .heat-l{fill:#089981;}
.pill-t{font:600 8.5px var(--font-mono);fill:#FFFFFF;}
.pill-tp,.pill-stb{fill:#089981;} .pill-sl,.pill-inv,.pill-sts{fill:#F23645;}
.pill-entry,.pill-vwap,.pill-val{fill:#0288D1;} .pill-locked{fill:#455A64;}
.pill-ew,.pill-ewh{fill:#7E57C2;} .pill-ewa{fill:#1E88E5;}
.pill-vah,.pill-poc,.pill-liqb{fill:#E67E22;} .pill-liqs{fill:#C0651A;}
.ew0-l{stroke:#7E57C2;stroke-width:2;} .ew0-d{fill:#7E57C2;}
.ew0-t{font:700 8.5px var(--font-mono);fill:#5E35B1;}
.ew1-l{stroke:#1E88E5;stroke-width:1.4;stroke-dasharray:5 3;} .ew1-d{fill:#1E88E5;}
.ew2-l{stroke:#FB8C00;stroke-width:1.2;stroke-dasharray:2 3;} .ew2-d{fill:#FB8C00;}
.ewx-l{stroke:#94A3B8;stroke-width:1;stroke-dasharray:1 4;} .ewx-d{fill:#CBD5E1;}
.ewx-t{font:600 8px var(--font-mono);fill:#94A3B8;}
.proj-zone{fill:#7E57C2;opacity:.04;}
.proj-now{stroke:#94A3B8;stroke-width:1;stroke-dasharray:3 3;}
.proj-l{stroke:#7E57C2;stroke-width:1.6;stroke-dasharray:6 4;}
.proj-d{fill:#FFFFFF;stroke:#7E57C2;stroke-width:1.5;}
.proj-t{font:700 8.5px var(--font-mono);fill:#7E57C2;}
.proj-tag{font:600 8px var(--font-mono);fill:#7E57C2;}
.rsi{stroke:#7E57C2;stroke-width:1.2;}
.sub-t{font:600 8px var(--font-mono);fill:#64748B;}
"""
