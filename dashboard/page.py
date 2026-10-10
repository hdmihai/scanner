# -*- coding: utf-8 -*-
"""dashboard.page - Pagina: sablonul HTML. CSS-ul e in dashboard/styles/main.css; fiecare sectiune e izolata."""

import os

import chart_render
import html as _html

from dashboard.config import ALTSEASON_FILE, ALTSEASON_HISTORY_FILE, DATA_DIR, TOKEN_META_UPDATED
from dashboard import summaries as SUM
from dashboard.components import FOLD_JS, FOLD_TOOLS, fold, load_json, render_weight_bars
from dashboard.sections.agent import render_agent_card, render_learning_curve
from dashboard.sections.altseason import render_altseason
from dashboard.sections.analyst import render_analyst, render_token_steps
from dashboard.sections.tokens import token_anchor
from dashboard.sections.exchanges import page_href, render_exchange_modules, render_nav
from dashboard.sections.market import render_briefing, render_evidence, render_indicators, render_levels, render_liquidity, render_opportunity_rows
from dashboard.sections.plan import (elliott_outcome_stats, recent_calibration, render_calibration, render_plan,
                                     render_plan_memory, tracked_plan)
from dashboard.sections.health import health_items, render_health
from dashboard.sections.research import render_research
from dashboard.sections.status import render_self_check, render_similar_projects

_STYLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "styles", "main.css")


def css():
    """CSS-ul complet al paginilor (refolosit si de paginile burselor)."""
    return _css()


def _css():
    """CSS-ul paginii din fisierul real (fara acolade dublate), cu CSS-ul
    graficelor inserat in acelasi loc in care il punea sablonul vechi."""
    with open(_STYLES) as f:
        return f.read().replace("/*__CHART_CSS__*/", chart_render.CSS)


def _safe(name, fn, *a, **k):
    """IZOLAREA SECTIUNILOR: o sectiune care esueaza devine o caseta scurta cu
    eroarea, iar restul dashboard-ului se publica normal. Inainte, o exceptie in
    orice card oprea actualizarea intregii pagini."""
    try:
        return fn(*a, **k)
    except Exception as e:
        print(f"[!] sectiunea {name} a esuat: {e}")
        return (f'<p class="dim">Sectiunea nu s-a putut genera ({name}: '
                f'{_html.escape(str(e))[:160]}). Restul dashboard-ului e actualizat.</p>')


def _sum(name, fn, *a, **k):
    """Rezumatul unei sectiuni pliate; un rezumat care esueaza dispare, sectiunea ramane."""
    try:
        return fn(*a, **k) or []
    except Exception as e:
        print(f"[!] rezumatul {name} a esuat: {e}")
        return []


