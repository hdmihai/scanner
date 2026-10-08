# -*- coding: utf-8 -*-
"""dashboard.page - Pagina: sablonul HTML. CSS-ul e in dashboard/styles/main.css; fiecare sectiune e izolata."""

import os

import chart_render
import html as _html

from dashboard.config import ALTSEASON_FILE, ALTSEASON_HISTORY_FILE, DATA_DIR, TOKEN_META_UPDATED
from dashboard.components import load_json, render_weight_bars
from dashboard.sections.agent import render_agent_card, render_learning_curve
from dashboard.sections.altseason import render_altseason
from dashboard.sections.exchanges import page_href, render_exchange_modules, render_nav
from dashboard.sections.market import render_briefing, render_evidence, render_indicators, render_levels, render_liquidity, render_opportunity_rows
from dashboard.sections.plan import (elliott_outcome_stats, recent_calibration, render_calibration, render_plan,
                                     render_plan_memory, tracked_plan)
from dashboard.sections.health import render_health
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
    similar_html = _safe("render_similar_projects", render_similar_projects, 
        token_meta, narrative, symbol=(best or {}).get("symbol"),
        updated=(TOKEN_META_UPDATED or {}).get("when"))
    learning_curve_html = _safe("render_learning_curve", render_learning_curve, history, weights_history, health, agent_state)
    agent_html = _safe("render_agent_card", render_agent_card, agent_state)
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
                        load_json(os.path.join(DATA_DIR, "runs.json"), None))
    chart_svg = chart_render.render_components(chart, "main")
    weight_bars = _safe("render_weight_bars", render_weight_bars, weights)
    long_rows = _safe("render_opportunity_rows", render_opportunity_rows, scan.get("top_long", []))
    short_rows = _safe("render_opportunity_rows", render_opportunity_rows, scan.get("top_short", []))
    scan_time = scan.get("scan_time", "-")
    universe = scan.get("universe_size", 0)
    sessions_txt = ", ".join(session["active"])

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

  <div class="card">
    <h2>Starea sistemului</h2>
    {health_html}
  </div>

  <div class="card" id="altseason">
    <h2>Altcoin season &middot; faza ciclului (date reale de piata)</h2>
    {altseason_html}
  </div>

  <div class="card">
    <h2>Burse &middot; <span class="dim">cate un modul per bursa</span></h2>
    {exchanges_html}
  </div>

  {briefing_html}

  <div class="grid">
    <div>
      <div class="card">
        <h2>Grafic &middot; {(best or {}).get("symbol", "-")}</h2>
        {chart_svg}
        <div class="legend">
          <span><i class="dot" style="background:#26A69A"></i>EMA 9</span>
          <span><i class="dot" style="background:#F57C00"></i>EMA 20</span>
          <span><i class="dot" style="background:#E53935"></i>EMA 50</span>
          <span><i class="dot" style="background:#1565C0"></i>EMA 200</span>
          <span><i class="dot" style="background:#7E57C2"></i>Elliott</span>
          <span><i class="dot" style="background:#089981"></i>TP</span>
          <span><i class="dot" style="background:#F23645"></i>SL / E INV</span>
          <span><i class="dot" style="background:#0288D1"></i>entry</span>
        </div>
      </div>

      <div class="card">
        <h2>Indicatori &middot; VWAP, Volume Profile, SuperTrend, MACD</h2>
        {indicators_html}
      </div>

      <div class="card">
        <h2>Market structure &middot; Fibonacci</h2>
        {levels_html}
      </div>

      <div class="card">
        <h2>Liquidity levels &middot; order book</h2>
        {liquidity_html}
      </div>

      <div class="card">
        <h2>Detalii per token</h2>
        {tokens_html}
      </div>

      <div class="card">
        <h2>Top long <span class="dim">* Prob = formula din scor, nu masuratoare - vezi cardul de calibrare</span></h2>
        <table><tr><th>Symbol</th><th>Score</th><th title="formula, nu masuratoare">Prob*</th><th>Pers</th></tr>{long_rows}</table>
      </div>
      <div class="card">
        <h2>Top short <span class="dim">* idem</span></h2>
        <table><tr><th>Symbol</th><th>Score</th><th title="formula, nu masuratoare">Prob*</th><th>Pers</th></tr>{short_rows}</table>
      </div>
    </div>

    <div>
      <div class="card">
        <h2>AI plan &middot; best candidate</h2>
        {plan_html}
      </div>

      <div class="card">
        <h2>Auto-diagnostic &middot; auto-reparare agent</h2>
        {selfcheck_html}
      </div>

      <div class="card">
        <h2>Cercetare autonoma &middot; reguli testate pe date nevazute</h2>
        {research_html}
      </div>

      <div class="card">
        <h2>Similar projects</h2>
        {similar_html}
      </div>

      <div class="card">
        <h2>Adaptive weights &middot; model health</h2>
        {weight_bars}
        <div class="health-row">
          <span class="dim">{health["evaluated"]}/{health["min_samples"]} evaluated &middot; hit-rate {health["hit_rate"] if health["hit_rate"] is not None else "-"}%</span>
          <span class="health-status">{health["status"]}</span>
        </div>
      </div>

      <div class="card">
        <h2>Evidente &middot; <span class="dim">{evidence_symbol}, din memoria agentului</span></h2>
        {evidence_html}
      </div>

      <div class="card">
        <h2>Autonomous plan memory</h2>
        {plans_html}
      </div>

      <div class="card">
        <h2>Calibrare &middot; probabilitate masurata</h2>
        {calibration_html}
      </div>

      <div class="card">
        <h2>Agent AI &middot; invatare online</h2>
        {agent_html}
      </div>

      <div class="card">
        <h2>Learning curve &middot; progresul agentului</h2>
        {learning_curve_html}
      </div>

      <div class="card">
        <h2>Sessions</h2>
        <div class="sessions">
          <div><span class="dim">UTC now</span>{session["utc_time"]}</div>
          <div><span class="dim">Active</span>{sessions_txt}</div>
        </div>
      </div>
    </div>
  </div>

  <footer>
    Generat automat de crypto_ai_scanner.py + generate_dashboard.py, prin GitHub Actions.
    Scorurile si planul AI sunt euristici proprii, nu recomandari financiare &mdash;
    verifica intotdeauna pe cont propriu inainte de orice decizie de trading.
  </footer>
</div>
</body>
</html>'''
