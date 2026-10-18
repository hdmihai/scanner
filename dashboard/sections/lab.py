# -*- coding: utf-8 -*-
"""dashboard.sections.lab - LABORATORUL AGENTULUI (core/lab.py, research_lab.py): ce a invatat agentul din
depozitul de memorie, cu ce dovada, si ce alege la scanarea curenta (data/lab_live.json).

Raportul vine din data/memory_status.json -> "lab" (jobul de memorie, o data pe zi); alegerile curente din
data/lab_live.json (scanarea orara). Starea decide cum se prezinta alegerile: doar un model ACTIV
(confirmat live) recomanda; in CERCETARE si SHADOW alegerile sunt observatii masurate, nu recomandari."""

import html as _html

from core import lab as L

STATE_TAG = {"CERCETARE": ("tag-info", "CERCETARE"), "SHADOW": ("tag-info", "SHADOW - IN PROBA LIVE"),
             "ACTIV": ("tag-bull", "ACTIV - CONFIRMAT LIVE"), "RETRAS": ("tag-bear", "RETRAS")}
STATE_MEANS = {
    "CERCETARE": ("Niciun model nu are inca avantaj dovedit in afara esantionului. Agentul continua sa caute la "
                  "fiecare antrenare zilnica; alegerile de mai jos sunt doar masurate, nu recomandate."),
    "SHADOW": ("Modelul a trecut walk-forward-ul si perioada rezervata. Fiecare model publicat e testat apoi "
               "INAINTE, pe toate monedele bursei, pe barele de dupa publicarea lui; devine recomandare doar daca "
               "acest test live confirma."),
    "ACTIV": "Modelul e confirmat live: alegerile de mai jos sunt recomandarile agentului.",
    "RETRAS": "Live-ul a infirmat modelul: alegerile lui nu se mai recomanda pana la o noua dovada.",
}


def _e(x):
    return _html.escape(str(x))


def _r(st):
    if not st or st.get("mean") is None:
        return "n/d"
    ci = f" (IC95 {st['lo']:+.3f}..{st['hi']:+.3f})" if st.get("lo") is not None else ""
    return f"{st['mean']:+.3f}R{ci}"


def _row(name, st, base=None):
    st = st or {}
    return (f'<tr><td>{_e(name)}</td><td>{st.get("n", 0):,}</td><td>{_r(st)}</td>'
            f'<td>{_r(base) if base else "-"}</td></tr>')


def _fmt(v):
    if v is None:
        return "-"
    a = abs(v)
    return f"{v:,.2f}" if a >= 100 else f"{v:.4f}" if a >= 1 else f"{v:.6g}"


