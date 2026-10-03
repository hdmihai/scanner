# -*- coding: utf-8 -*-
"""dashboard.build - Punctul de intrare: citeste datele, construieste pagina, scrie docs/index.html."""

import os


from dashboard.config import AGENT_MODEL_FILE, BRIEFING_FILE, CHART_FILE, DETAILS_FILE, DOCS_DIR, EXCHANGES_FILE, EXCHANGE_SCANS_FILE, HISTORY_FILE, OUTPUT_FILE, PLANS_FILE, TOKEN_METADATA_FILE, TOKEN_META_UPDATED, WEIGHTS_FILE, WEIGHTS_HISTORY_FILE
from dashboard.page import build_html
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
                      exchange_scans)
    # scriere atomica: o pagina trunchiata nu ajunge niciodata publicata
    tmp = OUTPUT_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(html)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, OUTPUT_FILE)
    print(f"Dashboard generat: {OUTPUT_FILE}")
