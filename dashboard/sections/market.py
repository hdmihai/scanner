# -*- coding: utf-8 -*-
"""dashboard.sections.market - Sectiunile de piata: briefing, oportunitati, niveluri, indicatori, lichiditate, evidente."""



from dashboard.components import fmt_price


def render_briefing(brief):
    if not brief or not brief.get("text"):
        return ""
    src = brief.get("source", "determinist")
    tag = "Gemini" if src == "gemini" else "generat din statistici"
    return f'''<div class="card briefing-card">
    <h2>Briefing &middot; <span class="dim">{tag}</span></h2>
    <p class="briefing-text">{brief["text"]}</p>
  </div>'''


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
        # Verdictele salvate inainte de intervalul de incredere (fara "ci_low") erau doar
        # semnul diferentei - le arat neutru colorat, cu mentiunea asta, nu ca semnal.
        old = "ci_low" not in n
        nb = ('<div class="ev-neighbors nb-{}">Intrari comparabile: <strong>{}</strong> '
              '&middot; {}{}</div>').format(
                  "neu" if old else ("ok" if n["verdict"] == "FAVORABIL" else
                                     ("bad" if n["verdict"] == "NEFAVORABIL" else "neu")),
                  n["verdict"], n["reason"],
                  ' <span class="dim">(verdict salvat inainte de intervalul de incredere: doar semnul '
                  'diferentei, nu un semnal)</span>' if old else "")
    elif n.get("reason"):
        nb = '<div class="ev-neighbors nb-neu dim">{}</div>'.format(n["reason"])

    return head + '<div class="ev-list">' + "".join(rows) + "</div>" + nb
