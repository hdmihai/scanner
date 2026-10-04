# -*- coding: utf-8 -*-
"""dashboard.sections.health - Starea sistemului: cadenta scanarilor, agentul, divergenta live-backtest, auto-diagnosticul si prospetimea datelor, intr-un singur loc."""

import time
from datetime import datetime, timezone

EXPECTED_SCANS_24H = 24


def _scan_times(history):
    out = []
    for h in history or []:
        try:
            out.append(datetime.strptime(h.get("scan_time", ""), "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc).timestamp())
        except (ValueError, TypeError):
            pass
    return sorted(out)


def _tile(level, title, value, detail):
    cls = {"OK": "hl-ok", "WARN": "hl-warn", "ERROR": "hl-err"}.get(level, "hl-warn")
    return (f'<div class="hl-tile {cls}"><span class="hl-t">{title}</span><strong>{value}</strong>'
            f'<span class="hl-d">{detail}</span></div>')


def render_health(history, agent_state, plans_store, self_check, altseason, runs=None):
    """STAREA SISTEMULUI. Problemele gasite pana acum doar prin verificari manuale
    - scanari care rulau la 4-7 ore in loc de orar, auto-diagnosticul dezactivat
    fara nicio eroare vizibila, agentul neconfirmat live - apar aici primele."""
    now = time.time()
    tiles, worst = [], "OK"
    rank = {"OK": 0, "WARN": 1, "ERROR": 2}

    def add(level, *a):
        nonlocal worst
        tiles.append(_tile(level, *a))
        worst = level if rank[level] > rank[worst] else worst

    ts = _scan_times(history)
    n24 = sum(1 for t in ts if now - t <= 86400)
    age = (now - ts[-1]) / 60 if ts else None
    gaps = sorted(b - a for a, b in zip(ts[-25:], ts[-24:])) if len(ts) > 2 else []
    med = gaps[len(gaps) // 2] / 60 if gaps else None
    lvl = "OK" if n24 >= 18 and (age or 0) <= 90 else ("WARN" if n24 >= 8 else "ERROR")
    add(lvl, "Scanari 24h", f"{n24} / {EXPECTED_SCANS_24H}",
        (f"ultima acum {age:.0f} min" if age is not None else "nicio scanare")
        + (f" &middot; interval median {med:.0f} min" if med else "")
        + ("" if lvl == "OK" else " &middot; verifica Actions &rarr; Heartbeat scanare"))
    rr = [r for r in (runs or []) if now - (r.get("ts") or 0) <= 86400]
    if rr:
        bad = [r for r in rr if r.get("status") != "success"]
        last = bad[-1] if bad else None
        add("OK" if not bad else ("ERROR" if len(bad) * 2 >= len(rr) else "WARN"), "Rulari scanare 24h",
            f"{len(rr) - len(bad)} reusite / {len(bad)} esuate",
            (f'ultimul esec {last["when"]}' + (f' la pasul <strong>{last["failed"]}</strong>' if last.get("failed") else "")
             + f' &middot; <a href="{last["url"]}">deschide rularea</a>') if last else "toate rularile au reusit")
    a = agent_state or {}
    lv = a.get("live") or {}
    conf = lv.get("n", 0) >= 100 and (lv.get("ci_low") or 0) > 0.5
    add("OK" if a.get("status") == "ACTIVE" or not lv else ("OK" if conf else "WARN"),
        "Agent AI", a.get("status") or "n/d",
        (f"confirmare live {lv.get('n', 0)}/100 planuri"
         + (f" &middot; AUC live {lv['auc']:.3f} (IC {lv.get('ci_low')}-{lv.get('ci_high')})" if lv.get("auc") is not None else ""))
        if lv else "fara date live inca")
    dv = ((plans_store or {}).get("summary") or {}).get("divergence") or {}
    if dv.get("status") in ("sub_backtest", "in_marja", "peste_backtest"):
        add("ERROR" if dv["status"] == "sub_backtest" else "OK", "Live vs backtest",
            f"{dv['diff']:+.3f}R/plan",
            f"IC95 {dv['ci_low']:+.3f}..{dv['ci_high']:+.3f} pe {dv['n_live']} planuri &middot; "
            + {"sub_backtest": "SEMNIFICATIV sub backtest", "in_marja": "in marja statistica",
               "peste_backtest": "peste backtest"}[dv["status"]])
    sc = self_check or {}
    if sc:
        bad = [c["title"] for c in sc.get("checks") or [] if c.get("level") != "OK"]
        add(sc.get("status") if sc.get("status") in rank else "WARN", "Auto-diagnostic", sc.get("status", "n/d"),
            (f"{sc.get('when')} &middot; " + "; ".join(bad[:2])) if bad else f"{sc.get('when')} &middot; toate verificarile OK")
    else:
        add("WARN", "Auto-diagnostic", "lipsa", "data/self_check.json nu exista - pasul de diagnostic nu ruleaza")
    al = altseason or {}
    if al:
        stale = al.get("stale") or now - (al.get("ts") or 0) > 6 * 3600
        add("WARN" if stale else "OK", "Date altseason", "vechi" if stale else "la zi",
            f"{al.get('when')}" + (f" &middot; {al.get('last_error')}" if stale and al.get("last_error") else ""))
    head = {"OK": "TOTUL FUNCTIONEAZA", "WARN": "ATENTIE", "ERROR": "PROBLEMA"}[worst]
    return (f'<div class="hl-head hl-{worst.lower()}">{head}</div>'
            f'<div class="hl-grid">{"".join(tiles)}</div>')
