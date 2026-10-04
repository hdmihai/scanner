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
    # RITMUL DE ACUM, nu istoricul pe 24h: dupa o pauza, numaratoarea pe 24h ramane
    # mica inca multe ore, desi sistemul scaneaza deja orar. Starea se judeca dupa
    # varsta ultimei scanari si mediana ultimelor 3 intervale.
    gaps = [(b - a) / 60 for a, b in zip(ts[-4:], ts[-3:])] if len(ts) >= 4 else []
    med = sorted(gaps)[len(gaps) // 2] if gaps else None
    if age is None or age > 150:
        lvl = "ERROR"
    elif age > 75 or med is None or med > 90:
        lvl = "WARN"
    else:
        lvl = "OK"
    hint = {"ERROR": " &middot; scanarea s-a oprit: porneste Actions &rarr; Ceas scanare",
            "WARN": " &middot; ritmul orar nu e inca atins (ceasul il stabilizeaza in ~2-3 ore)",
            "OK": ""}[lvl]
    add(lvl, "Scanari", "ritm orar" if lvl == "OK" else ("oprite" if lvl == "ERROR" else "neregulate"),
        (f"ultima acum {age:.0f} min" if age is not None else "nicio scanare")
        + (f" &middot; ultimele intervale: {', '.join(f'{g:.0f}' for g in gaps)} min" if gaps else "")
        + f" &middot; {n24} in ultimele 24h{hint}")
    rr = [r for r in (runs or []) if now - (r.get("ts") or 0) <= 86400]
    if rr:
        bad = [r for r in rr if r.get("status") != "success"]
        last = bad[-1] if bad else None
        add("OK" if not bad else ("ERROR" if len(bad) * 2 >= len(rr) else "WARN"), "Rulari scanare 24h",
            f"{len(rr) - len(bad)} reusite / {len(bad)} esuate",
            f"surse: programate {sum(1 for r in rr if r.get('trigger', r.get('event')) == 'schedule')} &middot; "
            f"ceas {sum(1 for r in rr if r.get('trigger') == 'ceas')} &middot; "
            f"santinela {sum(1 for r in rr if r.get('trigger') == 'heartbeat')} &middot; "
            f"manuale {sum(1 for r in rr if r.get('trigger', r.get('event')) in ('manual', 'workflow_dispatch'))}<br>"
            + (
            (f'ultimul esec {last["when"]}' + (f' la pasul <strong>{last["failed"]}</strong>' if last.get("failed") else "")
             + f' &middot; <a href="{last["url"]}">deschide rularea</a>') if last else "toate rularile au reusit"))
    a = agent_state or {}
    lv = a.get("live") or {}
    n_live = lv.get("n", 0)
    conf = n_live >= 100 and (lv.get("ci_low") or 0) > 0.5
    worse = lv.get("ci_high") is not None and lv["ci_high"] < 0.5          # semnificativ sub hazard
    # SHADOW cat timp strange date e comportamentul CORECT, nu o problema: devine
    # avertisment doar daca, cu 100+ planuri live, ordonarea tot nu se confirma, si
    # problema daca e semnificativ mai slaba decat hazardul.
    lvl = ("ERROR" if worse else "WARN" if (n_live >= 100 and not conf and a.get("status") != "ACTIVE") else "OK")
    add(lvl, "Agent AI", a.get("status") or "n/d",
        ((f"confirmare live {n_live}/100 planuri"
          + (f" &middot; AUC live {lv['auc']:.3f} (IC {lv.get('ci_low')}-{lv.get('ci_high')})" if lv.get("auc") is not None else "")
          + ("" if a.get("status") == "ACTIVE" or n_live >= 100 else " &middot; invata, normal pana la 100"))
         if lv else "fara date live inca"))
    dv = ((plans_store or {}).get("summary") or {}).get("divergence") or {}
    if dv.get("status") in ("sub_backtest", "in_marja", "peste_backtest"):
        add("ERROR" if dv["status"] == "sub_backtest" else "OK", "Live vs backtest",
            f"{dv['diff']:+.3f}R/plan",
            f"IC95 {dv['ci_low']:+.3f}..{dv['ci_high']:+.3f} pe {dv['n_live']} planuri &middot; "
            + {"sub_backtest": "SEMNIFICATIV sub backtest", "in_marja": "in marja statistica",
               "peste_backtest": "peste backtest"}[dv["status"]])
    sc = self_check or {}
    # avertismente INFORMATIVE: semnaleaza informatie noua, nu un defect de reparat
    INFO_ONLY = {"plan_forecast"}
    if sc:
        bad = [c for c in sc.get("checks") or [] if c.get("level") != "OK"]
        real = [c for c in bad if not (c.get("level") == "WARN" and c.get("id") in INFO_ONLY)]
        lvl = "ERROR" if any(c.get("level") == "ERROR" for c in real) else ("WARN" if real else "OK")
        add(lvl, "Auto-diagnostic", {"OK": "OK", "WARN": "WARN", "ERROR": "ERROR"}[lvl],
            f"{sc.get('when')} &middot; " + ("; ".join(c["title"] for c in real[:2]) if real else
            ("toate verificarile OK" if not bad else f"{len(bad)} informare: " + bad[0]["title"])))
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