def render_lab(rep, live_doc):
    """Sectiunea laboratorului. `rep` = raportul din memory_status.json, `live_doc` = data/lab_live.json."""
    if not rep:
        return ('<p class="mem-note">Laboratorul nu a rulat inca: prima antrenare se face la urmatoarea rulare a '
                'jobului <strong>Memorie agent</strong> (Actions &rarr; Memorie agent; automat la 6 ore, antrenare '
                'o data pe zi). Pana atunci scanarea salveaza deja instantaneele din care va invata.</p>'
                + _live_html(None, live_doc))
    if rep.get("error"):
        return f'<p class="plan-conflict">Laboratorul a esuat: {_e(rep["error"])}</p>' + _live_html(rep, live_doc)
    state = rep.get("state") or "CERCETARE"
    cls, label = STATE_TAG.get(state, ("tag-info", state))
    tr, dat = rep.get("trade") or {}, rep.get("data") or {}
    head = (f'<div class="lab-head"><span class="tag {cls}">{_e(label)}</span> '
            f'<span>{_e(rep.get("reason") or "")}</span></div>'
            f'<p class="mem-note" style="margin-top:6px;">{_e(STATE_MEANS.get(state, ""))}</p>')
    what = (f'<p class="mem-note"><strong>Ce invata:</strong> pe fiecare bara de {_e(tr.get("bar", "4h"))} a '
            f'{dat.get("symbols", 0)} monede lichide ({_e(dat.get("from", "?"))} &ndash; {_e(dat.get("to", "?"))}, '
            f'{dat.get("examples", 0):,} exemple, plus {dat.get("live_train", 0):,} instantanee live etichetate), '
            f'cat R ar fi facut o tranzactie LONG si una SHORT: intrare la deschiderea barei urmatoare, SL la '
            f'{tr.get("sl_atr", L.SL_ATR)} ATR, TP la {tr.get("tp_r", L.TP_R)}R, inchidere dupa cel mult '
            f'{tr.get("max_hold", L.MAX_HOLD)} bare, comisioane {100 * tr.get("cost_frac", L.COST_FRAC) / 2:.1f}% pe parte, '
            f'orice ambiguitate din bara rezolvata impotriva tranzactiei. Alegerea agentului: cel mult '
            f'{tr.get("top_k", L.TOP_K)} tranzactii pe bara, cu EV peste cuantila {int(100 * tr.get("select_q", L.SELECT_Q))}% '
            f'a predictiilor de antrenare. Model ales: <strong>{_e(rep.get("chosen"))}</strong>. '
            f'Antrenat {_e(rep.get("when", "-"))} in {rep.get("seconds", "?")} s.</p>')
    wf = rep.get("walk_forward") or {}
    ch = wf.get(rep.get("chosen")) or {}
    rows = [_row(f"an {y}", s) for y, s in sorted((ch.get("years") or {}).items())]
    rows.append(_row("walk-forward, total", ch, rep.get("baseline_wf")))
    for k, v in wf.items():
        if k != rep.get("chosen"):
            rows.append(_row(f"(model {k}, total)", v))
    ho = rep.get("holdout") or {}
    rows.append(_row(f"perioada rezervata {ho.get('from', '')} – {ho.get('to', '')}", ho, ho.get("baseline")))
    lv = rep.get("live") or {}
    fw = lv.get("forward") or {}
    rows.append(_row(f"test inainte, tot universul bursei{(' din ' + fw['from']) if fw.get('from') else ''}"
                     f"{(' (' + str(fw['pending']) + ' in curs)') if fw.get('pending') else ''}", fw))
    rows.append(_row("alegeri din scanari (30 de monede), etichetate", lv.get("picks"), lv.get("baseline")))
    table = ('<div class="as-table-wrap"><table class="mem-table lab-table"><tr><th>Dovada</th><th>Alegeri</th>'
             '<th>R mediu pe alegere</th><th>Toate tranzactiile posibile</th></tr>' + "".join(rows) + '</table></div>'
             '<p class="mem-note">IC95 prin bootstrap pe blocuri de timp (saptamani; zile pentru live). Coloana din '
             'dreapta e referinta: ce ar da o alegere la intamplare din aceleasi bare. Fiecare an e testat cu un '
             'model antrenat doar pe anii dinainte; perioada rezervata e folosita o singura data.</p>')
    return head + what + table + _learned_html(rep.get("learned")) + _live_html(rep, live_doc) + _history_html(rep.get("history"))


def _learned_html(learned):
    if not learned:
        return ""
    parts = []
    for d in ("LONG", "SHORT"):
        rows = (learned or {}).get(d) or []
        if not rows:
            continue
        items = "".join(
            f'<li>{_e(r["text"])}: de la {r["q10"]:.3g} la {r["q90"]:.3g} &rarr; EV {r["effect"]:+.3f}R'
            + (f' <span class="dim">(importanta {100 * r["share"]:.0f}%)</span>' if r.get("share") is not None else "")
            + '</li>' for r in rows[:6])
        parts.append(f'<div><h4 class="scan-h">{d}: ce schimba cel mai mult EV-ul estimat</h4><ul class="lab-list">{items}</ul></div>')
    return ('<div class="lab-learned">' + "".join(parts) + '</div>'
            '<p class="mem-note">Efectul = cat se schimba EV-ul mediu estimat cand caracteristica trece de la cuantila '
            '10% la 90%, restul ramanand ca in date. E ce a invatat modelul, nu o garantie: valoarea lui e doar cea '
            'din tabelul de dovezi.</p>')


