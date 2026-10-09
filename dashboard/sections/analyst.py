# -*- coding: utf-8 -*-
"""dashboard.sections.analyst - Raportul analistului de rotatie a capitalului, in cele patru sectiuni:
diagnostic macro (alt season check), sectoare momentum, filtrare alfa, strategie executiva - plus
graficul BTC 1D cu zonele de suport/rezistenta si nivelul de invalidare."""

import html as _html

import chart_render


def _e(s):
    return _html.escape(str(s))


def _li(label, text, extra=""):
    return f'<li><strong>{_e(label)}:</strong> {_e(text)}{extra}</li>'


def _usd(v):
    return "n/d" if v is None else "$" + chart_render.fmt(v)


def _spark(series):
    """Indicele altseason pe ultimele 90 de zile (seria zilnica reconstruita), cu pragul 75."""
    pts = [(d, v) for d, v in series or [] if v is not None]
    if len(pts) < 3:
        return ""
    W, H, P = 240, 46, 3
    xs = lambda i: P + (W - 2 * P) * i / (len(pts) - 1)
    ys = lambda v: P + (H - 2 * P) * (1 - v / 100)
    line = " ".join(f"{xs(i):.1f},{ys(v):.1f}" for i, (_d, v) in enumerate(pts))
    return (f'<svg class="an-spark" viewBox="0 0 {W} {H}" role="img" aria-label="Indicele altseason pe 90 de zile">'
            f'<line x1="{P}" x2="{W - P}" y1="{ys(75):.1f}" y2="{ys(75):.1f}" class="an-sp75"/>'
            f'<polyline points="{line}" class="an-spl" fill="none"/>'
            f'<circle cx="{xs(len(pts) - 1):.1f}" cy="{ys(pts[-1][1]):.1f}" r="2.6" class="an-spd"/>'
            f'<text x="{P}" y="{H - 1}" class="an-spt">{_e(pts[0][0])}</text>'
            f'<text x="{W - P}" y="{H - 1}" class="an-spt" text-anchor="end">{_e(pts[-1][0])}</text></svg>')


def _btc_chart(rep):
    btc, st = rep.get("btc") or {}, rep.get("strategy") or {}
    ch = btc.get("chart") or {}
    if len(ch.get("candles") or []) < 5:
        return '<p class="dim">Graficul BTC 1D apare după prima scanare cu lumânări zilnice.</p>'
    # lumanarile sunt zilele INCHISE (din ele se calculeaza zonele); pretul de acum, din ziua in
    # curs, e linia albastra - pozitia lui fata de zone decide rolul lor (suport sau rezistenta)
    px = btc.get("price")
    marks = [{"price": px, "label": "PRET ACUM", "kind": "now"}] if px else []
    if st.get("inv_price"):
        marks.append({"price": st["inv_price"], "label": "INVALIDARE TEZA", "kind": "inv"})
    chart = {"symbol": "BTC/USDT · 1D", "direction": st.get("sentiment", ""), "timeframe": "1d",
             "candles": ch["candles"], "ema50": ch.get("ma50"), "ema200": ch.get("ma200"), "price": px,
             "zones": {"frame": [dict(z, tf="1d") for z in (btc.get("zones") or {}).get("zones") or []]},
             "marks": marks}
    return (chart_render.render_chart(chart, {"zones", "ema", "marks", "price_axis"}, 300)
            + '<div class="legend an-legend"><span><i class="dot" style="background:#E53935"></i>MA 50 zile</span>'
              '<span><i class="dot" style="background:#1565C0"></i>MA 200 zile</span>'
              '<span><i class="dot" style="background:#089981"></i>suport 1D</span>'
              '<span><i class="dot" style="background:#E67E22"></i>rezistență 1D</span>'
              '<span><i class="dot" style="background:#2962FF"></i>preț acum</span>'
              '<span><i class="dot" style="background:#F23645"></i>invalidare</span></div>')


SENT_CLS = {"Bullish": "tag-bull", "Bearish": "tag-bear"}


def _geo(a, price):
    """Geometria R:R din zonele 1D, intr-un rand."""
    a = a or {}
    out = []
    if a.get("rr") is not None:
        out.append(f'R:R {a["rr"]:.1f}' + (" · ASIMETRIC" if a.get("asymmetric") else "")
                   + f' (intrare {_usd(price)}, stop {_usd(a.get("stop"))}, țintă {_usd(a.get("target"))})')
    else:
        out.append("R:R indisponibil (lipsește suportul 1D sub preț sau ținta deasupra)")
    if a.get("support"):
        out.append(f'suport 1D {_usd(a["support"]["lo"])}–{_usd(a["support"]["hi"])}')
    if a.get("resistance"):
        out.append(f'rezistență 1D {_usd(a["resistance"]["lo"])}–{_usd(a["resistance"]["hi"])}')
    return " · ".join(out)


