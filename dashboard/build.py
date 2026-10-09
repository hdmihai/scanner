# -*- coding: utf-8 -*-
"""dashboard.build - Punctul de intrare: citeste datele, construieste pagina, scrie docs/index.html."""

import os


from dashboard.config import ALTSEASON_FILE, DATA_DIR, EXCHANGES_DATA_DIR, EXCHANGE_PAGES_DIR
from dashboard.config import AGENT_MODEL_FILE, BRIEFING_FILE, CHART_FILE, DETAILS_FILE, DOCS_DIR, EXCHANGES_FILE, EXCHANGE_SCANS_FILE, HISTORY_FILE, OUTPUT_FILE, PLANS_FILE, TOKEN_METADATA_FILE, TOKEN_META_UPDATED, WEIGHTS_FILE, WEIGHTS_HISTORY_FILE
from dashboard.page import _safe, build_html, css
from dashboard.exchange_page import build_exchange_page
from dashboard.sections.altseason import render_altseason_strip
from dashboard.components import compute_model_health, get_session_info, load_json


def main():
    history = load_json(HISTORY_FILE, [])
    weights = load_json(WEIGHTS_FILE, {"trend": 1.0, "momentum": 1.0, "volatility": 1.0, "volume": 1.0})
    chart = load_json(CHART_FILE, None)
    token_metadata = load_json(TOKEN_METADATA_FILE, {})
    weights_history = load_json(WEIGHTS_HISTORY_FILE, [])
    agent_state = load_json(AGENT_MODEL_FILE, {})
    plans_store = load_json(PLANS_FILE, {})
    briefing = load_json(BRIEFING_FILE, {})
    details = load_json(DETAILS_FILE, {})
    exchanges_store = load_json(EXCHANGES_FILE, {})
    exchange_scans = load_json(EXCHANGE_SCANS_FILE, {})
    cards = (exchanges_store or {}).get("exchanges") or []
    primary = (exchange_scans or {}).get("primary") or (exchanges_store or {}).get("used")
    ex_proposals = {c.get("id"): load_json(os.path.join(EXCHANGES_DATA_DIR, c.get("id", ""), "proposals.json"), {})
                    for c in cards}

    scan = history[-1] if history else {"scan_time": "-", "universe_size": 0, "top_long": [], "top_short": []}
    best = scan.get("best_candidate")
    deep = scan.get("deep_analysis")
    health = compute_model_health(history)
    session = get_session_info()

    token_meta = None
    narrative = token_metadata.get("narrative")
    if best and token_metadata.get("tokens"):
        token_meta = token_metadata["tokens"].get(best["symbol"])
        if narrative and narrative.get("symbol") != best["symbol"]:
            narrative = None  # narativul e vechi, pt alt candidat - nu-l arat ca fiind curent
    TOKEN_META_UPDATED["when"] = (token_metadata or {}).get("_updated")

    os.makedirs(DOCS_DIR, exist_ok=True)
    html = build_html(scan, best, deep, chart, health, weights, session, token_meta, narrative, history, weights_history, agent_state, plans_store, briefing, details, exchanges_store,
                      exchange_scans, ex_proposals)
    # scriere atomica: o pagina trunchiata nu ajunge niciodata publicata
    tmp = OUTPUT_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(html)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, OUTPUT_FILE)
    print(f"Dashboard generat: {OUTPUT_FILE}")
    write_exchange_pages(cards, primary, exchange_scans, details, ex_proposals, plans_store,
                         scan.get("scan_time", "-"))


def _write_atomic(path, html):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(html)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def write_exchange_pages(cards, primary, exchange_scans, primary_details, ex_proposals, plans_store, scan_time):
    """Cate o pagina per bursa: docs/exchanges/<id>.html. Fiecare pagina e izolata - una
    care esueaza nu le opreste pe celelalte si nici pagina principala."""
    if not cards:
        return
    import exchanges as ex_mod                      # notele adaptoarelor (ex. de ce lipseste Bybit)
    scans = (exchange_scans or {}).get("scans") or {}
    page_css = css()
    alt_html = _safe("render_altseason_strip", render_altseason_strip, load_json(ALTSEASON_FILE, None))
    primary_label = next((c.get("label") for c in cards if c.get("id") == primary), primary or "bursa activa")
    # pasii 1-4 ai analistului per token (data/analyst.json), pe cardul fiecarui token
    analyst_tokens = (load_json(os.path.join(DATA_DIR, "analyst.json"), {}) or {}).get("tokens")
    for c in cards:
        eid = c.get("id")
        card = {**c, "note": ex_mod.adapter(eid).note}
        if eid == primary:
            details = primary_details
        else:
            details = load_json(os.path.join(EXCHANGES_DATA_DIR, eid, "details.json"), {})
        html = _safe(f"pagina {eid}", build_exchange_page, card, cards, scans.get(eid), details,
                     ex_proposals.get(eid), plans_store, alt_html, page_css, primary, primary_label,
                     scan_time, analyst_tokens)
        if not html.lstrip().startswith("<!doctype"):
            # pagina a esuat: o pagina minimala cu eroarea, nu una lipsa
            html = (f'<!doctype html><html lang="ro"><head><meta charset="utf-8"><title>{eid}</title>'
                    f'<style>{page_css}</style></head><body><div class="wrap">{html}</div></body></html>')
        _write_atomic(os.path.join(EXCHANGE_PAGES_DIR, f"{eid}.html"), html)
    print(f"Pagini per bursa: {len(cards)} in {EXCHANGE_PAGES_DIR}/")
