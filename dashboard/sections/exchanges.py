# -*- coding: utf-8 -*-
"""dashboard.sections.exchanges - Tab-urile pe burse."""



from dashboard.config import CAP_LABELS
from dashboard.components import fmt_price


def render_exchange_scan(scan, label):
    """Ce vede o bursa ACUM: lista ei de simboluri, semnalele si indicatorii.

    Planurile se creeaza doar pe bursa activa - vezi nota din scanner. Aici e
    strict afisare, ca sa poti compara ce arata fiecare bursa fara sa imparti
    memoria agentului in cinci.
    """
    if not scan:
        return '<p class="dim">Nicio scanare inregistrata pentru aceasta bursa.</p>'
    if scan.get("error"):
        return ('<div class="ex-fail">Scanarea nu a putut rula.</div>'
                '<div class="ex-err">{}</div>'.format(scan["error"]))

    res = scan.get("results") or []
    resolved = scan.get("resolved") or []
    missing = scan.get("missing") or []
    head = ('<div class="scan-head"><strong>{}</strong> simboluri gasite aici'
            '{} &middot; <strong>{}</strong> cu semnal{}</div>').format(
        len(resolved),
        (' &middot; <span class="dim">{} lipsesc: {}</span>'.format(
            len(missing), ", ".join(missing[:8]) + ("..." if len(missing) > 8 else "")))
        if missing else "",
        len(res),
        ' <span class="tab-used">activa - aici se creeaza planuri</span>'
        if scan.get("primary") else
        ' <span class="dim">(doar afisare)</span>')

    if scan.get("truncated"):
        head += ('<div class="ex-note">Scanare trunchiata la limita de timp - '
                 'bursele secundare au buget fix ca sa nu intinda rularea.</div>')
    if not res:
        return head + '<p class="dim">Niciun semnal pe aceasta bursa acum.</p>'

    det = scan.get("details") or {}
    rows = []
    for r in res[:20]:
        d = det.get(r["symbol"]) or {}
        st = d.get("supertrend")
        rows.append(
            '<div class="scan-row">'
            '<span class="scan-sym">{}</span>'
            '<span class="badge badge-{}">{}</span>'
            '<span class="scan-score">{}</span>'
            '<span class="scan-px">{}</span>'
            '<span class="tag tag-{}">{}</span>'
            '<span class="dim scan-pos">{}</span>'
            '</div>'.format(
                r["symbol"], "bull" if r["direction"] == "LONG" else "bear",
                r["direction"], r["score"], fmt_price(r["price"]),
                "bull" if st == "BULLISH" else ("bear" if st == "BEARISH" else "info"),
                st or "-", d.get("position") or ""))
    return head + '<div class="scan-list">' + "".join(rows) + "</div>"


def render_exchange_tabs(store, scans_store=None):
    """Tab-uri per bursa, cu starea fiecareia.

    Fara JavaScript: radio ascuns + label, cu selectorul :checked din CSS. Merge
    in orice browser si pe telefon, si nu depinde de niciun CDN.
    """
    cards = (store or {}).get("exchanges") or []
    if not cards:
        return '<p class="dim">Bursele apar dupa prima scanare.</p>'

    active_sig = (store or {}).get("active_signature")
    used = (store or {}).get("used")

    tabs, panels = [], []
    for i, c in enumerate(cards):
        eid = c.get("id", f"ex{i}")
        ok = c.get("connected")
        checked = " checked" if i == 0 else ""
        dot = "ok" if ok else "bad"
        badge = ' <span class="tab-used">activa</span>' if eid == used else ""
        tabs.append(
            '<input type="radio" name="extab" id="tab-{0}" class="tab-radio"{1}>'
            '<label for="tab-{0}" class="tab-label"><i class="dot-{2}"></i>{3}{4}</label>'.format(
                eid, checked, dot, c.get("label", eid), badge))

        if not ok:
            body = ('<div class="ex-fail">Nu m-am putut conecta.</div>'
                    '<div class="ex-err">{}</div>'.format(c.get("error") or "motiv necunoscut"))
        else:
            avail = set(c.get("available") or [])
            rows = []
            for cap in c.get("declared") or []:
                has = cap in avail
                rows.append(
                    '<div class="cap-row"><span class="tag tag-{}">{}</span>'
                    '<span>{}</span></div>'.format(
                        "bull" if has else "bear",
                        "disponibil" if has else "indisponibil",
                        CAP_LABELS.get(cap, cap)))
            missing = [CAP_LABELS.get(cap, cap) for cap in c.get("declared") or []
                       if cap not in avail]
            note = ""
            if missing:
                note = ('<div class="ex-note">Evidentele care depind de: {} '
                        'sunt OMISE pentru aceasta bursa - nu inlocuite cu valori '
                        'neutre, care ar minti modelul despre ce a vazut.</div>').format(
                            ", ".join(missing))
            body = ('<div class="ex-meta"><div><span class="dim">PIETE</span><br>{}</div>'
                    '<div><span class="dim">VERIFICAT</span><br>{}</div></div>'
                    '<div class="cap-list">{}</div>{}').format(
                        c.get("markets", 0), c.get("checked_at", "-"), "".join(rows), note)
        # scanul bursei, sub fisa de capabilitati
        scan = ((scans_store or {}).get("scans") or {}).get(eid)
        body += ('<h4 class="scan-h">Ce vede aceasta bursa acum</h4>'
                 + render_exchange_scan(scan, c.get("label", eid)))
        panels.append('<div class="tab-panel tab-panel-{}">{}</div>'.format(eid, body))
    # Regula CSS care leaga fiecare radio de panoul lui. Generata dinamic, ca
    # sa nu depinda de o lista fixa de burse.
    rules = "".join(
        "#tab-{0}:checked ~ .tab-panel-{0}{{display:block;}}".format(c.get("id", f"ex{i}"))
        for i, c in enumerate(cards))
    panels.append("<style>" + rules + "</style>")

    sig = ""
    if active_sig:
        # Textul vechi ("se invata separat") descria comportamentul de dinainte de
        # familia de geometrie. Acum planurile cu capabilitati diferite se invata
        # IMPREUNA; evidentele indisponibile sunt doar omise din vector.
        sig = ('<div class="ex-sig">Semnatura activa: <strong>{}</strong> '
               '&middot; planurile cu capabilitati diferite se invata impreuna, pe '
               'aceeasi familie de geometrie; evidentele indisponibile sunt omise, '
               'nu inlocuite</div>').format(active_sig)
    return '<div class="tabs">' + "".join(tabs) + "".join(panels) + "</div>" + sig
