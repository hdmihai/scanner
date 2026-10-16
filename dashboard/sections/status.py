# -*- coding: utf-8 -*-
"""dashboard.sections.status - Auto-diagnosticul si proiectele similare."""

import html as _html
import os





def _self_repair_installed():
    """Auto-repararea de cod exista doar daca modulul ei e in repo. Textul paginii
    promitea patch-uri automate si dupa ce self_repair.py fusese sters (semnalat de
    monitorul zilnic) - acum spune ce se intampla efectiv."""
    return os.path.exists("self_repair.py") or os.path.isdir("selfrepair")


VERDICT_TAG = {"dovedit": "tag-bull", "daunator": "tag-bear"}
SEV_CLS = {"mare": "sc-error", "medie": "sc-warn", "mica": "sc-mica"}


def render_audit(audit):
    """AUTO-AUDITUL (core/self_audit.py): parghiile de decizie cu dovada lor masurata acum si
    registrul de imbunatatiri pe care agentul le-a gasit singur - deschise, apoi rezolvate."""
    if not audit:
        return ""
    e_ = lambda v: _html.escape(str(v if v is not None else ""))
    lev = "".join(
        f'<li><strong>{e_(r.get("name"))}</strong> &middot; {e_(r.get("state"))} &middot; '
        f'<span class="tag {VERDICT_TAG.get(r.get("verdict"), "tag-info")}">{e_(r.get("verdict"))}</span>'
        f'<br><span class="au-ev">{e_(r.get("evidence"))}</span></li>' for r in audit.get("levers") or [])
    from core.self_audit import ordered
    items = ordered(audit.get("ledger") or {})
    op = [e for e in items if e.get("status") == "deschisa"]
    done = [e for e in items if e.get("status") == "rezolvata"][:5]
    rows = []
    for e in op:
        files = ", ".join(f"<code>{e_(f)}</code>" for f in (e.get("files") or [])[:6])
        rows.append(
            f'<details class="sc-item {SEV_CLS.get(e.get("severity"), "sc-warn")}"><summary>'
            f'<span class="tag {"tag-bear" if e.get("severity") == "mare" else "tag-info"}">{e_(e.get("severity")).upper()}</span> '
            f'<strong>{e_(e.get("title"))}</strong> <span class="au-ev">din {e_(e.get("first_seen"))}'
            + (f', redeschisa de {e.get("reopened")} ori' if e.get("reopened") else "") + '</span></summary>'
            f'<p>{e_(e.get("problem"))}</p><p><strong>Masurat:</strong> {e_(e.get("evidence"))}</p>'
            f'<p><strong>Ce trebuie schimbat:</strong> {e_(e.get("action"))}</p>'
            + (f'<p class="au-ev">Unde: {files}</p>' if files else "") + '</details>')
    res = "".join(f'<li>{e_(e.get("title"))} <span class="au-ev">- rezolvata {e_(e.get("resolved"))}</span></li>'
                  for e in done)
    err = (f'<p class="au-ev">Ultima rulare a auto-auditului a esuat: {e_(audit.get("error"))}</p>'
           if audit.get("error") else "")
    return (f'<h4 class="scan-h">Parghiile de decizie si dovada lor (auto-audit, {e_(audit.get("when"))})</h4>'
            f'<ul class="as-ul lv-list">{lev}</ul>'
            f'<h4 class="scan-h">Imbunatatiri gasite de agent ({len(op)} deschise)</h4>'
            + ("".join(rows) if rows else '<p class="dim">Nicio imbunatatire deschisa.</p>')
            + (f'<p class="au-ev">Rezolvate recent:</p><ul class="as-ul">{res}</ul>' if res else "") + err
            + '<p class="dim as-note">La fiecare scanare agentul isi verifica propriul cod (cifre scrise de mana, '
              'intervale de incredere naive), urma planurilor live, dovada fiecarei parghii de decizie si textele '
              'care descriu starea lui. O imbunatatire mare noua deschide un Issue; monitorizarea zilnica '
              'scrie corectura de cod, iar aplicarea ramane a ta.</p>')


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
            + render_audit(diag.get("audit"))
            + mit + rep
            + (f'<h4 class="scan-h">Istoric (ultimele {min(48, len(diag.get("history") or []))} scanari)</h4>'
               f'<div class="sc-tl">{chips}</div>' if chips else "")
            + '<p class="dim as-note">Agentul invata PONDERI din rezultate; logica de calcul o verifica '
              'aceste invariante la fiecare scanare. O eroare noua deschide automat un Issue, iar '
              + ('auto-repararea propune un patch validat automat - merge-ul ramane al tau.</p>'
                 if _self_repair_installed() else
                 'atenuarile de mai sus se aplica automat; repararea codului e manuala (modulul de '
                 'auto-reparare nu e instalat).</p>'))


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
