# -*- coding: utf-8 -*-
"""dashboard.sections.altseason - Faza ciclului altcoin season si contextul istoric pe 10 ani."""

import math




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
