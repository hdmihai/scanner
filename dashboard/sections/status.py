# -*- coding: utf-8 -*-
"""dashboard.sections.status - Auto-diagnosticul si proiectele similare."""





def render_self_check(diag, repairs):
    """Auto-diagnosticul si auto-repararea: ce verifica agentul singur la fiecare
    scanare, ce a gasit, ce atenuari a aplicat automat si ce reparatii de cod a
    propus - vizibil fara ca cineva sa trimita date."""
    if not diag:
        return '<p class="dim">Primul auto-diagnostic ruleaza la urmatoarea scanare.</p>'
    st = diag.get("status", "OK")
    cls = {"OK": "tag-bull", "WARN": "tag-info", "ERROR": "tag-bear"}.get(st, "tag-info")
    label = {"OK": "TOTUL COERENT", "WARN": "AVERTISMENTE", "ERROR": "ERORI DETECTATE"}.get(st, st)
    checks = diag.get("checks") or []
    bad = [c for c in checks if c["level"] != "OK"]
    good = [c for c in checks if c["level"] == "OK"]
    rows = []
    for c in bad:
        ex = "".join(f"<li>{e}</li>" for e in c.get("examples") or [])
        rows.append(f'<details class="sc-item sc-{c["level"].lower()}"><summary><span class="tag '
                    f'{"tag-bear" if c["level"] == "ERROR" else "tag-info"}">{c["level"]}</span> '
                    f'<strong>{c["title"]}</strong></summary><p>{c["detail"]}</p>'
                    + (f'<ul class="as-ul">{ex}</ul>' if ex else "") + "</details>")
    ok_list = " &middot; ".join(c["title"] for c in good)
    m = diag.get("mitigations") or {}
    q = m.get("quarantine") or {}
    qtxt = ("".join(f'<li><code>{f}</code> - {v.get("reason", "")} (din {v.get("since", "?")}, '
                    f'{v.get("clean_runs", 0)}/3 rulari curate pana la eliberare)</li>' for f, v in q.items())
            or "<li>nicio caracteristica in carantina</li>")
    mit = (f'<h4 class="scan-h">Atenuari automate active</h4><ul class="as-ul">{qtxt}'
           f'<li>Filtrul de conflict Elliott: <strong>{"ACTIV" if m.get("elliott_filter", True) else "OPRIT"}</strong>'
           f' - {m.get("elliott_filter_reason", "")}</li>'
           + (f'<li><strong>MOD DE SIGURANTA ACTIV</strong> - {m.get("safe_reason")}</li>' if m.get("safe_mode") else "")
           + "</ul>")
    rep = ""
    if repairs:
        items = []
        for r in repairs[-5:][::-1]:
            link = f' &middot; <a href="{r["pr"]}">Pull Request</a>' if r.get("pr") else ""
            items.append(f'<li><code>{r.get("when")}</code> {r.get("check")}: <strong>{r.get("outcome")}</strong>'
                         f' - {r.get("reason", "")}{link}</li>')
        rep = f'<h4 class="scan-h">Reparatii de cod propuse (ultimele 5)</h4><ul class="as-ul">{"".join(items)}</ul>'
    chips = "".join(f'<span class="sc-chip sc-{h.get("status", "OK").lower()}"></span>'
                    for h in (diag.get("history") or [])[-48:])
    return (f'<div class="sc-head"><span class="tag {cls}">{label}</span> <span class="dim">verificat la '
            f'{diag.get("when")} &middot; {len(checks)} verificari</span></div>'
            + "".join(rows)
            + (f'<p class="dim sc-ok">In regula: {ok_list}</p>' if good else "")
            + mit + rep
            + (f'<h4 class="scan-h">Istoric (ultimele {min(48, len(diag.get("history") or []))} scanari)</h4>'
               f'<div class="sc-tl">{chips}</div>' if chips else "")
            + '<p class="dim as-note">Agentul invata PONDERI din rezultate; logica de calcul o verifica '
              'aceste invariante la fiecare scanare. O eroare noua deschide automat un Issue, iar '
              'auto-repararea propune un patch validat automat - merge-ul ramane al tau.</p>')


def render_similar_projects(token_meta, narrative, symbol=None, updated=None):
    if not token_meta:
        # Mesaj actionabil, inclusiv de pe telefon - "ruleaza scriptul" nu se
        # poate face de acolo. Actiunea dedicata e .github/workflows/metadata.yml.
        who = f" pentru <strong>{symbol}</strong>" if symbol else ""
        when = f" Ultima actualizare: {updated}." if updated else ""
        return (f'<p class="dim">Fara metadata{who} inca.{when} Se completeaza automat la '
                f'urmatoarea scanare; imediat, de pe telefon: aplicatia GitHub &rarr; '
                f'Actions &rarr; <strong>Metadata proiecte (Similar projects)</strong> '
                f'&rarr; Run workflow.</p>')
    labs_badge = ' <span class="badge-labs">BINANCE LABS</span>' if token_meta.get("binance_labs") else ""
    cats = ", ".join(token_meta.get("categories", [])[:4]) or "-"
    similar = token_meta.get("similar") or []
    sim_html = "".join(
        f'<div class="sim-row"><span>{s}</span><span class="dim">{round(v * 100)}% overlap</span></div>'
        for s, v in similar
    ) or '<p class="dim">Niciun proiect similar gasit inca in universul scanat.</p>'
    narrative_html = ""
    if narrative:
        narrative_html = f'<p class="narrative">{narrative["text"]}</p>'
    return f'''<div class="cats">{cats}{labs_badge}</div>
    <div class="sim-list">{sim_html}</div>
    {narrative_html}'''
