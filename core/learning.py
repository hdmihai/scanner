# -*- coding: utf-8 -*-
"""core.learning - invatarea din scanari: persistenta semnalelor intre scanari si ajustarea
ponderilor dupa rezultatul semnalelor vechi (lookahead).
"""

import time


def compute_persistence_and_age(history, symbol, direction, now_ts):
    """Cate scanari recente consecutive au avut acelasi symbol+directie si
    de cand (in minute) e activa aceasta directie pentru acest simbol."""
    streak = 0
    first_ts = now_ts
    for scan in reversed(history):
        match = next((r for r in scan["results"] if r["symbol"] == symbol), None)
        if match and match["direction"] == direction:
            streak += 1
            first_ts = scan["scan_id_ts"]
        else:
            break
    age_minutes = round((now_ts - first_ts) / 60)
    return streak, age_minutes


def evaluate_and_learn(history, weights, tickers, lookahead_hours, hit_threshold_atr):
    """'Invatare' simpla: verifica semnalele mai vechi decat lookahead_hours,
    vede daca pretul s-a miscat in directia prezisa, si ajusteaza usor
    ponderile componentelor care au dat rezultate bune/proaste."""
    now_ts = time.time()
    lookahead_sec = lookahead_hours * 3600
    feedback = {k: [] for k in weights}

    for scan in history:
        if scan.get("evaluated") or now_ts - scan["scan_id_ts"] < lookahead_sec:
            continue
        for r in scan["results"]:
            price_now = tickers.get(r["symbol"], {}).get("last")
            if price_now is None:
                continue
            move = (price_now - r["price"]) if r["direction"] == "LONG" else (r["price"] - price_now)
            hit = move >= r["atr"] * hit_threshold_atr
            r["outcome"] = "hit" if hit else "miss"
            for comp, val in r["components"].items():
                if val > 0.6:
                    feedback[comp].append(1 if hit else -1)
        scan["evaluated"] = True

    for comp, fb in feedback.items():
        if not fb:
            continue
        avg = sum(fb) / len(fb)
        weights[comp] = round(max(0.3, min(2.0, weights[comp] * (1 + avg * 0.05))), 4)

    return weights
