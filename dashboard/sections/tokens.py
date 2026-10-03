# -*- coding: utf-8 -*-
"""dashboard.sections.tokens - Detaliile fiecarui token: Elliott, lichidare, structura, grafic."""



from dashboard.components import fmt_price, honest_probability, render_rich_chart, render_sparkline


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
