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
PAD_R = 98         # coloana de preturi din dreapta
# LIZIBILITATE PE TELEFON: graficul are cel putin 720 px CSS latime (derulare orizontala pe ecrane
# mai inguste), deci 1 unitate de viewBox ~ 0.95 px; textul de 12 unitati iese ~11.5 px - marimea
# minima a etichetelor din graficele de prezentare profesionale. Inainte: 8.5 unitati pe 640 px,
# adica ~7 px, ilizibil.
FONT = 12          # textul etichetelor si al pastilelor, in unitati de viewBox
PILL_H = 18        # inaltimea unei pastile de nivel
CHAR_W = 7.25      # latimea unui caracter monospace la 12 unitati (0.6 em)
TEXT_DY = 13       # linia de baza a textului in pastila
MAX_LABEL = 44     # etichete mai lungi se scurteaza (cea mai lunga eticheta
                   # de lichiditate are 40 de caractere - trebuie sa incapa intreaga)

# Straturile disponibile. Fiecare componenta foloseste un subset.
ALL_LAYERS = {"ema", "plan", "locked", "ew_primary", "ew_all", "ew_levels",
              "ew_inv_primary", "projection", "indicators", "liq_heat",
              "liq_struct", "price_axis", "zones", "marks"}

# ZONELE de suport (verde) si rezistenta (portocaliu) sunt pe graficul principal si pe graficul
# fiecarui token: dreptunghiuri de la formarea zonei pana in prezent, cu marginile ca linii
# orizontale pe toata latimea - cea mai apropiata zona de fiecare parte incadreaza pretul.
MAIN_LAYERS = {"ema", "plan", "ew_primary", "ew_inv_primary", "projection",
               "price_axis", "zones", "marks"}
# marcaje care nu intind axa (ex. invalidarea analistului pe graficul unui token): se deseneaza
# doar daca pretul lor intra in intervalul dat de lumanari, niveluri si zone
SOFT_KINDS = {"invsoft"}
ZONE_KEEP_PCT = 8.0   # a doua zona de pe o parte intinde axa doar daca e la cel mult 8% de pret


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
        hit = pr.get("targets_hit") or {}
        for k, lbl in (("tp3", "ELLIOTT TP3"), ("tp2", "ELLIOTT TP2"), ("tp1", "ELLIOTT TP1")):
            v = (pr.get("targets") or {}).get(k)
            if v:
                # o tinta deja depasita nu mai e tinta: marcata ATINS, in gri
                if hit.get(k):
                    lv.append((v, f"{lbl} · ATINS", "hit", 3))
                else:
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

    # NIVELUL URMARIT PENTRU INTRARE, cand Elliott contrazice planul: capatul
    # corectiei proiectate - ca "NEXT ENTRY WATCH" din capturile de referinta.
    fcn = (chart.get("forecast") or {}) if "projection" in layers else {}
    if fcn.get("next_entry") and fcn.get("plan_dir"):
        lv.append((fcn["next_entry"], f"NEXT ENTRY WATCH · {fcn['plan_dir']}", "nextentry", 10))

    if "liq_struct" in layers:
        for l in ((chart.get("liq_structure") or {}).get("levels") or [])[:6]:
            side = "BUY" if l["side"] == "BUY" else "SELL"
            lv.append((l["price"], f"{side}-SIDE LIQ · {l['kind']} · {l['role']}",
                       "liqb" if side == "BUY" else "liqs", 5))

    marks = [m for m in (chart.get("marks") or []) if m.get("price")] if "marks" in layers else []
    if "zones" in layers:
        # o singura pastila pe pret: doua zone alaturate au o margine comuna, iar un marcaj
        # (invalidarea) pe marginea unei zone o inlocuieste. La margine comuna ramane rolul
        # potrivit pozitiei fata de pretul curent - deasupra rezistenta, dedesubt suportul.
        px = chart.get("price") or ((chart.get("candles") or [[0] * 5])[-1])[4]
        taken = {fmt(m["price"]) for m in marks}
        best = {}
        for z in _zone_frame(chart):
            sup = z["side"] == "S"
            tf = str(z.get("tf") or "").upper()
            name = "SUPORT" if sup else "REZISTENTA"
            for price, label, kind, pri in (
                    (z["hi"] if sup else z["lo"], f"{name} {tf} · forta {z.get('strength', 0)}",
                     "zsup" if sup else "zres", 9),
                    (z["lo"] if sup else z["hi"], f"{name} {tf} · {'baza' if sup else 'varf'}",
                     "zsup2" if sup else "zres2", 4)):
                key = fmt(price)
                if key in taken:
                    continue
                rank = ((price <= px) == sup, pri)
                if key not in best or rank > best[key][0]:
                    best[key] = (rank, (price, label, kind, pri))
        lv += [v for _r, v in best.values()]
    for m in marks:
        lv.append((m["price"], m.get("label", ""), m.get("kind", "inv"), 10))
    return lv


