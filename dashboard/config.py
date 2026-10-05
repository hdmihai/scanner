# -*- coding: utf-8 -*-
"""dashboard.config - Constante si cai catre fisierele de date. TOKEN_META_UPDATED e un dict partajat (mutat, nu reatribuit)."""

import os


DATA_DIR = "data"
DOCS_DIR = "docs"
HISTORY_FILE = os.path.join(DATA_DIR, "scan_history.json")
WEIGHTS_FILE = os.path.join(DATA_DIR, "weights.json")
WEIGHTS_HISTORY_FILE = os.path.join(DATA_DIR, "weights_history.json")
AGENT_MODEL_FILE = os.path.join(DATA_DIR, "agent_model.json")
PLANS_FILE = os.path.join(DATA_DIR, "plans.json")
BRIEFING_FILE = os.path.join(DATA_DIR, "briefing.json")
DETAILS_FILE = os.path.join(DATA_DIR, "latest_details.json")
EXCHANGES_FILE = os.path.join(DATA_DIR, "exchanges.json")
EXCHANGE_SCANS_FILE = os.path.join(DATA_DIR, "exchange_scans.json")
ALTSEASON_FILE = os.path.join(DATA_DIR, "altseason.json")
ALTSEASON_HISTORY_FILE = os.path.join(DATA_DIR, "altseason_history.json")
CHART_FILE = os.path.join(DATA_DIR, "latest_chart.json")
OUTPUT_FILE = os.path.join(DOCS_DIR, "index.html")
# Modulele per bursa: datele scrise de scaner si paginile generate.
EXCHANGES_DATA_DIR = os.path.join(DATA_DIR, "exchanges")
EXCHANGE_PAGES_DIR = os.path.join(DOCS_DIR, "exchanges")
MIN_SAMPLES_FOR_VALIDATION = 100
TOKEN_METADATA_FILE = os.path.join(DATA_DIR, "token_metadata.json")
TOKEN_META_UPDATED = {}
STATE_STYLE = {
    "OPEN": ("open", "OPEN &middot; WAITING"),
    "TP1_HIT": ("tp1", "TP1 HIT &middot; RUNNING"),
    "TP2_HIT": ("tp2", "TP2 HIT &middot; CLOSED"),
    "SL_HIT": ("sl", "SL &middot; INVALIDATED"),
    "EXPIRED": ("exp", "EXPIRED"),
}
CAP_LABELS = {
    "ohlcv": "Lumanari istorice",
    "orderbook_live": "Adancime live",
    "trades_live": "Flux de tranzactii live",
    "orderbook_history": "Order book istoric",
    "open_interest": "Open interest",
}