def render_token_steps(t):
    """Pasii 1-4 ai analistului pentru UN token: pe cardul fiecarui token si sub graficul
    principal. Aceleasi etichete ca raportul de piata."""
    if not t:
        return ('<p class="dim">Analiza în 4 pași pentru acest token apare după prima scanare cu versiunea nouă '
                '(are nevoie de lumânările zilnice ale tokenului).</p>')
    d, sec, a, st = t.get("diag") or {}, t.get("sector") or {}, t.get("alpha") or {}, t.get("strategy") or {}
    sent = st.get("sentiment") or "n/d"
    why = "; ".join(st.get("why") or [])
    return (f'<div class="an-tok"><div class="an-tok-h"><span class="tag {SENT_CLS.get(sent, "tag-info")}">'
            f'{_e(sent.upper())}</span> <span class="dim">{_e(st.get("alignment") or "")}</span></div>'
            '<ol class="an-steps">'
            f'<li><strong>Diagnostic:</strong> {_e(d.get("text") or "n/d")}'
            f'<div class="an-why">{_e(d.get("rotation") or "")}</div></li>'
            f'<li><strong>Sector:</strong> {_e(sec.get("text") or "n/d")}<ul class="an-sub">'
            f'<li><em>Catalizator:</em> {_e(sec.get("catalyst") or "n/d")}</li>'
            f'<li><em>Sustenabilitate:</em> {_e(sec.get("sustain") or "n/d")}</li></ul></li>'
            f'<li><strong>Filtrare alfa:</strong><ul class="an-sub">'
            f'<li><em>Argument:</em> {_e(a.get("argument") or "n/d")}</li>'
            f'<li><em>Risc Critic:</em> {_e(a.get("risk") or "n/d")}</li></ul>'
            f'<div class="an-rr{" an-asym" if a.get("asymmetric") else ""}">{_e(_geo(a, t.get("price")))}</div></li>'
            f'<li><strong>Strategie:</strong><ul class="an-sub">'
            f'<li><em>Sentiment:</em> {_e(sent)}' + (f' <span class="an-why">({_e(why)})</span>' if why else "") + '</li>'
            f'<li><em>Ghid de Intrare:</em> {_e(st.get("guide") or "n/d")}</li>'
            f'<li><em>Trigger de Invalidare:</em> {_e(st.get("invalidation") or "n/d")}</li></ul></li>'
            '</ol></div>')


def _calls_text(c):
    """Verdictele per token, verificate la 7 si 30 de zile - sau de cand se acumuleaza."""
    c = c or {}
    hz = c.get("horizons") or {}
    parts = []
    for h in ("7", "30"):
        r = hz.get(h) or {}
        if not r:
            continue
        seg = []
        for sent in ("Bullish", "Neutru", "Bearish"):
            x = r.get(sent)
            if not x:
                continue
            hit = ("" if x.get("hit") is None else
                   f", {'au bătut' if sent == 'Bullish' else 'au pierdut față de'} BTC în {x['hit']:.0f}%")
            seg.append(f"{sent} n={x['n']}: medie {x['mean_rel']:+.1f}% vs BTC{hit}")
        if seg:
            parts.append(f"la {h} zile — " + "; ".join(seg))
    if parts:
        return "Verdictele per token, verificate pe lumânările zilnice: " + " · ".join(parts) + "."
    since = c.get("first_day")
    return ("Verdictele per token se înregistrează zilnic" + (f" din {since}" if since else "")
            + "; primele verificări la 7 zile, apoi la 30 de zile - până atunci nu au o rată măsurată.")


def render_token_list(tokens, links=None):
    """Toti tokenii scanati intr-o lista compacta: sentimentul, sectorul, R:R si invalidarea -
    cu legatura spre cardul tokenului (pasii 1-4 complet)."""
    links = links or {}
    order = {"Bullish": 0, "Neutru": 1, "Bearish": 2}
    rows = []
    for b, t in sorted((tokens or {}).items(), key=lambda kv: (order.get(kv[1]["strategy"]["sentiment"], 3),
                                                               -((kv[1]["alpha"] or {}).get("rr") or -1))):
        st, a, sec = t["strategy"], t.get("alpha") or {}, t.get("sector") or {}
        sent = st.get("sentiment") or "n/d"
        name = f'<a href="{_e(links[b])}">{_e(b)}</a>' if links.get(b) else _e(b)
        rr = f'R:R {a["rr"]:.1f}' if a.get("rr") is not None else "R:R n/d"
        inv = f'inv. {_usd(st.get("inv_price"))}' if st.get("inv_price") else "inv. n/d"
        rows.append(f'<div class="an-trow"><span class="an-tk">{name}</span>'
                    f'<span class="tag {SENT_CLS.get(sent, "tag-info")}">{_e(sent)}</span>'
                    f'<span class="an-tc">{_e(sec.get("catalyst") or "")}</span>'
                    f'<span class="an-tn{" an-asym" if a.get("asymmetric") else ""}">{_e(rr)}</span>'
                    f'<span class="an-tn">{_e(inv)}</span></div>')
    return "".join(rows)