def _zone_frame(chart):
    """Zonele desenate: cadrul calculat de nucleu (core/zones.bundle), doar cele valide."""
    return [z for z in (((chart or {}).get("zones") or {}).get("frame") or [])
            if z.get("lo") and z.get("hi") and z.get("side") in ("S", "R")]


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

    # PROGNOZA (linie continua prin pretul real + punctata din prezent) are
    # prioritate; proiectia veche ramane doar ca rezerva pentru date fara ea.
    fc = (chart.get("forecast") or {}) if "projection" in layers else {}
    solid = fc.get("solid") or []
    if fc.get("path"):
        proj = {"path": fc["path"], "confidence": fc.get("confidence")}
    else:
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
    lvl_w = [len(_short(l[1])) * CHAR_W + 10 for l in levels]
    pillcol = min(max(lvl_w) + 6, 330) if lvl_w else 0
    xmax = plot_w - pillcol

    # intervalul de pret: lumanari + niveluri + proiectie. Nivelurile foarte
    # departate (peste 60% de pret) nu intind axa - le-ar turti lumanarile.
    px = chart.get("price") or candles[-1][4]
    near = [l[0] for l in levels if abs(l[0] - px) / px < 0.6 and not str(l[2]).startswith("z")
            and l[2] not in SOFT_KINDS]
    # ZONELE: cea mai apropiata de fiecare parte intra intreaga in axa (incadreaza pretul); din
    # urmatoarele intra doar marginea dinspre pret, si doar daca e aproape - zona apare ca banda
    # care continua dincolo de marginea graficului, fara sa turteasca lumanarile
    zframe = _zone_frame(chart) if "zones" in layers else []
    for side in ("S", "R"):
        zs = [z for z in zframe if z["side"] == side]
        for k, z in enumerate(zs):
            edge = z["hi"] if side == "S" else z["lo"]
            if k == 0:
                near += [z["lo"], z["hi"]]
            elif abs(edge - px) / px * 100 <= ZONE_KEEP_PCT:
                near.append(edge)
    lo = min(lows + near + [p["price"] for p in ppath] + [p["price"] for p in solid])
    hi = max(highs + near + [p["price"] for p in ppath] + [p["price"] for p in solid])
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

    # PASTILELE (pozitiile se calculeaza inainte de axa, ca axa sa nu scrie sub ele): scenariul
    # Elliott + nivelurile, pe axa din dreapta, fara
    # suprapunere. Se sorteaza dupa pret si se impraștie pe verticala; daca nu
    # incap toate, raman cele cu prioritate mare.
    pills = [(lv[0], lv[1], lv[2], lv[3]) for lv in levels if lo <= lv[0] <= hi]
    max_pills = max(3, int(H / (PILL_H + 2)))
    if len(pills) > max_pills:
        pills = sorted(pills, key=lambda p: -p[3])[:max_pills]
    pills.sort(key=lambda p: -p[0])
    # POZITIILE: fiecare pastila cat mai aproape de pretul ei, fara suprapuneri si in interiorul
    # graficului - intai de sus in jos (distanta minima), apoi de jos in sus (marginea de jos).
    # Intr-o singura trecere, pastilele impinse sub marginea de jos se asezau una peste alta.
    gap = PILL_H + 2
    pys = []
    for price, *_rest in pills:
        want = y(price) - PILL_H / 2
        pys.append(max(want, pys[-1] + gap) if pys else max(want, 0.0))
    for k in range(len(pys) - 1, -1, -1):
        pys[k] = min(pys[k], pys[k + 1] - gap if k + 1 < len(pys) else H - PILL_H)
    b = []

    # axa de pret: 6 diviziuni rotunde, gri discret; o eticheta de axa nu se scrie unde e deja o
    # pastila de pret (pozitia ei FINALA, dupa impraștiere - nu pretul ei)
    if "price_axis" in layers:
        for k in range(1, 6):
            v = lo + rng * k / 6
            yy = y(v)
            if any(ty - 4 < yy + 4 and yy - 9 < ty + PILL_H + 4 for ty in pys):
                continue    # o pastila de pret ocupa deja locul
            b.append(f'<line class="grid" x1="0" y1="{yy:.0f}" x2="{plot_w}" y2="{yy:.0f}"/>')
            b.append(f'<text class="axis-t" x="{W - 4}" y="{yy + 4:.0f}" '
                     f'text-anchor="end">{fmt(v)}</text>')

    # ZONELE: dreptunghi de la lumanarea de formare (sau marginea stanga) pana in prezent, plus
    # marginile ca linii orizontale pe toata latimea graficului - ca in capturile de referinta
    if zframe:
        ts = [c[0] for c in candles]
        for z in zframe:
            y1, y2 = y(z["hi"]), y(z["lo"])
            if y2 < 0 or y1 > H:
                continue
            y1, y2 = max(0.0, y1), min(float(H), y2)
            i0 = next((i for i, t in enumerate(ts) if t >= (z.get("t0") or 0)), 0)
            x0 = x(i0) if z.get("t0") and z["t0"] > ts[0] else 0.0
            cls = "zs" if z["side"] == "S" else "zr"
            tfc = "z1d" if z.get("tf") == "1d" else "zsc"
            b.append(f'<rect class="zone {cls} {tfc}" x="{x0:.0f}" y="{y1:.1f}" width="{max(xmax - x0, 2):.0f}" '
                     f'height="{max(y2 - y1, 1.5):.1f}"/>')
            for yy in (y1, y2):
                b.append(f'<line class="zl {cls} {tfc}" x1="0" y1="{yy:.1f}" x2="{plot_w}" y2="{yy:.1f}"/>')

    # heatmap-ul de lichidare: fundal, sub lumanari
    for c in heat:
        cy = y(c["price"])
        if 0 <= cy <= H:
            op = 0.07 + 0.28 * c["intensity"]
            cls = "heat-s" if c["side"] == "SHORT" else "heat-l"
            b.append(f'<rect class="{cls}" x="0" y="{cy - 2.5:.0f}" width="{xmax:.0f}" '
                     f'height="5" opacity="{op:.2f}"/>')

    # liniile nivelurilor, tot sub lumanari, ca sa nu acopere pretul (zonele au liniile lor)
    for price, _lbl, kind, _p in levels:
        ly = y(price)
        if 0 <= ly <= H and not str(kind).startswith("z"):
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
        w = len(text) * CHAR_W + 12
        placed.append((0, cy, w + 4, cy + PILL_H))
        b.append(f'<rect class="pill pill-{kind}" x="3" y="{cy}" width="{w:.0f}" '
                 f'height="{PILL_H}" rx="3"/>')
        b.append(f'<text class="pill-t" x="8" y="{cy + TEXT_DY}">{_esc(text)}</text>')

    def place_label(cx, cy, text, above, cls):
        w = len(text) * CHAR_W
        # incerc intai partea preferata, apoi cealalta; o eticheta care ar iesi
        # din grafic (sub un minim aflat jos, peste un varf aflat sus) nu se
        # taie - trece de partea opusa. Centrul se tine in interiorul zonei.
        cx = min(max(cx, w / 2 + 2), xmax - w / 2 - 2) if xmax > w + 4 else cx
        tries = [(above, k) for k in range(4)] + [(not above, k) for k in range(4)]
        for side_above, step in tries:
            dy = -(10 + step * 15) if side_above else (20 + step * 15)
            ty = cy + dy
            if ty - 11 < chips_h or ty + 3 > H:
                continue
            box = (cx - w / 2, ty - 11, cx + w / 2, ty + 3)
            if not any(box[0] < p[2] and box[2] > p[0] and box[1] < p[3] and box[3] > p[1]
                       for p in placed):
                placed.append(box)
                # CONTUR ALB ca text separat, desenat DEDESUBT: lizibil peste
                # lumanari si linii. Varianta cu `paint-order` depindea de suportul
                # randatorului - unde lipsea, conturul acoperea textul si eticheta
                # devenea invizibila (verificat la randare).
                b.append(f'<text class="halo {cls}-h" x="{cx:.0f}" y="{ty:.0f}" '
                         f'text-anchor="middle">{_esc(text)}</text>')
                b.append(f'<text class="{cls}" x="{cx:.0f}" y="{ty:.0f}" '
                         f'text-anchor="middle">{_esc(text)}</text>')
                return

    # ETICHETA PROGNOZEI: doua randuri scurte, aliniate la dreapta zonei de prognoza (nu intra sub
    # coloana pastilelor), sus, sub scenariile Elliott; locul lor se rezerva INAINTE de etichetele
    # punctelor, ca acestea sa le ocoleasca. Daca sus nu incap (peste o eticheta de scenariu), jos.
    tags = []
    if ppath:
        hist = fc.get("hist") or {}
        src = {"elliott": "scenariu Elliott", "plan": "drumul planului",
               "elliott_conflict": "CONFLICT: corectie Elliott",
               "plan_split": "drumul planului · Elliott impartit"}.get(fc.get("source"), "scenariu")
        htxt = (f" · istoric {hist['win_rate']}% din {hist['n']}" if hist.get("n") else "")
        t2 = (f'{htxt.lstrip(" ·")}{" · " if htxt else ""}structura '
              f'{(proj.get("confidence") or 0) * 100:.0f}%')
        lines = [f"PROGNOZA · {src}", t2]
        tw = max(len(t) for t in lines) * CHAR_W
        x_r = xmax - 4
        top = [chips_h + 15, chips_h + 30]
        clash = any(x_r - tw < p[2] and p[1] < top[1] + 3 and p[3] > top[0] - 11 for p in placed)
        ys = [H - 21, H - 6] if clash else top
        for y_, t in zip(ys, lines):
            tags.append((x_r, y_, t))
            placed.append((x_r - len(t) * CHAR_W, y_ - 11, x_r, y_ + 3))

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

    # LINIA CONTINUA: de la ultimul punct al structurii, prin pivotii formati de
    # atunci, pana la lumanarea curenta - realitatea, nu prognoza.
    if len(solid) >= 2 and ("ew_primary" in layers or "ew_all" in layers):
        sc = [(x(p["idx"] - off), y(p["price"])) for p in solid if 0 <= p["idx"] - off < n]
        if len(sc) >= 2:
            b.append('<polyline class="live-l" points="'
                     + " ".join(f"{a:.0f},{c:.0f}" for a, c in sc) + '" fill="none"/>')
            b.append(f'<circle class="live-d" cx="{sc[-1][0]:.0f}" cy="{sc[-1][1]:.0f}" r="3.4"/>')

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
        for x_, y_, txt_ in tags:
            # contur alb dedesubt: textul ramane lizibil peste lumanari si linii
            b.append(f'<text class="halo proj-tag-h" x="{x_:.0f}" y="{y_:.0f}" text-anchor="end">{_esc(txt_)}</text>')
            b.append(f'<text class="proj-tag" x="{x_:.0f}" y="{y_:.0f}" text-anchor="end">{_esc(txt_)}</text>')

    for (price, label, kind, _p), ty in zip(pills, pys):
        text = label if kind in ("ewh", "ewa") else _short(label)
        lw = len(text) * CHAR_W + 10
        lx = plot_w - 3 - lw
        b.append(f'<rect class="pill pill-{kind}" x="{lx:.0f}" y="{ty:.0f}" '
                 f'width="{lw:.0f}" height="{PILL_H}" rx="3"/>')
        b.append(f'<text class="pill-t" x="{lx + 5:.0f}" y="{ty + TEXT_DY:.0f}">{_esc(text)}</text>')
        if kind not in ("ewh", "ewa"):
            b.append(f'<rect class="pill pill-{kind} pill-px" x="{plot_w + 2}" y="{ty:.0f}" '
                     f'width="{PAD_R - 4}" height="{PILL_H}" rx="3"/>')
            b.append(f'<text class="pill-t pill-pxt" x="{plot_w + 7}" y="{ty + TEXT_DY:.0f}">{fmt(price)}</text>')

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
    vh, rh, mh, gap = 56, 64, 36, 10

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
    b.append(f'<text class="sub-t" x="2" y="13">VOLUM</text>')

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
        b.append(f'<text class="sub-t" x="2" y="{y0 + 13}">RSI 14 · {last:.1f}</text>')
    else:
        b.append(f'<text class="sub-t" x="2" y="{y0 + 13}">RSI 14 · N/A</text>')

    y1 = y0 + rh + gap
    hist = (chart.get("macd") or {}).get("histogram")
    if hist is not None:
        mid = y1 + mh / 2
        bh = mh / 2 - 3
        cls = "cuf" if hist > 0 else "cdf"
        b.append(f'<line class="grid" x1="0" y1="{mid:.0f}" x2="{plot_w}" y2="{mid:.0f}"/>')
        b.append(f'<rect class="{cls}" x="{plot_w - 40}" y="{mid - bh if hist > 0 else mid:.0f}" '
                 f'width="34" height="{bh:.0f}"/>')
        b.append(f'<text class="sub-t" x="2" y="{y1 + 13}">MACD hist {hist:+.6g}</text>')
    H = y1 + mh + 2
    return (f'<div class="rc-scroll"><svg viewBox="0 0 {W} {H:.0f}" class="rc" '
            f'preserveAspectRatio="xMidYMid meet">{"".join(b)}</svg></div>')


