# -*- coding: utf-8 -*-
"""dashboard.sections.plan - Planul AI, memoria planurilor si calibrarea probabilitatilor."""



from core.plans import open_plan_for
from dashboard.config import SL_AFTER_TP1_LABEL, STATE_STYLE
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


def tracked_plan(plans_store, symbol, direction):
    """Planul ACTIV deja urmarit pentru simbol+directie - aceeasi functie pe care o
    foloseste scanarea (core.plans.open_plan_for, prin has_open_plan). Cand exista,
    scanarea NU emite plan nou si nu scrie nicio decizie pentru simbol."""
    if not symbol or not direction:
        return None
    try:
        return open_plan_for(plans_store or {}, symbol, direction)
    except (AttributeError, TypeError) as e:       # date stricate: cardul ramane ca inainte
        print(f"[!] tracked_plan: {e}")
        return None


def recent_calibration(plans_store):
    """Calibrarea pe ultimele 12 luni, calculata de nucleu la fiecare scanare
    (core.plans.calibration_by_period, in summary). {} daca lipseste sau e stricata."""
    try:
        rc = ((plans_store or {}).get("summary") or {}).get("calibration_recent") or {}
        return rc if isinstance(rc.get("buckets"), dict) and rc["buckets"] else {}
    except AttributeError:
        return {}


