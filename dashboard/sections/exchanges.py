# -*- coding: utf-8 -*-
"""dashboard.sections.exchanges - Modulele per bursa: rezumatul de pe pagina principala si navigarea.

Fiecare bursa are pagina ei (docs/exchanges/<id>.html, construita de
dashboard.exchange_page). Pagina principala pastreaza nucleul - starea
sistemului, altseason, agentul, planurile - plus cate un rezumat per bursa.
"""

import html as _html

from dashboard.config import CAP_LABELS
from dashboard.sections.tokens import decision_label


def page_href(eid, from_exchange_page=False):
    """Legatura catre pagina unei burse, relativa la pagina curenta."""
    return f"{eid}.html" if from_exchange_page else f"exchanges/{eid}.html"


def render_nav(cards, current=None, from_exchange_page=False):
    """Navigarea comuna: pagina principala + cate o pagina per bursa."""
    home = "../index.html" if from_exchange_page else "index.html"
    items = [f'<a class="nav-i{" nav-on" if current is None else ""}" href="{home}">Principal</a>']
    for c in cards or []:
        eid = c.get("id")
        cls = "nav-i" + (" nav-on" if eid == current else "") + ("" if c.get("connected") else " nav-off")
        items.append(f'<a class="{cls}" href="{page_href(eid, from_exchange_page)}">'
                     f'{_html.escape(c.get("label", eid))}</a>')
    return '<nav class="nav">' + "".join(items) + "</nav>"


def render_caps(card):
    """Capabilitatile declarate ale bursei si care functioneaza efectiv acum."""
    avail = set(card.get("available") or [])
    rows = []
    for cap in card.get("declared") or []:
        has = cap in avail
        rows.append('<span class="cap-chip cap-{}">{}</span>'.format(
            "ok" if has else "no", CAP_LABELS.get(cap, cap)))
    missing = [CAP_LABELS.get(cap, cap) for cap in card.get("declared") or [] if cap not in avail]
    note = ""
    if missing and card.get("connected"):
        note = ('<p class="dim exm-note">Evidentele care depind de: {} sunt omise pe aceasta '
                'bursa - nu inlocuite cu valori neutre, care ar minti modelul.</p>').format(
                    ", ".join(missing))
    return '<div class="cap-chips">' + "".join(rows) + "</div>" + note


def proposal_counts(props, trained):
    """'16 propuneri: 15 emise, 1 asteapta corectia' (sau la conditional)."""
    if not props:
        return "nicio propunere"
    by = {}
    for p in props.values():
        _cls, txt = decision_label(p, trained)
        by[txt] = by.get(txt, 0) + 1
    parts = ", ".join(f"{n} {t}" for t, n in sorted(by.items(), key=lambda kv: -kv[1]))
    return f"{len(props)} propuneri: {parts}"


def render_exchange_modules(store, scans_store=None, proposals_by_ex=None):
    """Cate un modul per bursa pe pagina principala: rolul (invata / neantrenat /
    indisponibila), acoperirea, semnalele, propunerile si legatura spre pagina ei."""
    cards = (store or {}).get("exchanges") or []
    if not cards:
        return '<p class="dim">Bursele apar dupa prima scanare.</p>'
    scans = (scans_store or {}).get("scans") or {}
    primary = (scans_store or {}).get("primary") or (store or {}).get("used")
    out = []
    for c in cards:
        eid = c.get("id")
        label = _html.escape(c.get("label", eid))
        sc = scans.get(eid) or {}
        if not c.get("connected"):
            out.append(f'''<div class="exm exm-off">
      <div class="exm-head"><i class="dot-bad"></i><strong>{label}</strong>
        <span class="tag tag-bear">indisponibila</span></div>
      <p class="exm-err">{_html.escape(str(c.get("error") or "motiv necunoscut"))[:200]}</p>
    </div>''')
            continue
        trained = eid == primary
        role = ('<span class="tag tag-learn">aici invata agentul</span>' if trained else
                '<span class="tag tag-shadow">neantrenat</span>')
        res = sc.get("results") or []
        nl = sum(1 for r in res if r.get("direction") == "LONG")
        props = ((proposals_by_ex or {}).get(eid) or {}).get("proposals") or {}
        top = sorted(res, key=lambda r: -(r.get("score") or 0))[:3]
        chips = "".join(
            '<span class="exm-sig"><b>{}</b> <span class="badge badge-{}">{}</span> {}</span>'.format(
                _html.escape(str(r["symbol"]).split("/")[0]),
                "long" if r["direction"] == "LONG" else "short", r["direction"], r.get("score"))
            for r in top)
        miss = sc.get("missing") or []
        meta = []
        if miss:
            meta.append("lipsesc: " + ", ".join(miss))
        if sc.get("duration_s"):
            meta.append(f"analiza {sc['duration_s']}s")
        if sc.get("truncated"):
            meta.append("trunchiata la limita de timp")
        err = (f'<p class="exm-err">Analiza de acum a esuat: {_html.escape(str(sc["error"]))[:180]}</p>'
               if sc.get("error") else "")
        out.append(f'''<div class="exm{" exm-learn" if trained else ""}">
      <div class="exm-head"><i class="dot-ok"></i><strong>{label}</strong>{role}
        <a class="exm-link" href="{page_href(eid)}">Deschide modulul {label}</a></div>
      <div class="exm-stats">{len(sc.get("resolved") or [])} token-uri &middot; {len(res)} cu semnal
        ({nl} long, {len(res) - nl} short) &middot; {proposal_counts(props, trained)}</div>
      <div class="exm-top">{chips}</div>
      {err}<p class="dim exm-meta">{" &middot; ".join(meta)}</p>
    </div>''')
    return ('<div class="exm-grid">' + "".join(out) + "</div>"
            '<p class="dim exm-foot">Fiecare bursa e analizata complet, cu acelasi nucleu. Agentul '
            'invata doar de pe bursa activa: acelasi token are practic acelasi pret peste tot, iar '
            'invatarea din mai multe burse ar numara fiecare rezultat de mai multe ori.</p>')