# ---------------------------------------------------------------------------
# componentele: fiecare zona de interes, pliabila
# ---------------------------------------------------------------------------
def zone_stats_text(zs):
    """Rata masurata de respectare a zonelor la prima retestare, fata de benzi-placebo, pe fiecare
    timeframe - textul afisat langa zone, ca ele sa nu para mai mult decat sunt."""
    out = []
    for key, name in (("scan", (zs or {}).get("tf") or "scanare"), ("d1", "1D")):
        s = (zs or {}).get(key) or {}
        z, p = s.get("zones") or {}, s.get("placebo") or {}
        if not z.get("n"):
            continue
        verdict = {"peste_placebo": "PESTE benzile aleatoare - zonele aduc informatie",
                   "sub_placebo": "SUB benzile aleatoare", "in_marja": "in marja benzilor aleatoare - fara avantaj masurat"
                   }.get(s.get("verdict"), "esantion prea mic pentru un verdict")
        out.append(f"{name}: zonele au tinut la prima retestare in {z['rate']}% din cazuri (IC {z['ci_low']}-"
                   f"{z['ci_high']}%, n={z['n']}) fata de {p.get('rate')}% pentru benzi aleatoare de aceeasi latime "
                   f"(n={p.get('n')}) - {verdict}")
    return out


