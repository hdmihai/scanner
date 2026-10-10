# -*- coding: utf-8 -*-
"""dashboard.sections.agent - Cardul agentului AI si curba de invatare."""



from dashboard.components import compute_hit_rate_curve, render_line_chart_svg


def render_agent_card(agent_state, audit=None):
    if not agent_state or not agent_state.get("agent", {}).get("total"):
        return ('<p class="dim">Agentul nu s-a antrenat inca &mdash; ruleaza ai_agent.py '
                'dupa prima scanare cu rezultate evaluate.</p>')

    a = agent_state["agent"]
    status = agent_state.get("status", "SHADOW")
    reason = agent_state.get("status_reason", "")
    ba = agent_state.get("balanced_agent")
    bb = agent_state.get("balanced_baseline")
    status_cls = "ok" if status == "ACTIVE" else "warn"

    dir_rows = ""
    for d in ("LONG", "SHORT"):
        s = (agent_state.get("by_direction") or {}).get(d, {})
        if s.get("total"):
            dir_rows += (
                f'<div class="liq-row"><span>{d}</span>'
                f'<span>agent {100*s["agent"]/s["total"]:.0f}%</span>'
                f'<span class="dim">eur. {100*s["baseline"]/s["total"]:.0f}%</span></div>'
            )

    weights = (agent_state.get("model") or {}).get("weights", {})
    w_rows = "".join(
        f'<div class="fib-row"><span>{k}</span><span>{v:+.2f}</span></div>'
        for k, v in weights.items()
    )

    # "euristica" = min(50 + 0.35*scor, 88)%, mereu >= 50% -> prezice mereu castig,
    # deci acuratetea ei e doar rata de castig. Pragul care conteaza e clasa
    # majoritara (mereu pierdere); ordonarea se vede in AUC fata de scorul brut.
    maj = agent_state.get("majority_baseline")
    auc_a, auc_s = agent_state.get("auc"), agent_state.get("auc_score_baseline")
    bal_line = ((f'{ba:.1f}% vs euristica {bb:.1f}% (euristica prezice mereu castig)'
                 + (f' &middot; prag real, mereu pierdere: {maj:.1f}%' if maj is not None else ""))
                if ba is not None else "in curs de acumulare")
    if auc_a is not None and auc_s is not None:
        bal_line += (f'<br><span class="dim">ordonare AUC pe evaluarea dominata de backtest: agent {auc_a:.3f} '
                     f'vs scor brut {auc_s:.3f} (0.5 = hazard)</span>')
    # ORDONAREA LIVE: probabilitatea data de agent la momentul deciziei fata de
    # rezultatul real. Statusul ACTIVE cere confirmarea ei, nu doar backtest-ul.
    lv = agent_state.get("live") or {}
    if lv.get("auc") is not None:
        conf_live = lv.get("ci_low") is not None and lv["ci_low"] > 0.5 and lv.get("n", 0) >= 100
        bal_line += (f'<br><strong>LIVE</strong>: AUC {lv["auc"]:.3f} (IC95 {lv.get("ci_low")}-{lv.get("ci_high")}) '
                     f'pe {lv["n"]} planuri live &middot; R mediu {lv.get("avg_r", 0):+.3f}R &middot; '
                     + ("ordonare confirmata live" if conf_live else
                        "neconfirmata inca: filtrul cere 100 de planuri live si IC peste 0.5"))
    elif lv.get("n") is not None:
        bal_line += f'<br><strong>LIVE</strong>: {lv["n"]} planuri live evaluate - prea putine pentru AUC'
    ex = agent_state.get("skew_excluded") or []
    if ex:
        sk = agent_state.get("skew") or {}
        bal_line += ('<br><span class="dim">excluse din model (nu exista la fel in backtest): '
                     + ", ".join(f'{k}{" (" + str(sk[k][0]) + "% live / " + str(sk[k][1]) + "% backtest)" if k in sk else ""}'
                                 for k in ex) + '</span>')
    pend = agent_state.get("pending_features")
    if pend is None:
        pend = ["ev_analyst"] if "ev_analyst" in ((agent_state.get("model") or {}).get("weights") or {}) else []
    if pend:
        names = {"ev_analyst": "ev_analyst (verdictul Analistului Web3)"}
        bal_line += ('<br><span class="dim">in asteptarea backtest-ului (afisate pe planuri, greutate 0 pana cand '
                     'backtest-ul re-rulat le contine; apoi agentul se reantreneaza pe tot istoricul): '
                     + ", ".join(names.get(k, k) for k in pend) + '</span>')

    if agent_state.get("learning_rate") is not None:
        bal_line += (f'<br><span class="dim">ritm de invatare {agent_state["learning_rate"]} dupa {a["total"]} exemple '
                     '(scade cu memoria: 0.02/sqrt(1+n/1000), minim 0.001) &middot; evaluare '
                     + ("cauzala: predictia de la crearea planului" if agent_state.get("eval_version") else
                        "in ordinea inchiderii (umflata)") + '</span>')
    au = audit or {}
    if au.get("when"):
        rb = au.get("rebuild") or {}
        bal_line += ('<br><span class="dim">audit memorie: ' + f'{au.get("checked", 0)} planuri verificate, '
                     f'{len(au.get("removed") or [])} scoase pentru date false'
                     + (f' &middot; BLOCAT: {au["blocked"]}' if au.get("blocked") else "")
                     + (f' &middot; memorie reconstruita {rb.get("when")}: {rb.get("removed_total")} planuri vechi '
                        f'scoase, {rb.get("backtest_new")} de backtest regenerate' if rb else "")
                     + '</span>')

    return f'''
    <div class="agent-status agent-{status_cls}">
      <strong>{status}</strong> &middot; {reason}
    </div>
    <div class="agent-metrics">
      <div><span class="dim">Exemple invatate</span><br>{a["total"]}</div>
      <div><span class="dim">Zile acoperite</span><br>{agent_state.get("days_covered", 0)}</div>
    </div>
    <p class="dim" style="margin:12px 0 6px;">Acuratete echilibrata (media LONG/SHORT)</p>
    <div class="agent-balanced">{bal_line}</div>
    <div class="liq-list" style="margin-top:10px;">{dir_rows}</div>
    <p class="dim" style="margin:14px 0 6px;">Greutati invatate din date</p>
    <div class="fib-list">{w_rows}</div>
    '''


