# -*- coding: utf-8 -*-
"""dashboard.sections.tokens - Detaliile fiecarui token: Elliott, lichidare, structura, grafic."""



import html as _html

from dashboard.components import fmt_price, honest_probability, render_rich_chart, render_sparkline

# Decizia agentului, in cuvinte. Pe bursa care invata, ISSUE inseamna plan emis;
# pe celelalte, doar ce AR face agentul (propunere simulata, neantrenata).
DECISION_TEXT = {
    "POARTA_DEZACTIVATA": ("ok", "emis", "ar emite"),
    "EXPLORARE": ("ok", "emis (explorare)", "ar emite (explorare)"),
    "EXPLORARE_FILTRU": ("warn", "emis (explorare)", "ar emite (explorare)"),
    "EXPLORARE_ELLIOTT": ("warn", "emis (explorare)", "ar emite (explorare)"),
    "ASTEAPTA_CORECTIA": ("bad", "asteapta corectia", "ar astepta corectia"),
    "FILTRU_AGENT": ("bad", "filtrat de agent", "ar filtra semnalul"),
    "MOD_SIGURANTA": ("bad", "mod de siguranta", "ar refuza (mod de siguranta)"),
    "REGULA_CERCETARE": ("bad", "exclus de o regula", "ar exclude (regula din cercetare)"),
    "EXPLORARE_REGULA": ("warn", "emis (explorare)", "ar emite (explorare)"),
}


def token_anchor(sym):
    """Ancora HTML a panoului unui token (BTC/USDT -> tok-BTC-USDT)."""
    return "tok-" + "".join(ch if ch.isalnum() else "-" for ch in str(sym))


def decision_label(prop, trained):
    """(clasa, text) pentru decizia unei propuneri."""
    if not prop:
        return None, None
    if prop.get("open_plan"):
        return "info", "plan deschis"
    mode = (prop.get("decision") or {}).get("mode") or ""
    cls, done, would = DECISION_TEXT.get(mode, ("info", mode.lower() or "-", mode.lower() or "-"))
    if not mode and (prop.get("decision") or {}).get("action") == "ISSUE":
        cls, done, would = "ok", "emis", "ar emite"
    return cls, (done if trained else would)


def render_proposal(prop, trained=True, learn_label=None, ready=True):
    """Propunerea de intrare a agentului pentru un token, la fel de detaliata pe
    orice bursa: decizia, probabilitatea masurata si a agentului, verdictul
    intrarilor comparabile, evidentele si conflictul Elliott.

    Pe bursele care nu invata, totul e calculat la fel, dar decizia e SIMULATA:
    agentul nu deschide plan si nu invata din ea (acelasi token are practic
    acelasi pret pe toate bursele - invatarea ar numara fiecare rezultat de mai
    multe ori)."""
    if not prop:
        if not ready:
            return ('<p class="dim">Propunerile agentului apar dupa prima scanare cu modulele '
                    'per bursa.</p>')
        return ('<p class="dim">Fara propunere: semnalul nu e in topul pe directie al '
                'acestei scanari (8 pe LONG, 8 pe SHORT).</p>')
    if prop.get("open_plan"):
        return ('<div class="prop-head prop-info"><strong>Plan deschis</strong> &middot; agentul '
                'urmareste deja un plan pe acest token si directie - vezi istoricul de mai jos.</div>')
    dec = prop.get("decision") or {}
    cls, txt = decision_label(prop, trained)
    who = ("" if trained else
           f' <span class="tag tag-shadow">neantrenat</span>')
    reason = _html.escape(str(dec.get("reason") or ""))
    out = [f'<div class="prop-head prop-{cls}"><strong>{txt.capitalize()}</strong>{who}'
           f'<div class="prop-why">{reason}</div></div>']
    lp = prop.get("learning_open_plan")
    if not trained and lp:
        out.append(f'<p class="dim prop-note">Pe {learn_label or "bursa care invata"} agentul are deja '
                   f'planul #{lp.get("id")} ({lp.get("symbol")}, {str(lp.get("state", "")).lower()}) '
                   f'pe acest token si directie.</p>')
    cal, agp = dec.get("calibrated_prob"), dec.get("agent_prob")
    cells = []
    cells.append('<div><span class="dim">PROBABILITATE MASURATA</span><br>{}</div>'.format(
        f"{cal}%" if cal is not None else "necalibrat"))
    cells.append('<div><span class="dim">AGENT{}</span><br>{}</div>'.format(
        "" if (prop.get("agent") or {}).get("active") else " (SHADOW)",
        f"{agp * 100:.1f}%" if isinstance(agp, (int, float)) else "-"))
    lv = prop.get("levels") or {}
    if lv.get("expected_r") is not None:
        cells.append(f'<div><span class="dim">R LA TP2</span><br>{lv["expected_r"]}R</div>')
    n = prop.get("neighbors") or {}
    if n.get("verdict"):
        # cu intervalul de incredere al diferentei fata de media generala (core.agent.
        # comparable_entries): "neutru" = in marja zgomotului, nu "usor favorabil"
        ci = (f', {n["points"]:+.1f} pct, IC95 {n["ci_low"]:+.0f}..{n["ci_high"]:+.0f}'
              if isinstance(n.get("ci_low"), (int, float)) and isinstance(n.get("points"), (int, float)) else "")
        cells.append(f'<div><span class="dim">COMPARABILE</span><br>{n["verdict"].lower()} '
                     f'({n.get("win_rate")}% din {n.get("n")}{ci})</div>')
    out.append('<div class="tok-meta prop-meta">' + "".join(cells) + "</div>")
    ec = prop.get("elliott_conflict") or {}
    if ec.get("bias") is not None and ec["bias"] <= -0.1:
        nxt = ec.get("next_entry")
        out.append('<div class="plan-conflict">Elliott contrazice directia ({}).{}</div>'.format(
            _html.escape(str(ec.get("headline") or ec.get("text") or "structura curenta")),
            f' Nivel urmarit pentru intrare: <strong>{fmt_price(nxt)}</strong>.' if nxt else ""))
    f = prop.get("fusion") or {}
    ev = prop.get("evidence") or []
    if ev:
        d = prop.get("direction")
        rows = []
        for e in ev:
            ecls = "ok" if e.get("direction") == d else ("neu" if e.get("direction") == "NEUTRU" else "bad")
            mark = {"ok": "sustine", "neu": "context", "bad": "contrazice"}[ecls]
            rows.append('<div class="ev-row ev-{}"><span class="ev-mark">{}</span>'
                        '<span class="ev-label">{}</span><span class="ev-bar"><i style="width:{}%">'
                        '</i></span></div>'.format(ecls, mark, _html.escape(str(e.get("label", ""))),
                                                   int(round((e.get("strength") or 0) * 100))))
        head = ""
        if f.get("score") is not None:
            head = ('<div class="ev-fusion">Evidente: <strong>{}</strong> sustin, <strong>{}</strong> '
                    'contrazic, {} context &middot; aliniere <strong>{}%</strong></div>').format(
                        f.get("support"), f.get("oppose"), f.get("neutral"), f.get("score"))
        out.append(head + '<details class="ev-more"><summary>Toate evidentele ({})</summary>'
                   '<div class="ev-list">{}</div></details>'.format(len(ev), "".join(rows)))
    return "".join(out)


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