def _zone_meta(chart):
    frame = _zone_frame(chart)
    n_s = len([z for z in frame if z["side"] == "S"])
    return f"{n_s} suport · {len(frame) - n_s} rezistenta"


def render_zone_list(chart):
    """Zonele desenate pe grafic, cu de ce sunt acolo: timeframe, interval, forta, atingeri,
    confluente, distanta - si rata lor masurata de respectare."""
    rows = []
    for z in _zone_frame(chart):
        sup = z["side"] == "S"
        src = ", ".join(z.get("sources") or []) or "fara confluente"
        state = "proaspata" if z.get("fresh") else f"{z.get('touches', 0)} atingeri"
        kind = {"cerere": "cerere", "oferta": "oferta", "flip": "flip (zona sparta, rol inversat)"}.get(
            z.get("kind"), z.get("kind") or "")
        rows.append(f'<div class="zrow {"zrow-s" if sup else "zrow-r"}"><strong>{"SUPORT" if sup else "REZISTENTA"} '
                    f'{_esc(str(z.get("tf") or "").upper())}</strong> {fmt(z["lo"])} – {fmt(z["hi"])} '
                    f'<span class="dim">· {z.get("dist_pct", 0):+.1f}% · forta {z.get("strength", 0)}/100 · {state} · '
                    f'{_esc(kind)}{" · confirmata si pe timeframe-ul mic" if z.get("confirmed") else ""}'
                    f'{" · rol inversat in asteptare: pretul a trecut de zona fara inchidere dincolo de ea" if z.get("pending") else ""}'
                    f' · {_esc(src)}</span></div>')
    if not rows:
        rows.append('<p class="dim">Nicio zona activa la cel mult 25% de pret pe istoricul disponibil.</p>')
    else:
        sides = {z["side"] for z in _zone_frame(chart)}
        # o parte goala are o cauza concreta, nu e o eroare: pretul e la extrema seriei, iar un
        # minim/maxim devine pivot (deci zona) abia dupa 5 bare de fiecare parte
        if "S" not in sides:
            rows.append('<p class="dim">Niciun suport confirmat sub pret: pretul e sub toate zonele active sau la '
                        'minimul seriei - un minim devine zona abia dupa 5 bare de confirmare.</p>')
        if "R" not in sides:
            rows.append('<p class="dim">Nicio rezistenta confirmata deasupra pretului: pretul e peste toate zonele '
                        'active sau la maximul seriei - un maxim devine zona abia dupa 5 bare de confirmare.</p>')
    stats = "".join(f'<p class="dim zstat">{_esc(t)}</p>' for t in zone_stats_text(chart.get("zone_stats")))
    return "".join(rows) + stats


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
                   render_chart(chart, MAIN_LAYERS, 390, fullscreen_id=f"max-{uid}"),
                   open_=True),
        _component("Elliott Wave · toate scenariile",
                   f"{ew.get('alive', 0)} din {ew.get('total', 0)} scenarii valide",
                   render_chart(chart, {"ew_primary", "ew_all", "ew_levels",
                                        "projection", "price_axis"}, 340, show_head=False),
                   open_=True),
        _component("Plan · LOCKED vs CURRENT",
                   f"plan #{lock['id']} blocat" if lock else "doar niveluri CURRENT",
                   render_chart(chart, {"plan", "locked", "price_axis"}, 300,
                                show_head=False)),
        _component("Lichiditate · heatmap si structura",
                   (f"{len(ls.get('levels') or [])} niveluri structurale"
                    + (" · sweep bilateral" if ls.get("two_sided_sweep") else "")),
                   render_chart(chart, {"liq_heat", "liq_struct", "price_axis"}, 320,
                                show_head=False)),
        _component("Zone de suport / rezistenta", _zone_meta(chart),
                   render_chart(chart, {"zones", "price_axis"}, 320, show_head=False)
                   + render_zone_list(chart)),
        _component("Indicatori · VWAP, Volume Profile, SuperTrend",
                   f"SuperTrend {st.get('direction', 'N/A')}",
                   render_chart(chart, {"ema", "indicators", "price_axis"}, 320,
                                show_head=False)),
        _component("Volum · RSI · MACD",
                   f"RSI {rs[-1]:.1f}" if rs else "RSI N/A",
                   render_subpanels(chart)),
    ]
    return '<div class="cc-stack">' + "".join(parts) + "</div>"