def _live_html(rep, doc):
    doc = doc or {}
    if doc.get("error") and not doc.get("latest"):
        return f'<p class="mem-note">Scanarea live a laboratorului: {_e(doc["error"])}</p>'
    latest = doc.get("latest") or []
    state = (rep or {}).get("state") or doc.get("state") or "FARA_MODEL"
    if not latest:
        n = len(doc.get("snapshots") or [])
        return (f'<p class="mem-note">Instantanee salvate de scanare (ultimele 2 zile): {n}. Alegerile apar dupa '
                f'prima antrenare a laboratorului.</p>' if doc else "")
    title = {"ACTIV": "Recomandarile agentului la bara curenta",
             "SHADOW": "Alegerile modelului la bara curenta (in proba live - nu recomandari)"}.get(
        state, "Ce ar alege modelul la bara curenta (masurat, nu recomandat)")
    rows = "".join(
        f'<tr class="{"lab-pick" if r.get("pick") else ""}"><td>{_e(r["symbol"])}</td>'
        f'<td class="{"bull" if r["dir"] == "LONG" else "bear"}">{_e(r["dir"])}</td>'
        f'<td>{r["ev"]:+.3f}R</td><td>{_fmt(r.get("entry"))}</td><td>{_fmt(r.get("sl"))}</td>'
        f'<td>{_fmt(r.get("tp"))}</td><td>{"ALES" if r.get("pick") else ("nelichid" if not r.get("liquid") else "-")}</td></tr>'
        for r in latest[:10])
    return (f'<h4 class="scan-h">{_e(title)} &middot; <span class="dim">{_e(doc.get("when", "-"))}, '
            f'{doc.get("scored", 0)} monede evaluate</span></h4>'
            '<div class="as-table-wrap"><table class="mem-table lab-table"><tr><th>Moneda</th><th>Directie</th>'
            '<th>EV estimat</th><th>Intrare*</th><th>SL</th><th>TP</th><th>Alegere</th></tr>' + rows + '</table></div>'
            '<p class="mem-note">* pretul de inchidere al barei; intrarea reala e la deschiderea barei urmatoare. '
            'Toate aceste instantanee (si cele neale) se eticheteaza dupa ce se incheie si intra in antrenarea '
            'urmatoare - asa invata agentul din fiecare scanare.</p>')


def _signed(v):
    return "n/d" if v is None else f"{v:+.3f}R"


def _history_html(hist):
    if not hist:
        return ""
    rows = "".join(
        f'<tr><td>{_e(h.get("version"))}</td><td>{_e(h.get("state"))}</td><td>{_e(h.get("chosen"))}</td>'
        f'<td>{_signed(h.get("wf"))}</td><td>{_signed(h.get("holdout"))}</td>'
        f'<td>{h.get("live_n", 0)}</td></tr>'
        for h in list(hist)[-8:][::-1])
    return ('<details class="cc"><summary><span class="cc-title">Istoricul antrenarilor</span></summary><div class="cc-body">'
            '<div class="as-table-wrap"><table class="mem-table"><tr><th>Versiune</th><th>Stare</th><th>Model</th>'
            '<th>Walk-forward</th><th>Rezervat</th><th>Alegeri live</th></tr>' + rows + '</table></div></div></details>')


def summary(rep, live_doc):
    """Cele 3 randuri vizibile cand sectiunea e pliata."""
    if not rep:
        n = len((live_doc or {}).get("snapshots") or [])
        return ["Laboratorul nu a rulat inca (prima antrenare la urmatorul job de memorie)",
                f"Instantanee live salvate: {n}"]
    if rep.get("error"):
        return [f"Laborator: eroare - {rep['error']}"]
    ch = (rep.get("walk_forward") or {}).get(rep.get("chosen")) or {}
    ho = rep.get("holdout") or {}
    lv = (rep.get("live") or {}).get("forward") or {}
    return [f"Stare: {rep.get('state')} - {rep.get('reason', '')}",
            f"Walk-forward {_r(ch)}/alegere, n={ch.get('n', 0):,} · rezervat {_r(ho)}",
            f"Test inainte (live): {lv.get('n', 0)} alegeri incheiate {('· ' + _r(lv)) if lv.get('n') else ''}".strip()]