def render_token_details(details, plans_store, watchlist=None, proposals=None,
                         trained=True, learn_label=None, proposals_ready=True):
    """Un panou pliabil per simbol scanat. Foloseste <details>/<summary> nativ:
    zero JavaScript, merge in orice browser, se deschide cu un tap pe telefon,
    si ramane inchis implicit ca pagina sa nu devina grea.

    Aceeasi functie deseneaza modulul ORICAREI burse: `proposals` sunt propunerile
    agentului pe acea bursa, `trained` spune daca agentul invata de aici, iar
    `learn_label` e bursa care invata (istoricul planurilor e al ei)."""
    proposals = proposals or {}
    symbols = (details or {}).get("symbols") or {}
    if not symbols and not watchlist:
        return '<p class="dim">Detaliile apar dupa prima scanare cu semnale.</p>'

    # istoricul de planuri pe simbol, ca sa vezi ce a facut agentul pe fiecare
    by_symbol = {}
    for p in (plans_store or {}).get("plans", []):
        # pe bursele care nu invata, istoricul e al TOKEN-ului pe bursa care invata
        # (perechea poate diferi: BTC/USD pe Kraken, BTC/USDT pe OKX)
        key = p["symbol"] if trained else str(p["symbol"]).split("/")[0]
        by_symbol.setdefault(key, []).append(p)

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
        if not trained and learn_label and prob_txt != "-":
            prob_note += f", calibrat pe {learn_label}"
        prop = proposals.get(sym)
        pcls, ptxt = decision_label(prop, trained)
        prop_tag = f'<span class="tag tag-{pcls} tok-dec">{ptxt}</span>' if ptxt else ""
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

        hist = by_symbol.get(sym if trained else sym.split("/")[0], [])
        closed = [p for p in hist if p.get("realized_r") is not None]
        hist_html = '<p class="dim">Niciun plan inca pe acest simbol.</p>'
        hist_title = ("Istoricul planurilor pe acest simbol" if trained else
                      f"Istoricul planurilor pe {learn_label or 'bursa care invata'} (aici agentul nu invata)")
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

        blocks.append(f'''<details class="tok" id="{token_anchor(sym)}">
      <summary>
        <span class="tok-sym">{sym}</span>
        <span class="badge badge-{dcls}">{direction}</span>
        {prop_tag}
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
        <h4>Propunerea agentului</h4>{render_proposal(prop, trained, learn_label, proposals_ready)}
        <h4>Indicatori</h4>{ind_html}
        <div class="ema-line dim">{ema_html}</div>
        <h4>Componentele scorului</h4><div class="fib-list">{comp_html}</div>
        <h4>{hist_title}</h4>{hist_html}
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
