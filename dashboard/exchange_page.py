# -*- coding: utf-8 -*-
"""dashboard.exchange_page - Pagina unei burse (docs/exchanges/<id>.html): modulul ei complet.

Aceeasi structura pentru orice bursa: altseason sus (rezumatul modulului
separat, afisat mereu; analiza completa e pe pagina principala), starea bursei si rolul ei, propunerile agentului, apoi lista celor 30
de token-uri - pliata implicit - cu graficul detaliat si analiza completa a
fiecaruia. Bursa activa si celelalte difera doar prin eticheta: pe cea activa
agentul deschide planuri si invata; pe celelalte decizia e simulata.
"""

import html as _html

from dashboard.components import fmt_price
from dashboard.sections.exchanges import proposal_counts, render_caps, render_nav
from dashboard.sections.tokens import decision_label, render_token_details, token_anchor


def _role(card, trained, learn_label):
    if not card.get("connected"):
        note = card.get("note") or ""
        return ('<div class="ex-role ex-role-off"><strong>Indisponibila.</strong> '
                f'{_html.escape(str(card.get("error") or "motiv necunoscut"))[:220]}'
                + (f'<br><span class="dim">{_html.escape(note)}</span>' if note else "") + "</div>")
    if trained:
        return ('<div class="ex-role ex-role-learn"><strong>Bursa activa.</strong> Aici agentul '
                'deschide planuri si invata din rezultatele lor.</div>')
    return ('<div class="ex-role"><strong>Analiza completa, decizie simulata.</strong> Acelasi nucleu ca '
            f'pe {learn_label}: grafic, Elliott, structura, lichidari, plan si decizia agentului. '
            'Agentul nu invata de aici: acelasi token are practic acelasi pret pe toate bursele, iar '
            'invatarea din mai multe burse ar numara fiecare rezultat de mai multe ori.</div>')


def _proposal_rows(props, trained):
    rows = []
    for sym, p in sorted(props.items(), key=lambda kv: -(kv[1].get("score") or 0)):
        cls, txt = decision_label(p, trained)
        dec = p.get("decision") or {}
        cal = dec.get("calibrated_prob")
        agp = dec.get("agent_prob")
        lv = p.get("levels") or {}
        rows.append(
            '<a class="prop-row" href="#{}"><span class="prop-sym">{}</span>'
            '<span class="badge badge-{}">{}</span><span class="prop-score">{}</span>'
            '<span class="tag tag-{}">{}</span><span class="prop-num">{}</span>'
            '<span class="prop-num dim">{}</span></a>'.format(
                token_anchor(sym), _html.escape(sym),
                "long" if p.get("direction") == "LONG" else "short", p.get("direction"),
                p.get("score"), cls, txt,
                f"{cal}% masurat" if cal is not None else ("plan deschis" if p.get("open_plan") else "-"),
                (f"intrare {fmt_price(lv['entry'])}" if lv.get("entry") is not None else
                 (f"agent {agp * 100:.0f}%" if isinstance(agp, (int, float)) else ""))))
    return "".join(rows)


def build_exchange_page(card, cards, scan, details, proposals_store, plans_store, altseason_html,
                        css, primary, primary_label, scan_time):
    """HTML-ul complet al paginii unei burse."""
    eid = card.get("id")
    label = _html.escape(card.get("label", eid))
    trained = eid == primary
    props = (proposals_store or {}).get("proposals") or {}
    symbols = (details or {}).get("symbols") or {}
    scan = scan or {}
    res = scan.get("results") or []
    resolved = scan.get("resolved") or []
    missing = scan.get("missing") or []
    watchlist = sorted({s_.split("/")[0] for s_ in resolved} | set(missing))
    det_time = (details or {}).get("scan_time")

    banner = ""
    if card.get("connected") and scan.get("error"):
        banner = ('<div class="plan-conflict">Analiza de acum a esuat: {}. {}</div>'.format(
            _html.escape(str(scan["error"]))[:200],
            f"Datele de mai jos sunt din {det_time}." if det_time else "Nu exista inca date."))
    elif card.get("connected") and not symbols and not trained:
        banner = ('<div class="plan-conflict explore">Analiza completa a acestei burse apare dupa '
                  'prima scanare cu modulele per bursa (in cel mult o ora).</div>')
    elif card.get("connected") and scan.get("truncated"):
        banner = ('<div class="plan-conflict explore">Analiza a fost trunchiata la limita de timp a '
                  'bursei - lista de mai jos e partiala.</div>')

    stats = ""
    if card.get("connected"):
        nl = sum(1 for r in res if r.get("direction") == "LONG")
        cells = [
            ("TOKEN-URI", f"{len(resolved)}" + (f' <span class="dim">lipsesc: {", ".join(missing)}</span>' if missing else "")),
            ("CU SEMNAL", f"{len(res)} <span class=\"dim\">({nl} long, {len(res) - nl} short)</span>"),
            ("PROPUNERI", proposal_counts(props, trained)),
            ("DATE DIN", f"{det_time or '-'}" + (f' <span class="dim">analiza {scan["duration_s"]}s</span>'
                                                 if scan.get("duration_s") else "")),
        ]
        stats = '<div class="ex-stats">' + "".join(
            f'<div><span class="dim">{k}</span><br>{v}</div>' for k, v in cells) + "</div>"

    prop_card = ""
    if props:
        # sufixul se calculeaza separat: ghilimele simple in expresia unui f-string cu
        # ghilimele simple sunt valide doar din Python 3.12 (workflow-ul ruleaza 3.11)
        simulated = "" if trained else ' <span class="dim">(simulate, neantrenat)</span>'
        prop_card = f'''<div class="card">
    <h2>Propunerile agentului &middot; {label}{simulated}</h2>
    <div class="prop-list">{_proposal_rows(props, trained)}</div>
  </div>'''

    tokens_card = ""
    if card.get("connected"):
        tok_html = render_token_details(details, plans_store, watchlist, props, trained, primary_label,
                                        proposals_ready=bool((proposals_store or {}).get("scan_time")))
        tokens_card = f'''<div class="card">
    <h2>Token-uri &middot; {label}</h2>
    <details class="tok-list">
      <summary>Lista celor {len(watchlist)} token-uri &middot; {len(symbols)} cu analiza completa</summary>
      <div class="tok-list-body">{tok_html}</div>
    </details>
  </div>'''

    return f'''<!doctype html>
<html lang="ro">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{label} &middot; SCANLINE</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>{css}</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="brand">SCANLINE<small>Modulul bursei {label}</small></div>
    <div class="meta">{scan_time}<br>{(details or {}).get("timeframe") or ""}</div>
  </header>
  {render_nav(cards, eid, True)}

  <div class="card">
    <h2>Altcoin season &middot; modul separat, acelasi pe toate bursele</h2>
    {altseason_html}
  </div>

  <div class="card">
    <h2>{label} &middot; starea bursei</h2>
    {_role(card, trained, primary_label)}
    {banner}
    {stats}
    {render_caps(card)}
  </div>

  {prop_card}
  {tokens_card}

  <footer>
    Generat automat de crypto_ai_scanner.py + generate_dashboard.py, prin GitHub Actions.
    Scorurile si planurile sunt euristici proprii, nu recomandari financiare &mdash;
    verifica intotdeauna pe cont propriu inainte de orice decizie de trading.
  </footer>
</div>
</body>
</html>'''