def render_analyst(rep, links=None):
    """Raportul complet. Fiecare rand vine dintr-o cifra masurata la aceasta scanare (vezi
    core/analyst.py); cand o sursa lipseste, randul spune ce lipseste, nu inventeaza.
    `links`: {ticker: legatura spre cardul tokenului de pe pagina bursei active}."""
    if not rep or not rep.get("macro"):
        return ('<p class="dim">Raportul analistului apare după prima scanare cu versiunea nouă '
                '(altseason, zone 1D, sectoare).</p>')
    fr, m = rep.get("fresh") or {}, rep["macro"]
    bd, ph, ai, ty = m.get("btc_d") or {}, m.get("phase") or {}, m.get("alt_index") or {}, m.get("ten_year") or {}
    st = rep.get("strategy") or {}
    sent = st.get("sentiment") or "n/d"
    scls = {"Bullish": "tag-bull", "Bearish": "tag-bear"}.get(sent, "tag-info")
    ok = fr.get("same_scan") and fr.get("history_ok")
    fresh = ('<span class="tag tag-ok">date la zi</span>' if ok else
             '<span class="tag tag-bear">ATENȚIE: date vechi</span>')
    head = (f'<div class="an-head"><span class="tag {scls}">SENTIMENT {_e(sent.upper())}</span> {fresh} '
            f'<span class="dim">raport {_e(rep.get("when"))} · altseason evaluat {_e(fr.get("alt_when") or "n/d")}'
            f' · istoric 10 ani până la {_e(fr.get("history_last_day") or "n/d")}'
            f' · indice 90z recalculat pe {_e(fr.get("perf90_day") or "n/d")} (zile închise)</span></div>')
    if rep.get("error"):
        head += (f'<div class="plan-conflict"><strong>RAPORT VECHI</strong> &middot; ultima actualizare a eșuat '
                 f'({_e(rep.get("error_at") or "")}): {_e(rep["error"])}</div>')

    tl = "".join(f"<li>{_e(x)}</li>" for x in ty.get("lines") or [])
    s1 = ('<h3 class="an-h">1. DIAGNOSTIC MACRO (ALT SEASON CHECK)</h3><ul class="an-ul">'
          + _li("Status BTC Dominance (BTC.D)", bd.get("text") or "n/d")
          + _li("Faza Rotației de Capital", ph.get("text") or "n/d")
          + _li("Altcoin Index", ai.get("text") or "n/d", _spark(ai.get("series")))
          + "</ul>"
          + (f'<div class="an-10y"><span class="scan-h">Raportat la statistica ultimilor 10 ani</span>'
             f'<ul class="an-ul">{tl}</ul></div>' if tl else ""))

    sec = rep.get("sectors") or {}
    items = []
    for s in sec.get("top") or []:
        lead = ", ".join(f'{x["symbol"]} {x["rs30"]:+.0f}%' for x in s.get("leaders") or [])
        items.append(f'<li><strong>{_e(s["name"])}:</strong><ul class="an-sub">'
                     f'<li><em>Catalizator:</em> {_e(s["catalyst"])}</li>'
                     f'<li><em>Sustenabilitate:</em> {_e(s["sustain"])} <span class="an-why">({_e(s["sustain_why"])})</span></li>'
                     f'</ul><div class="an-why">lideri vs BTC pe 30 de zile: {_e(lead)}</div></li>')
    if not items and not sec.get("covered"):
        # fara date pe sectoare nu se poate spune unde e capitalul - doar ca datele se incarca
        items.append('<li>Datele pe sectoare se încarcă: câte 4 sectoare CoinGecko la fiecare scanare, '
                     'toate după aproximativ 3 scanări. Până atunci secțiunea nu trage concluzii.</li>')
    elif not items:
        items.append('<li>Niciun sector nu bate BTC pe 30 de zile cu cel puțin jumătate din monede '
                     f'({sec.get("covered", 0)} din {sec.get("of", 0)} sectoare cu date) — capitalul rămâne în BTC.</li>')
    s2 = ('<h3 class="an-h">2. SECTOARE MOMENTUM (MAXIM 2-3 NARAȚIUNI)</h3><ul class="an-ul">' + "".join(items) + "</ul>"
          + '<p class="an-note">Catalizatorul e motorul măsurat al mișcării (forța relativă față de BTC și lățimea '
            'sectorului), nu o știre. '
          + (f'{sec.get("covered", 0)} din {sec["of"]} sectoare CoinGecko cu date; ' if sec.get("of") else "")
          + 'fiecare sector se reîmprospătează o dată la 3 ore.</p>')

    rows = []
    for p in rep.get("alpha") or []:
        sup, res = p.get("support") or {}, p.get("resistance") or {}
        geo = []
        if p.get("rr") is not None:
            geo.append(f'R:R {p["rr"]:.1f}' + (" · ASIMETRIC" if p.get("asymmetric") else "")
                       + f' (intrare {_usd(p.get("price"))}, stop {_usd(p.get("stop"))}, țintă {_usd(p.get("target"))})')
        if sup:
            geo.append(f'suport 1D {_usd(sup.get("lo"))}–{_usd(sup.get("hi"))}')
        if res:
            geo.append(f'rezistență 1D {_usd(res.get("lo"))}–{_usd(res.get("hi"))}')
        if p.get("beta") is not None:
            geo.append(f'β {p["beta"]:.2f} / corelație {p["corr"]:.2f} cu BTC')
        sig = p.get("signal") or {}
        if sig.get("direction"):
            geo.append(f'scaner: {sig["direction"]} {sig.get("score")}/100')
        if p.get("other_asset"):
            geo.append("pe bursă, același ticker e alt activ (preț diferit de CoinGecko) - fără zone și R:R")
        elif p.get("has_daily") is False:
            geo.append("fără lumânări zilnice pe bursă (nelistat sau peste limita de cereri pe scanare) - fără zone și R:R")
        elif p.get("rr") is None:
            geo.append("R:R indisponibil: lipsește suportul 1D sub preț sau ținta deasupra")
        rows.append(f'<li><strong>{_e(p["ticker"])}:</strong> <span class="an-why">{_e(p.get("name") or "")}'
                    + (f' · #{p["rank"]}' if p.get("rank") else "") + f' · {_e(p.get("source"))}</span>'
                    f'<ul class="an-sub"><li><em>Argument:</em> {_e(p["argument"])}</li>'
                    f'<li><em>Risc Critic:</em> {_e(p["risk"])}</li></ul>'
                    f'<div class="an-rr{" an-asym" if p.get("asymmetric") else ""}">{_e(" · ".join(geo))}</div></li>')
    if not rows:
        rows.append('<li>Niciun proiect nu bate BTC simultan pe 7 și pe 30 de zile în datele acestei scanări.</li>')
    zs = chart_render.zone_stats_text(rep.get("zone_stats"))
    s3 = ('<h3 class="an-h">3. FILTRARE ALFA (PROIECTE SPECIFICE)</h3><ul class="an-ul">' + "".join(rows) + "</ul>"
          + '<p class="an-note">Selecție: bat BTC pe 7 ȘI pe 30 de zile. R:R e geometrie din zonele 1D (stop sub baza '
            'suportului, țintă la prima rezistență), nu probabilitate; asimetric = R:R ≥ 3. Riscul critic e măsurat: '
            'oferta în circulație și FDV (deblocări), volumul (lichiditate), β și corelația cu BTC.'
          + (" " + _e(" ".join(zs)) if zs else "") + "</p>")

    why = "; ".join(st.get("why") or [])
    s4 = ('<h3 class="an-h">4. STRATEGIE EXECUTIVĂ</h3><ul class="an-ul">'
          + _li("Sentiment General", sent + (f" — {why}" if why else ""))
          + _li("Ghid de Intrare", st.get("guide") or "n/d")
          + _li("Trigger de Invalidare", st.get("invalidation") or "n/d") + "</ul>")
    toks = rep.get("tokens") or {}
    s5 = ""
    if toks:
        cnt = {k: sum(1 for t in toks.values() if t["strategy"]["sentiment"] == k) for k in ("Bullish", "Neutru", "Bearish")}
        s5 = (f'<details class="an-tokens"><summary><span class="an-h">PAȘII 1–4 PENTRU FIECARE TOKEN SCANAT</span> '
              f'<span class="dim">{len(toks)} tokeni · Bullish {cnt["Bullish"]} · Neutru {cnt["Neutru"]} · '
              f'Bearish {cnt["Bearish"]}</span></summary>'
              f'<div class="an-tlist">{render_token_list(toks, links)}</div>'
              f'<p class="an-note">Aceleași reguli ca pentru piață, aplicate fiecărui token din lista din care învață '
              f'agentul: forța față de BTC și faza rotației, sectorul și momentum-ul lui, R:R din zonele 1D și riscul '
              f'măsurat, apoi sentimentul (în interiorul sentimentului pieței), intrarea și invalidarea. Detaliul complet '
              f'e pe cardul fiecărui token. {_e(_calls_text(rep.get("calls")))}</p></details>')
    return f'{head}<div class="an-body">{s1}{s2}{s3}{s4}{s5}</div>{_btc_chart(rep)}'
