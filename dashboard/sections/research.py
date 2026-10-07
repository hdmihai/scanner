# -*- coding: utf-8 -*-
"""dashboard.sections.research - Cercetarea autonoma: ce a testat agentul, ce a acceptat si de ce."""

import html as _html

STATE = {"shadow": ("tag-shadow", "SHADOW - masurata live, nu filtreaza"),
         "activa": ("tag-learn", "ACTIVA - filtreaza semnalele"),
         "retrasa": ("tag-info", "RETRASA")}


def _num(v, fmt="{:+.3f}"):
    return "-" if v is None else fmt.format(v)


def render_research(state):
    """Cardul cercetarii: ultima investigatie, regulile si cele mai apropiate respinse."""
    if not state or not state.get("report"):
        return ('<p class="dim">Prima investigatie ruleaza la urmatorul auto-diagnostic: cauta reguli care '
                'readuc edge-ul, testate pe date nevazute.</p>')
    rep = state["report"]
    if rep.get("status") != "ok":
        return f'<p class="dim">Investigatia nu a rulat: {_html.escape(str(rep.get("status")))}.</p>'
    hd, sr = rep.get("holdout") or {}, rep.get("search") or {}
    out = [f'<p class="res-head">Ultima investigatie: <strong>{_html.escape(str(state.get("last_run")))}</strong> '
           f'({_html.escape(str(state.get("trigger") or ""))}) &middot; {rep["n_rules"]} reguli testate pe '
           f'{rep["n_plans"]} planuri de backtest &middot; <strong>{len(rep.get("accepted") or [])} acceptate</strong></p>',
           f'<p class="dim res-note">Cautare: {sr.get("n")} planuri, {_num(sr.get("r"))}R/plan. Perioada rezervata '
           f'(ultimele {hd.get("days")} zile, nefolosita la cautare): {hd.get("n")} planuri, {_num(hd.get("r"))}R/plan. '
           f'O regula trece doar daca planurile excluse pierd in cautare (prag Bonferroni p &lt; {rep.get("alpha")}), '
           f'in 3 din 4 perioade si pe perioada rezervata.</p>']
    rules = sorted((state.get("rules") or {}).values(), key=lambda r: ("activa", "shadow", "retrasa").index(r.get("state", "retrasa")))
    if rules:
        rows = []
        for r in rules:
            cls, lbl = STATE.get(r.get("state"), ("tag-info", r.get("state")))
            lv = r.get("live") or {}
            live_txt = (f'live: {lv.get("n_match")} potrivite {_num(lv.get("r_match"))}R vs restul {_num(lv.get("r_rest"))}R'
                        if lv else "live: in asteptare")
            extra = f' &middot; {_html.escape(r.get("retired_reason", ""))}' if r.get("state") == "retrasa" else ""
            rows.append(f'<div class="res-rule"><span class="tag {cls}">{lbl}</span> <strong>{_html.escape(r["text"])}</strong>'
                        f'<div class="dim res-note">din {r.get("since")} &middot; {live_txt}{extra}</div></div>')
        out.append('<h4 class="scan-h">Reguli</h4>' + "".join(rows))
    else:
        out.append('<div class="plan-ok">Nicio regula nu a trecut toate conditiile. Erodarea edge-ului nu vine dintr-o '
                   'regula care s-ar putea repara cu un filtru: vine din piata. Agentul nu schimba nimic.</div>')
    top = rep.get("rejected_top") or []
    if top:
        rows = "".join(
            f'<tr><td>{_html.escape(t["text"])}</td><td>{t["n_ex"]}</td><td>{_num(t.get("r_ex"))}</td>'
            f'<td>{_num(t.get("r_ex_holdout"))}</td><td class="dim">{_html.escape(t["reason"])}</td></tr>' for t in top[:6])
        out.append('<h4 class="scan-h">Cele mai apropiate de prag (respinse)</h4><div class="as-table-wrap"><table class="as-table">'
                   '<tr><th>Regula</th><th>Excluse</th><th>R exclus</th><th>R rezervat</th><th>Motiv</th></tr>'
                   f'{rows}</table></div>')
    return "".join(out)