def build_html(scan, best, deep, chart, health, weights, session, token_meta, narrative, history, weights_history, agent_state, plans_store, briefing, details, exchanges_store,
               exchange_scans=None, ex_proposals=None):
    _dec = (load_json(os.path.join(DATA_DIR, "decisions.json"), {}).get("decisions") or {})
    # planul deja urmarit pentru candidatul principal: cand exista, scanarea nu scrie
    # nicio decizie pentru el, iar cardul trebuie sa arate planul real, nu semnalul curent
    plan_html = _safe("render_plan", render_plan, best, deep, (plans_store or {}).get("calibration"),
                            _dec.get((best or {}).get("symbol")),
                            elliott_outcome_stats(plans_store),
                            open_plan=tracked_plan(plans_store, (best or {}).get("symbol"),
                                                   (best or {}).get("direction")),
                            recent=recent_calibration(plans_store))
    levels_html = _safe("render_levels", render_levels, deep)
    liquidity_html = _safe("render_liquidity", render_liquidity, deep)
    selfcheck_html = _safe("render_self_check", render_self_check, load_json(os.path.join(DATA_DIR, "self_check.json"), None),
                                       load_json(os.path.join(DATA_DIR, "repair_log.json"), []))
    research_html = _safe("render_research", render_research, load_json(os.path.join(DATA_DIR, "research.json"), None))
    altseason_html = _safe("render_altseason", render_altseason, load_json(ALTSEASON_FILE, None),
                                      load_json(ALTSEASON_HISTORY_FILE, []))
    # ANALISTUL: raportul de piata si pasii 1-4 per token, cu legaturi spre cardul fiecarui token
    # de pe pagina bursei active (unde e detaliul complet si graficul cu invalidarea marcata)
    _an = load_json(os.path.join(DATA_DIR, "analyst.json"), None)
    _primary = (exchange_scans or {}).get("primary") or (exchanges_store or {}).get("used")
    _links = ({str(sym).split("/")[0].upper(): f"{page_href(_primary)}#{token_anchor(sym)}"
               for sym in ((details or {}).get("symbols") or {})} if _primary else {})
    analyst_html = _safe("render_analyst", render_analyst, _an, _links)
    _best_tok = ((_an or {}).get("tokens") or {}).get(str((best or {}).get("symbol") or "").split("/")[0].upper())
    main_steps_html = _safe("render_token_steps", render_token_steps, _best_tok)
    similar_html = _safe("render_similar_projects", render_similar_projects, 
        token_meta, narrative, symbol=(best or {}).get("symbol"),
        updated=(TOKEN_META_UPDATED or {}).get("when"))
    learning_curve_html = _safe("render_learning_curve", render_learning_curve, history, weights_history, health, agent_state)
    _audit = load_json(os.path.join(DATA_DIR, "plan_audit.json"), None)
    _policy = load_json(os.path.join(DATA_DIR, "scoring_policy.json"), None)
    agent_html = _safe("render_agent_card", render_agent_card, agent_state, _audit)
    plans_html = _safe("render_plan_memory", render_plan_memory, plans_store)
    _all = (plans_store or {}).get("plans") or []
    _latest = max(_all, key=lambda p: p.get("id", 0)) if _all else None
    evidence_html = _safe("render_evidence", render_evidence, _latest)
    exchanges_html = _safe("render_exchange_modules", render_exchange_modules, exchanges_store,
                           exchange_scans, ex_proposals)
    _cards = (exchanges_store or {}).get("exchanges") or []
    nav_html = _safe("render_nav", render_nav, _cards)
    # Detaliile per token traiesc acum in modulul fiecarei burse (docs/exchanges/).
    tokens_html = ('<p class="tok-moved">Graficele detaliate si propunerile agentului pentru fiecare token '
                   'sunt in modulul fiecarei burse: '
                   + " &middot; ".join(f'<a href="{page_href(c.get("id"))}">{c.get("label", c.get("id"))}</a>'
                                       for c in _cards if c.get("connected"))
                   + ".</p>")
    evidence_symbol = (_latest or {}).get("symbol", "-")
    calibration_html = _safe("render_calibration", render_calibration, plans_store)
    indicators_html = _safe("render_indicators", render_indicators, deep)
    briefing_html = _safe("render_briefing", render_briefing, briefing)
    health_html = _safe("render_health", render_health, history, agent_state, plans_store,
                        load_json(os.path.join(DATA_DIR, "self_check.json"), None),
                        load_json(ALTSEASON_FILE, None),
                        load_json(os.path.join(DATA_DIR, "runs.json"), None), _audit)
    _inv = ((_best_tok or {}).get("strategy") or {}).get("inv_price")
    if chart and _inv and chart.get("symbol") == (best or {}).get("symbol"):
        # invalidarea analistului (pasul 4) pe graficul principal: doar daca incape, nu intinde axa
        chart = dict(chart, marks=[{"price": _inv, "label": "INVALIDARE ANALIST", "kind": "invsoft"}])
    chart_svg = chart_render.render_components(chart, "main")
    weight_bars = _safe("render_weight_bars", render_weight_bars, weights)
    long_rows = _safe("render_opportunity_rows", render_opportunity_rows, scan.get("top_long", []))
    short_rows = _safe("render_opportunity_rows", render_opportunity_rows, scan.get("top_short", []))
    scan_time = scan.get("scan_time", "-")
    universe = scan.get("universe_size", 0)
    sessions_txt = ", ".join(session["active"])

    _runs = load_json(os.path.join(DATA_DIR, "runs.json"), None)
    _sc = load_json(os.path.join(DATA_DIR, "self_check.json"), None)
    _alt = load_json(ALTSEASON_FILE, None)
    _hw = _sum("health", lambda: SUM.health(*health_items(history, agent_state, plans_store, _sc, _alt, _runs,
                                                           _audit)))
    _open_best = tracked_plan(plans_store, (best or {}).get("symbol"), (best or {}).get("direction"))
    _sym = (best or {}).get("symbol", "-")
    legend = """<div class="legend">
          <span><i class="dot" style="background:#26A69A"></i>EMA 9</span>
          <span><i class="dot" style="background:#F57C00"></i>EMA 20</span>
          <span><i class="dot" style="background:#E53935"></i>EMA 50</span>
          <span><i class="dot" style="background:#1565C0"></i>EMA 200</span>
          <span><i class="dot" style="background:#7E57C2"></i>Elliott</span>
          <span><i class="dot" style="background:#089981"></i>TP</span>
          <span><i class="dot" style="background:#F23645"></i>SL / E INV</span>
          <span><i class="dot" style="background:#0288D1"></i>entry</span>
          <span><i class="dot" style="background:#C62828"></i>invalidare analist</span>
        </div>"""
    table_head = ('<table><tr><th>Symbol</th><th>Score</th><th title="formula, nu masuratoare">Prob*</th>'
                  '<th>Pers</th></tr>')
    top_cards = "\n\n  ".join([
        fold("Starea sistemului", health_html, _hw, anchor="stare"),
        fold("Analist Web3 &middot; rotatia capitalului si risc (la fiecare scanare)", analyst_html,
             _sum("analyst", SUM.analyst, _an), anchor="analist"),
        fold("Altcoin season &middot; faza ciclului (date reale de piata)", altseason_html,
             _sum("altseason", SUM.altseason, _alt), anchor="altseason"),
        fold('Burse &middot; <span class="dim">cate un modul per bursa</span>', exchanges_html,
             _sum("exchanges", SUM.exchanges, exchanges_store, exchange_scans), anchor="burse"),
    ] + ([fold('Briefing &middot; <span class="dim">real data vs simulated history</span>',
               briefing_html, _sum("briefing", SUM.briefing, briefing), anchor="briefing", cls="briefing-card")]
         if briefing_html else []))
    left_cards = "\n\n      ".join([
        fold(f"Grafic &middot; {_sym}", f"""{chart_svg}
        {legend}
        <details class="cc an-main"><summary><span class="cc-title">Analist &middot; pașii 1–4 &middot; {_sym}</span></summary>
          <div class="cc-body">{main_steps_html}</div></details>""",
             _sum("chart", SUM.chart, best, deep, _best_tok), anchor="grafic"),
        fold("Indicatori &middot; VWAP, Volume Profile, SuperTrend, MACD", indicators_html,
             _sum("indicators", SUM.indicators, deep)),
        fold("Market structure &middot; Fibonacci", levels_html, _sum("structure", SUM.structure, deep)),
        fold("Liquidity levels &middot; order book", liquidity_html, _sum("liquidity", SUM.liquidity, deep)),
        fold("Detalii per token", tokens_html, _sum("tokens", SUM.tokens_moved, _cards)),
        fold('Top long <span class="dim">* Prob = formula din scor, nu masuratoare - vezi cardul de calibrare</span>',
             f"{table_head}{long_rows}</table>", _sum("top_long", SUM.top, scan.get("top_long"), "long")),
        fold('Top short <span class="dim">* idem</span>', f"{table_head}{short_rows}</table>",
             _sum("top_short", SUM.top, scan.get("top_short"), "short")),
    ])
    _frozen = ('<p class="dim" style="margin:0 0 8px;">Ponderi inghetate din ' + str((_policy or {}).get("since"))
               + ' - aceleasi pentru backtest si live; ajustarea euristica de mai jos e doar diagnostic.</p>'
               if (_policy or {}).get("frozen") else "")
    weights_body = f"""{_frozen}{weight_bars}
        <div class="health-row">
          <span class="dim">{health["evaluated"]}/{health["min_samples"]} evaluated &middot; hit-rate {health["hit_rate"] if health["hit_rate"] is not None else "-"}%</span>
          <span class="health-status">{health["status"]}</span>
        </div>"""
    sessions_body = f"""<div class="sessions">
          <div><span class="dim">UTC now</span>{session["utc_time"]}</div>
          <div><span class="dim">Active</span>{sessions_txt}</div>
        </div>"""
    right_cards = "\n\n      ".join([
        fold("AI plan &middot; best candidate", plan_html,
             _sum("plan", SUM.plan, best, _dec.get((best or {}).get("symbol")), _open_best), anchor="plan"),
        fold("Auto-diagnostic &middot; auto-reparare agent", selfcheck_html, _sum("self_check", SUM.self_check, _sc)),
        fold("Cercetare autonoma &middot; reguli testate pe date nevazute", research_html,
             _sum("research", SUM.research, load_json(os.path.join(DATA_DIR, "research.json"), None))),
        fold("Similar projects", similar_html, _sum("similar", SUM.similar, token_meta, narrative, (best or {}).get("symbol"))),
        fold("Adaptive weights &middot; model health", weights_body, _sum("weights", SUM.weights, weights, health, _policy)),
        fold(f'Evidente &middot; <span class="dim">{evidence_symbol}, din memoria agentului</span>', evidence_html,
             _sum("evidence", SUM.evidence, _latest)),
        fold("Autonomous plan memory", plans_html, _sum("plan_memory", SUM.plan_memory, plans_store)),
        fold("Calibrare &middot; probabilitate masurata", calibration_html, _sum("calibration", SUM.calibration, plans_store)),
        fold("Agent AI &middot; invatare online", agent_html, _sum("agent", SUM.agent, agent_state, _audit), anchor="agent"),
        fold("Learning curve &middot; progresul agentului", learning_curve_html,
             _sum("learning", SUM.learning, agent_state, health)),
        fold("Sessions", sessions_body, _sum("sessions", SUM.sessions, session)),
    ])

    return f'''<!doctype html>
<html lang="ro">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SCANLINE &middot; AI market scanner</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>{_css()}</style>
</head>
<body>
<div class="wrap">

  <header>
    <div class="brand">SCANLINE<small>AI market scanner &middot; self-learning</small></div>
    <div class="meta">{scan_time}<br>universe {universe}</div>
  </header>
  {nav_html}
  {FOLD_TOOLS}

  {top_cards}

  <div class="grid">
    <div>
      {left_cards}
    </div>

    <div>
      {right_cards}
    </div>
  </div>

  <footer>
    Generat automat de crypto_ai_scanner.py + generate_dashboard.py, prin GitHub Actions.
    Scorurile si planul AI sunt euristici proprii, nu recomandari financiare &mdash;
    verifica intotdeauna pe cont propriu inainte de orice decizie de trading.
  </footer>
</div>
{FOLD_JS}
</body>
</html>'''