def render_learning_curve(history, weights_history, health, agent_state=None):
    hit_curve = compute_hit_rate_curve(history)
    hit_svg = render_line_chart_svg({"hit_rate": hit_curve}, y_min=0, y_max=100)

    comps = ["trend", "momentum", "volatility", "volume"]
    w_series = {c: [w.get(c) for w in weights_history] for c in comps}
    w_svg = render_line_chart_svg(w_series, y_min=0.3, y_max=2.0) if len(weights_history) >= 2 else (
        '<div class="chart-empty">Se acumuleaza de-abia de acum - revino peste cateva zile.</div>'
    )

    legend = "".join(
        f'<span><i class="dot" style="background:{c}"></i>{n}</span>'
        for n, c in zip(comps, ["var(--ema20)", "var(--bull)", "var(--amber)", "var(--bear)"])
    )

    agent_block = ""
    curve = (agent_state or {}).get("curve") or []
    if len(curve) >= 2:
        agent_svg = render_line_chart_svg({
            "agent": [p["agent"] for p in curve],
            "baseline": [p["baseline"] for p in curve],
        }, y_min=0, y_max=100)
        agent_block = f'''
    <div class="lc-block">
      <h3>Agent AI vs euristica <span class="dim">(acuratete cumulativa)</span></h3>
      {agent_svg}
      <div class="legend">
        <span><i class="dot" style="background:var(--ema20)"></i>agent AI</span>
        <span><i class="dot" style="background:var(--bull)"></i>euristica</span>
      </div>
    </div>'''

    return f'''
    <div class="lc-block">
      <h3>Hit-rate cumulativ <span class="dim">({health["evaluated"]} semnale evaluate)</span></h3>
      {hit_svg}
    </div>
    <div class="lc-block">
      <h3>Evolutia ponderilor adaptive</h3>
      {w_svg}
      <div class="legend">{legend}</div>
    </div>{agent_block}
    '''