# CSS-ul componentelor si al graficelor, pe fundal alb, dupa conventiile graficelor de prezentare
# profesionala: paleta sobra (tonuri mai putin saturate), etichete cu text inchis pe fond deschis,
# culoarea doar ca accent (contur, linie), cifre monospace aliniate, minimum ~11 px pe telefon.
# Regulile de text sunt calificate cu .rc: altfel reguli mai vechi din main.css (.proj-t de 7.5 px)
# le suprascriau.
CSS = """
.cc-stack{display:flex;flex-direction:column;gap:10px;}
.cc{border:1px solid #E3E8EF;border-radius:10px;background:#FFFFFF;overflow:hidden;}
.cc>summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:10px;
  padding:10px 14px;background:#F8FAFC;border-bottom:1px solid transparent;}
.cc[open]>summary{border-bottom-color:#E3E8EF;}
.cc>summary::-webkit-details-marker{display:none;}
.cc>summary::before{content:"▸";color:#475569;font-size:12px;transition:transform .15s;}
.cc[open]>summary::before{transform:rotate(90deg);}
.cc-title{font-weight:700;font-size:14px;color:#0F172A;white-space:nowrap;}
.cc-meta{font-size:12px;color:#475569;font-family:var(--font-mono);margin-left:auto;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;}
.cc-body{padding:10px 12px 12px;}
.rc-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:13px;
  font-family:var(--font-mono);margin-bottom:6px;color:#0F172A;}
.rc-dir{padding:2px 8px;border-radius:4px;font-weight:700;font-size:12px;}
.rc-long{background:#E7F3EE;color:#1F6B50;} .rc-short{background:#F8E9E8;color:#9E3530;}
.rc-scroll{overflow-x:auto;display:flex;flex-direction:row-reverse;-webkit-overflow-scrolling:touch;}
.rc{display:block;width:100%;height:auto;background:#FFFFFF;}
@media(max-width:760px){.rc{min-width:720px;}}
.rc text{font-family:var(--font-mono);}
.rc .grid{stroke:#EDF0F4;stroke-width:1;}
.rc .axis-t{font-size:12px;fill:#475569;}
.cu{stroke:#2E8B6A;stroke-width:1;} .cd{stroke:#C8463F;stroke-width:1;}
.cuf{fill:#2E8B6A;} .cdf{fill:#C8463F;}
.ema9{stroke:#6FAFA6;stroke-width:1.3;} .ema20{stroke:#D9944A;stroke-width:1.3;}
.ema50{stroke:#B5625E;stroke-width:1.3;} .ema200{stroke:#2F5D9E;stroke-width:2.2;}
.lvl{stroke-width:1;stroke-dasharray:5 3;opacity:.85;}
.lvl-tp,.lvl-stb{stroke:#2E8B6A;} .lvl-sl,.lvl-inv,.lvl-sts{stroke:#C8463F;}
.lvl-entry,.lvl-vwap,.lvl-val{stroke:#2F6FB0;} .lvl-locked{stroke:#5B6675;}
.lvl-ew{stroke:#6B5BA8;} .lvl-vah,.lvl-poc,.lvl-liqb,.lvl-liqs{stroke:#C98A3E;}
.lvl-nextentry{stroke:#A8832E;stroke-dasharray:7 3;} .lvl-hit{stroke:#9AA4B2;}
.heat-s{fill:#C8463F;} .heat-l{fill:#2E8B6A;}
/* pastile: fond deschis, contur in culoarea nivelului, text aproape negru */
.pill{stroke-width:1;}
.rc .pill-t{font-size:12px;font-weight:600;fill:#111827;}
.rc .pill-pxt{font-weight:700;}
.pill-tp,.pill-stb,.pill-zsup{fill:#E7F3EE;stroke:#2E8B6A;}
.pill-sl,.pill-inv,.pill-sts{fill:#F8E9E8;stroke:#C8463F;}
.pill-entry,.pill-vwap,.pill-val{fill:#E8EFF8;stroke:#2F6FB0;}
.pill-locked{fill:#EEF0F3;stroke:#5B6675;}
.pill-ew,.pill-ewh{fill:#EFEDF7;stroke:#6B5BA8;} .pill-ewa{fill:#E8F0F9;stroke:#3B7DC4;}
.pill-hit{fill:#F3F4F6;stroke:#9AA4B2;} .pill-nextentry{fill:#F6F0E1;stroke:#A8832E;}
.pill-vah,.pill-poc,.pill-liqb,.pill-zres{fill:#F8EFE3;stroke:#C98A3E;} .pill-liqs{fill:#F6EBDD;stroke:#A9672A;}
.pill-zsup2{fill:#F1F8F5;stroke:#8DBFAE;} .pill-zres2{fill:#FBF4EA;stroke:#DDB98A;}
.pill-now{fill:#E6ECF7;stroke:#2B4F8C;} .lvl-now{stroke:#2B4F8C;stroke-dasharray:2 2;opacity:.9;}
.pill-invsoft{fill:#F5E6E6;stroke:#8E2B2B;} .lvl-invsoft{stroke:#8E2B2B;stroke-dasharray:6 3;opacity:.85;}
/* etichete pe grafic: text inchis, contur alb dedesubt */
.rc .halo{fill:#FFFFFF;stroke:#FFFFFF;stroke-width:4px;stroke-linejoin:round;}
.ew0-l{stroke:#6B5BA8;stroke-width:2;} .ew0-d{fill:#6B5BA8;}
.rc .ew0-t-h,.rc .ew0-t,.rc .proj-t-h,.rc .proj-t{font-size:12px;font-weight:700;}
.rc .ew0-t{fill:#3F3470;} .rc .proj-t{fill:#3F3470;}
.rc .ewx-t-h,.rc .ewx-t{font-size:12px;font-weight:600;} .rc .ewx-t{fill:#475569;}
.ew1-l{stroke:#3B7DC4;stroke-width:1.4;stroke-dasharray:5 3;} .ew1-d{fill:#3B7DC4;}
.ew2-l{stroke:#C98A3E;stroke-width:1.2;stroke-dasharray:2 3;} .ew2-d{fill:#C98A3E;}
.ewx-l{stroke:#9AA4B2;stroke-width:1;stroke-dasharray:1 4;} .ewx-d{fill:#C4CBD5;}
.live-l{stroke:#6B5BA8;stroke-width:1.8;} .live-d{fill:#6B5BA8;stroke:#FFFFFF;stroke-width:1.2;}
.proj-zone{fill:#6B5BA8;opacity:.045;}
.rc .proj-now{stroke:#8792A2;stroke-width:1;stroke-dasharray:3 3;opacity:1;}
.proj-l{stroke:#6B5BA8;stroke-width:1.6;stroke-dasharray:6 4;}
.proj-d{fill:#FFFFFF;stroke:#6B5BA8;stroke-width:1.5;}
.rc .proj-tag,.rc .proj-tag-h{font-size:12px;font-weight:600;opacity:1;} .rc .proj-tag{fill:#334155;}
.rsi{stroke:#6B5BA8;stroke-width:1.3;}
.zone.zs{fill:#2E8B6A;opacity:.07;} .zone.zr{fill:#C98A3E;opacity:.09;}
.zone.zs.z1d{opacity:.13;} .zone.zr.z1d{opacity:.15;}
.zl{stroke-width:1;} .zl.zs{stroke:#2E8B6A;opacity:.75;} .zl.zr{stroke:#C98A3E;opacity:.8;}
.zl.zsc{stroke-dasharray:5 3;stroke-width:.9;}
.zrow{font-size:13px;padding:6px 9px;border-left:3px solid;margin:4px 0;border-radius:4px;background:#F8FAFC;color:#1F2937;}
.zrow-s{border-left-color:#2E8B6A;} .zrow-r{border-left-color:#C98A3E;}
.zstat{font-size:12px;margin:6px 0 0;}
.rc .sub-t{font-size:12px;font-weight:600;fill:#334155;}
"""