def _bucket(score):
    return str(int((score or 0) // 20) * 20)


def _recent_note(recent, b):
    """Rata aceluiasi interval de scor pe ultimele N zile, cu verdictul fata de
    perioada ANTERIOARA ferestrei (esantioane disjuncte; "sub"/"peste" doar cand
    intervalele Wilson nu se suprapun - calculat in nucleu)."""
    rec = ((recent or {}).get("buckets") or {}).get(b) or {}
    if not rec.get("reliable"):
        return ""
    pr = rec.get("prior") or {}
    verdict = {
        "sub": (f' &middot; <strong>semnificativ SUB perioada anterioara</strong> '
                f'({pr.get("win_rate")}%, IC {pr.get("ci_low")}-{pr.get("ci_high")}%, n={pr.get("total")})'),
        "peste": (f' &middot; semnificativ peste perioada anterioara '
                  f'({pr.get("win_rate")}%, IC {pr.get("ci_low")}-{pr.get("ci_high")}%, n={pr.get("total")})'),
        "in_marja": " &middot; in marja perioadei anterioare",
    }.get(rec.get("vs_prior"), "")
    return (f'ultimele {recent.get("days")} zile: {rec["win_rate"]}% (IC {rec["ci_low"]}-{rec["ci_high"]}%, '
            f'n={rec["total"]}, R mediu {rec["avg_r"]:+.3f}R){verdict}')


def _r_line(r_tp2, cal):
    """"Expected R" era |TP2 - entry| / risc (core/geometry.py): raportul castig/risc
    DACA se atinge TP2, nu o valoare asteptata. La DASH (8 oct) cardul arata
    "Expected R 4.5R", in timp ce R-ul mediu MASURAT pe intervalul de scor e
    +0.156R. Le afisez pe amandoua, fiecare cu numele lui."""
    parts = []
    if r_tp2 is not None:
        parts.append(f'R la TP2 &middot; <strong>{r_tp2}R</strong> '
                     '<span class="dim">(castig/risc daca atinge TP2, nu valoare asteptata)</span>')
    if cal.get("reliable") and cal.get("avg_r") is not None:
        parts.append(f'R mediu masurat pe interval &middot; <strong>{cal["avg_r"]:+.3f}R</strong> '
                     f'<span class="dim">(n={cal["total"]}, tot istoricul)</span>')
    return f'<div class="expected-r">{"<br>".join(parts)}</div>' if parts else ""


def _r_tp2(lv):
    try:
        risk = abs(lv["entry"] - lv["sl"])
        return round(abs(lv["tp2"] - lv["entry"]) / risk, 2) if risk else None
    except (KeyError, TypeError):
        return None


def render_plan(best, deep, calibration=None, decision=None, ew_stats=None, open_plan=None, recent=None):
    """Cardul "AI plan". CONFIDENCE afisa formula din scor (ex. 76.2%), desi
    probabilitatea MASURATA pe acelasi interval de scor era mult mai mica - iar
    cardul nu spunea nimic cand Elliott contrazicea planul. Acum: probabilitatea
    masurata, decizia efectiva a agentului si conflictul, cu nivelul urmarit.

    PLAN DEJA URMARIT (`open_plan`): cand candidatul principal are deja un plan
    activ pe aceeasi directie, scanarea nu emite nimic nou (has_open_plan) si nu
    scrie nicio decizie. Cardul afisa totusi nivelurile si probabilitatea
    SEMNALULUI CURENT ca si cum ar fi fost planul agentului (8 oct: DASH SHORT,
    49.9% la scor 80, entry 54.945), desi planul real #293318 avea entry 54.52 si
    SL 55.88. Acum cardul arata planul urmarit, iar semnalul curent doar ca
    informatie. `recent` = calibrarea pe ultimele 12 luni (recent_calibration)."""
    if not best or not deep:
        return '<p class="dim">Niciun candidat cu semnal clar in scanarea curenta.</p>'
    plan = deep["plan"]
    direction_cls = "long" if best["direction"] == "LONG" else "short"
    b = _bucket(best.get("risk_adjusted") or best.get("score"))
    cal = (calibration or {}).get(b) or {}
    if open_plan and not decision:
        return _render_tracked(best, plan, open_plan, calibration or {}, b, cal, direction_cls, ew_stats, recent)
    rn = _recent_note(recent, b)
    if cal.get("reliable"):
        conf_html = (f'<span class="confidence-value">{cal["win_rate"]}%</span>'
                     f'<div class="dim conf-note">masurat pe {cal["total"]} planuri cu scor {b}-{int(b)+19} '
                     f'(IC {cal["ci_low"]}-{cal["ci_high"]}%, tot istoricul) &middot; formula din scor: '
                     f'{best["probability"]}%' + (f'<br>{rn}' if rn else "") + '</div>')
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
    {_r_line(plan.get("expected_r"), cal)}
    {_ew_stats_html(ew_stats)}
    '''


def _ew_stats_html(ew_stats):
    if not ew_stats:
        return ""
    return (f'<div class="dim conf-note">Istoric masurat: contra Elliott '
            f'{ew_stats["r_con"]:+.3f}R/plan (n={ew_stats["n_con"]}) &middot; fara conflict '
            f'{ew_stats["r_rest"]:+.3f}R/plan (n={ew_stats["n_rest"]})</div>')


def _render_tracked(best, cur, op, calibration, b_now, cal_now, direction_cls, ew_stats, recent):
    """Cardul cand candidatul principal are deja un plan activ (vezi render_plan):
    nivelurile si probabilitatea PLANULUI REAL, de la emitere; semnalul curent e
    doar informativ."""
    _, label = STATE_STYLE.get(op.get("state"), ("open", op.get("state") or "?"))
    s0 = op.get("score_at_entry")
    b0 = _bucket(s0)
    c0 = (calibration.get(b0) or {}) if s0 is not None else {}
    if c0.get("reliable"):
        rn = _recent_note(recent, b0)
        prob = (f'<span class="confidence-value">{c0["win_rate"]}%</span>'
                f'<div class="dim conf-note">la emitere: masurat pe {c0["total"]} planuri cu scor '
                f'{b0}-{int(b0)+19} (IC {c0["ci_low"]}-{c0["ci_high"]}%, tot istoricul); planul a fost '
                f'deschis la scor {s0}' + (f'<br>{rn}' if rn else "") + '</div>')
    else:
        prob = ('<span class="confidence-value">&mdash;</span>'
                '<div class="dim conf-note">fara calibrare sigura pentru scorul de la emitere</div>')
    note_state = (" Jumatate din pozitie e inchisa la TP1, iar SL-ul restului e mutat la intrare (breakeven)."
                  if op.get("state") == "TP1_HIT" else "")
    cur_txt = ""
    if cur:
        cur_p = (f'{cal_now["win_rate"]}% masurat (IC {cal_now["ci_low"]}-{cal_now["ci_high"]}%, '
                 f'n={cal_now["total"]})' if cal_now.get("reliable") else "necalibrat")
        rn_now = _recent_note(recent, b_now)
        if rn_now:
            cur_p += f"; {rn_now}"
        cur_txt = (f'<div class="dim conf-note">Semnalul curent (scor {best.get("risk_adjusted")}, interval '
                   f'{b_now}-{int(b_now)+19}: {cur_p}) ar propune entry {fmt_price(cur.get("entry"))} &middot; '
                   f'SL {fmt_price(cur.get("sl"))} &middot; TP1 {fmt_price(cur.get("tp1"))} &middot; '
                   f'TP2 {fmt_price(cur.get("tp2"))} - NU se emite: un singur plan activ pe simbol si '
                   f'directie.</div>')
    return f'''
    <div class="plan-head">
      <span class="symbol">{best["symbol"]}</span>
      <span class="badge badge-{direction_cls}">{best["direction"]}</span>
    </div>
    <div class="plan-ok"><strong>PLAN URMARIT #{op.get("id")}</strong> &middot; {label}
      &middot; emis {op.get("created_time") or "-"}.{note_state}
      Nivelurile de mai jos sunt ale planului urmarit, nu ale semnalului curent.</div>
    <div class="confidence-row">
      <span class="dim">PROBABILITATE</span>
      <div>{prob}</div>
    </div>
    <div class="plan-grid">
      <div><span class="dim">ENTRY</span><br>{fmt_price(op.get("entry"))}</div>
      <div><span class="dim">{"SL INITIAL" if op.get("state") == "TP1_HIT" else "SL"}</span><br class="sl">{fmt_price(op.get("sl"))}</div>
      <div><span class="dim">TP1</span><br>{fmt_price(op.get("tp1"))}</div>
      <div><span class="dim">TP2</span><br>{fmt_price(op.get("tp2"))}</div>
    </div>
    {_r_line(_r_tp2(op), c0)}
    {cur_txt}
    {_ew_stats_html(ew_stats)}
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
        if p["state"] == "SL_HIT" and p.get("tp1_hit_ts") is not None:
            label = SL_AFTER_TP1_LABEL
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
    """Probabilitatea MASURATA pe intervale de scor, nu formula. Sub fiecare rata pe
    tot istoricul apare rata aceluiasi interval pe ultimele 12 luni, cu verdictul fata
    de perioada anterioara (core.plans.calibration_by_period) - edge-ul s-a erodat,
    iar media 2018-azi singura supraestima probabilitatea de acum."""
    cal = (store or {}).get("calibration") or {}
    if not cal:
        return '<p class="dim">Se calibreaza dupa primele planuri inchise.</p>'
    rec = recent_calibration(store)
    rows = []
    for b in sorted(cal, key=int):
        e = cal[b]
        badge = "reliable" if e["reliable"] else "thin"
        txt = (f'{e["win_rate"]}% <span class="dim">(IC {e["ci_low"]}-{e["ci_high"]}%)</span>'
               if e["reliable"] else f'<span class="dim">n={e["total"]}, prea putine date</span>')
        rn = _recent_note(rec, b)
        if rn:
            txt += f'<br><span class="dim">{rn}</span>'
        rows.append(f'''<div class="cal-row cal-{badge}">
      <span>scor {b}-{int(b)+19}</span><span>{txt}</span>
      <span class="dim">{e["avg_r"]:+.2f}R</span></div>''')
    note = (f'<p class="dim" style="margin:6px 0 0;">Rata principala si R-ul din dreapta sunt pe tot istoricul '
            f'(backtest 2018-azi + live); randul al doilea e acelasi interval pe ultimele {rec["days"]} de zile '
            f'({rec.get("since")} - {rec.get("until")}), comparat cu perioada de dinainte.</p>' if rec else "")
    return '<div class="cal-list">' + "".join(rows) + "</div>" + note
