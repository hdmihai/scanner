# -*- coding: utf-8 -*-
"""dashboard.sections.plan - Planul AI, memoria planurilor si calibrarea probabilitatilor."""



from dashboard.config import STATE_STYLE
from dashboard.components import fmt_price


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
            same = dv.get("basis") == "aceeasi_perioada"
            win = dv.get("window") or ["?", "?"]
            if dv.get("status") == "sub_backtest" and same:
                head += ('<div class="plan-conflict"><strong>ALARMA: REZULTATELE LIVE SUNT SUB BACKTEST IN ACEEASI PERIOADA</strong> '
                         f'&middot; diferenta {dv["diff"]:+.3f}R/plan (IC95 {dv["ci_low"]:+.3f}..{dv["ci_high"]:+.3f}), '
                         f'{win[0]} - {win[1]} - semnificativa statistic. Aceleasi zile de piata dau rezultate diferite: '
                         'cauza e in executie sau in calcul, nu in piata.</div>')
            elif dv.get("status") in ("sub_backtest", "in_marja", "peste_backtest"):
                basis = (f'pe aceeasi perioada ({win[0]} - {win[1]})' if same else
                         ('fata de tot istoricul - fereastra comuna e prea mica pentru o comparatie corecta'
                          if dv.get("basis") else 'fata de tot istoricul'))
                head += (f'<p class="dim" style="margin:-6px 0 12px;">Live fata de backtest {basis}: '
                         f'{dv["diff"]:+.3f}R/plan (IC95 {dv["ci_low"]:+.3f}..{dv["ci_high"]:+.3f}) - '
                         + {"sub_backtest": "sub backtest", "in_marja": "in marja statistica",
                            "peste_backtest": "peste backtest"}[dv["status"]] + '.</p>')
            ed = summary.get("edge") or {}
            if ed.get("recent") and ed.get("status") in ("neconcludent", "negativ"):
                rec = ed["recent"]
                yrs = " &middot; ".join(f'{y} {v["r"]:+.3f}R' for y, v in (ed.get("by_year") or {}).items())
                head += ('<div class="plan-conflict explore"><strong>EDGE-UL S-A ERODAT</strong> &middot; '
                         f'backtest-ul pe ultimele {ed["recent_days"]} zile: {rec["r"]:+.3f}R/plan '
                         f'(IC95 {rec["ci_low"]:+.3f}..{rec["ci_high"]:+.3f}, n={rec["n"]}) - '
                         + ("negativ" if ed["status"] == "negativ" else "nu e distinct de zero")
                         + f'. Pe ani: {yrs}. Probabilitatile si R-ul asteptat din planuri sunt calibrate pe tot '
                         'istoricul, deci supraestimeaza avantajul de acum.</div>')

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
