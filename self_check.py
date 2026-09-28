#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
self_check.py
==============
Auto-diagnosticul agentului: DETECTEAZA -> ATENUEAZA -> RAPORTEAZA, la fiecare
scanare, fara ca cineva sa trimita date sau capturi.

DE CE EXISTA
------------
Agentul invata PONDERI (cat conteaza fiecare evidenta), nu COD. Cand logica
care calculeaza o evidenta e gresita, agentul vede doar un semnal zgomotos si ii
scade ponderea - pe SEI, ev_elliott ajunsese la +0.04 - dar nu poate afla DE CE.
Bug-urile de logica gasite pana acum au fost descoperite doar din capturi:
tinte comparate cu pretul curent in loc de extremul undei (SEI), evidente
calculate cu seria altui token (NEAR), calibrare pe 46 de planuri in loc de
16.000 (geometrie). Fiecare avea un simptom MASURABIL. Modulul asta le masoara.

CE FACE, CONCRET
----------------
1. INVARIANTI - lucruri care nu au voie sa se intample niciodata:
   - tinta Elliott marcata neatinsa, desi unda a depasit-o
   - plan emis, iar prognoza de pe grafic merge integral impotriva lui
   - evidenta din plan contrazice panoul calculat independent, pe aceleasi date
   - niveluri de plan imposibile (SL de partea gresita a intrarii)
   - calibrarea foloseste mult mai putine planuri decat exista
2. MONITOARE STATISTICE - simptome, fara sa stie cauza:
   - o caracteristica devenita constanta in planurile recente (calcul stricat)
   - o caracteristica al carei efect s-a inversat pe datele recente
   - rezultatele live mult sub cele din backtest
3. ATENUARI AUTOMATE - cu efect de la scanarea urmatoare:
   - CARANTINA: o caracteristica suspecta e ignorata de agent (valoare 0) pana
     trece curata verificarile 3 rulari la rand
   - FILTRUL ELLIOTT se porneste/opreste singur, dupa ce arata datele recente
   - MODUL DE SIGURANTA: la niveluri de plan imposibile, nu se mai emit planuri
4. RAPORTARE: data/self_check.json (citit de dashboard) si un Issue GitHub
   deschis automat pentru fiecare eroare NOUA - notificare pe telefon.

CE NU FACE
----------
Nu rescrie cod. Repararea automata a codului unui sistem de tranzactionare, fara
revizuire umana, ar putea introduce erori mai grave decat cele reparate. Modulul
izoleaza problema, o face inofensiva si o raporteaza precis - repararea codului
ramane un pas revizuit.
"""

import json
import math
import os
import time
import urllib.request

DATA = "data"
OUT = os.path.join(DATA, "self_check.json")
RELEASE_AFTER = 3            # rulari curate consecutive pana la iesirea din carantina
RECENT = 2000                # fereastra "recenta" pentru monitoarele statistice

# ce caracteristici depind de ce calcul - pentru carantina tintita
STRUCT_FEATURES = ["ev_ichimoku", "ev_ma_cross", "ev_regime", "ev_mtf_align",
                   "ev_elliott", "ev_liq_struct", "ev_liq_sweep"]


def _load(name, default):
    try:
        with open(os.path.join(DATA, name)) as f:
            return json.load(f)
    except Exception:
        return default


def _family(geo):
    return "-".join(str(geo or "").split("-")[:2])


def _mean_se(v):
    n = len(v)
    if n < 2:
        return None, None
    m = sum(v) / n
    var = sum((x - m) ** 2 for x in v) / (n - 1)
    return m, math.sqrt(var / n)


def _chk(cid, level, title, detail, examples=None, features=None):
    return {"id": cid, "level": level, "title": title, "detail": detail,
            "examples": (examples or [])[:5], "features": features or []}


def _scan_ts(details):
    try:
        return time.mktime(time.strptime(details["scan_time"], "%Y-%m-%d %H:%M UTC")) - time.timezone
    except Exception:
        return None


# ---------------------------------------------------------------------------
# INVARIANTI
# ---------------------------------------------------------------------------
def check_targets_vs_extreme(details):
    """O tinta marcata neatinsa trebuie sa fie DINCOLO de extremul undei."""
    bad = []
    for sym, s in (details.get("symbols") or {}).items():
        ew = s.get("elliott") or {}
        pr = ew.get("primary") or {}
        tg, hit = pr.get("targets") or {}, pr.get("targets_hit") or {}
        pts = pr.get("points") or []
        if not tg or not pts:
            continue
        sign = 1 if pr.get("direction") == "LONG" else -1
        ext = pr.get("extreme")
        if ext is None:                       # date vechi: extremul din lumanari
            cd, off = s.get("candles") or [], ew.get("offset", 0)
            a = (pts[-2]["idx"] if len(pts) >= 2 else pts[-1]["idx"]) - off
            seg = cd[max(0, a):]
            if not seg:
                continue
            ext = max(c[2] for c in seg) if sign > 0 else min(c[3] for c in seg)
        for k, v in tg.items():
            if v is not None and hit.get(k) is False and sign * (ext - v) >= 0:
                bad.append(f"{sym}: {k.upper()} {v:.6g} marcat neatins, desi unda a atins {ext:.6g}")
    if bad:
        return _chk("ew_targets", "ERROR", "Tinte Elliott marcate gresit",
                    f"{len(bad)} tinte apar neatinse desi unda le-a depasit - stadiul si directia "
                    "asteptata se calculeaza atunci pe o baza falsa.", bad, ["ev_elliott"])
    return _chk("ew_targets", "OK", "Tinte Elliott", "tintele sunt marcate conform extremului undei")


def check_plan_vs_forecast(details, store):
    """Un plan DESCHIS nu are voie sa aiba prognoza curenta integral impotriva lui.
    Pentru un plan care inca asteapta intrarea (PENDING) e o eroare - decizia de a
    intra e inca activa si contrazice propria analiza. Pentru unul deja intrat e
    doar un avertisment: piata s-a schimbat dupa intrare. Planurile emise explicit
    pentru explorare sunt excluse. Varianta initiala verifica doar planurile create
    in ultima scanare si rata planul SEI, creat cu 1.9 ore inainte si inca PENDING."""
    err, warn = [], []
    for p in store.get("plans") or []:
        if p.get("source") == "backtest" or p.get("realized_r") is not None:
            continue
        mode = ((p.get("decision") or {}).get("mode") or "")
        if mode.startswith("EXPLORARE"):
            continue
        s = (details.get("symbols") or {}).get(p["symbol"]) or {}
        path = ((s.get("forecast") or {}).get("path") or [])
        if len(path) < 3:
            continue
        now = path[0]["price"]
        proj = [q["price"] for q in path[1:]]
        against = (all(x < now * 0.998 for x in proj) if p["direction"] == "LONG"
                   else all(x > now * 1.002 for x in proj))
        if against:
            msg = (f"{p['symbol']} {p['direction']} (plan #{p['id']}, {p.get('state')}): prognoza merge "
                   f"integral impotriva ({now:.6g} -> {proj[-1]:.6g})")
            (err if p.get("state") == "PENDING" else warn).append(msg)
    if err:
        return _chk("plan_forecast", "ERROR", "Planuri in asteptare contra propriei prognoze",
                    "Planuri care inca asteapta intrarea merg impotriva prognozei afisate - "
                    "decizia si analiza nu folosesc aceeasi logica.", err + warn, ["ev_elliott"])
    if warn:
        return _chk("plan_forecast", "WARN", "Planuri intrate, contrazise acum de prognoza",
                    "Piata s-a schimbat dupa intrare; prognoza curenta merge impotriva planului.", warn)
    return _chk("plan_forecast", "OK", "Plan vs prognoza", "planurile deschise sunt coerente cu prognoza")


def check_evidence_vs_panel(details, store):
    """Evidenta din plan si panoul de structura sunt doua calcule independente pe
    ACELEASI lumanari - trebuie sa coincida."""
    ts = _scan_ts(details)
    bad, seen = [], 0
    for p in store.get("plans") or []:
        if p.get("source") == "backtest" or ts is None or (p.get("created_ts") or 0) < ts - 1800:
            continue
        panel = ((details.get("symbols") or {}).get(p["symbol"]) or {}).get("structure_panel") or {}
        vote = (panel.get("ichimoku") or {}).get("vote")
        ev = next((e for e in p.get("evidence") or [] if e.get("key") == "ichimoku"), None)
        if ev is None or not vote:
            continue
        seen += 1
        if (ev.get("direction") == "LONG") != (vote > 0):
            bad.append(f"{p['symbol']}: evidenta planului '{ev.get('label')}', panoul spune "
                       f"{(panel.get('ichimoku') or {}).get('position')}")
    if bad:
        return _chk("evidence_panel", "ERROR", "Evidente calculate pe date gresite",
                    f"{len(bad)} din {seen} planuri au evidente care contrazic panoul calculat pe "
                    "aceleasi lumanari - calculul evidentelor foloseste alta serie decat cea a "
                    "simbolului.", bad, STRUCT_FEATURES)
    return _chk("evidence_panel", "OK", "Evidente vs panou",
                f"evidentele coincid cu panoul ({seen} planuri verificate)")


def check_plan_geometry(store):
    bad = []
    for p in store.get("plans") or []:
        if p.get("realized_r") is not None or p.get("source") == "backtest":
            continue
        e, sl, t1, t2 = p.get("entry"), p.get("sl"), p.get("tp1"), p.get("tp2")
        if None in (e, sl, t1, t2):
            continue
        ok = (0 < sl < e < t1 <= t2) if p["direction"] == "LONG" else (0 < t2 <= t1 < e < sl)
        if not ok:
            bad.append(f"{p['symbol']} {p['direction']} #{p['id']}: SL {sl:.6g} ENTRY {e:.6g} "
                       f"TP1 {t1:.6g} TP2 {t2:.6g}")
    if bad:
        return _chk("plan_geometry", "ERROR", "Niveluri de plan imposibile",
                    "Planuri deschise cu SL sau tinte de partea gresita a intrarii - calculul "
                    "planului e stricat. MOD DE SIGURANTA activat: nu se mai emit planuri.", bad)
    return _chk("plan_geometry", "OK", "Geometria planurilor", "toate planurile deschise sunt coerente")


def check_calibration_coverage(store):
    plans = store.get("plans") or []
    live = [p for p in plans if p.get("source") != "backtest"]
    fam = _family((live or plans or [{}])[-1].get("geometry"))
    closed = [p for p in plans if p.get("realized_r") is not None and p.get("state") != "NO_ENTRY"
              and _family(p.get("geometry")) == fam]
    n_cal = sum((v or {}).get("total") or 0 for v in (store.get("calibration") or {}).values())
    if len(closed) >= 200 and n_cal < 0.5 * len(closed):
        return _chk("calibration", "ERROR", "Calibrarea ignora majoritatea planurilor",
                    f"calibrarea foloseste {n_cal} planuri din {len(closed)} inchise in familia "
                    f"{fam} - probabilitatile afisate sunt calculate pe un esantion minuscul.")
    return _chk("calibration", "OK", "Acoperirea calibrarii",
                f"{n_cal} din {len(closed)} planuri inchise ale familiei {fam}")


def check_freshness(details):
    out = []
    ts = _scan_ts(details)
    if ts is not None and time.time() - ts > 3 * 3600:
        out.append(_chk("fresh_scan", "WARN", "Scanare veche",
                        f"ultima scanare e de acum {(time.time() - ts) / 3600:.1f} ore"))
    alt = _load("altseason.json", None)
    if alt is None or alt.get("stale") or time.time() - (alt or {}).get("ts", 0) > 6 * 3600:
        out.append(_chk("fresh_altseason", "WARN", "Faza altcoin season neactualizata",
                        "datele CoinGecko lipsesc sau sunt mai vechi de 6 ore"))
    meta = (_load("token_metadata.json", {}) or {}).get("tokens") or {}
    scans = _load("exchange_scans.json", {}) or {}
    uni = set()
    for sc in (scans.get("scans") or {}).values():
        uni |= {x for x in (sc.get("resolved") or []) if isinstance(x, str) and x.endswith("/USDT")}
    if uni and len([u for u in uni if u in meta]) < 0.8 * len(uni):
        out.append(_chk("fresh_meta", "WARN", "Metadata incompleta",
                        f"{len([u for u in uni if u in meta])} din {len(uni)} tokeni au metadata"))
    return out or [_chk("freshness", "OK", "Prospetimea datelor", "scanare, altseason si metadata la zi")]


# ---------------------------------------------------------------------------
# MONITOARE STATISTICE
# ---------------------------------------------------------------------------
def feature_health(store):
    plans = [p for p in store.get("plans") or []
             if p.get("realized_r") is not None and p.get("state") != "NO_ENTRY" and p.get("components")]
    if len(plans) < 500:
        return [], {}
    plans.sort(key=lambda p: p.get("closed_ts") or 0)
    recent = plans[-RECENT:]
    feats = sorted({k for p in plans[-50:] for k in p["components"] if k.startswith("ev_")})
    checks, table = [], {}

    def spread(group, f):
        pos = [p["realized_r"] for p in group if (p["components"].get(f) or 0) > 0.1]
        neg = [p["realized_r"] for p in group if (p["components"].get(f) or 0) < -0.1]
        if len(pos) < 150 or len(neg) < 150:
            return None, None
        (mp, sp), (mn, sn) = _mean_se(pos), _mean_se(neg)
        return mp - mn, math.sqrt(sp ** 2 + sn ** 2)

    for f in feats:
        vals_r = [p["components"].get(f) for p in recent if f in p["components"]]
        vals_a = [p["components"].get(f) for p in plans if f in p["components"]]
        if len(vals_r) < 200:
            continue
        top_r = max(vals_r.count(v) for v in set(vals_r)) / len(vals_r)
        top_a = max(vals_a.count(v) for v in set(vals_a[-5000:])) / min(len(vals_a), 5000)
        d_all, se_all = spread(plans, f)
        d_rec, se_rec = spread(recent, f)
        table[f] = {"spread_all": d_all and round(d_all, 4), "spread_recent": d_rec and round(d_rec, 4),
                    "constant_share_recent": round(top_r, 3)}
        if top_r >= 0.95 and top_a < 0.8:
            checks.append(_chk(f"deg_{f}", "WARN", f"{f} a devenit constanta",
                               f"{top_r:.0%} din planurile recente au aceeasi valoare, desi istoric "
                               "varia - calculul caracteristicii pare stricat. Pusa in carantina.",
                               features=[f]))
        elif (d_all is not None and d_rec is not None and d_all - 2 * se_all > 0
              and d_rec + 2 * se_rec < 0):
            checks.append(_chk(f"flip_{f}", "WARN", f"Efectul {f} s-a inversat",
                               f"istoric {d_all:+.3f}R, recent {d_rec:+.3f}R - fie regimul pietei s-a "
                               "schimbat, fie calculul. Agentul isi ajusteaza ponderea singur."))
    return checks, table


def live_vs_backtest(store):
    closed = [p for p in store.get("plans") or []
              if p.get("realized_r") is not None and p.get("state") != "NO_ENTRY"]
    live = [p["realized_r"] for p in closed if p.get("source") != "backtest"]
    bt = [p["realized_r"] for p in closed if p.get("source") == "backtest"]
    if len(live) < 30 or len(bt) < 300:
        return _chk("live_bt", "OK", "Live vs backtest",
                    f"prea putine planuri live inchise pentru comparatie ({len(live)})")
    (ml, sl), (mb, _sb) = _mean_se(live), _mean_se(bt)
    if ml < mb - 2 * sl:
        return _chk("live_bt", "WARN", "Rezultatele live sub backtest",
                    f"live {ml:+.3f}R/plan (n={len(live)}) fata de backtest {mb:+.3f}R - diferenta "
                    "depaseste zgomotul statistic.")
    return _chk("live_bt", "OK", "Live vs backtest",
                f"live {ml:+.3f}R/plan (n={len(live)}), backtest {mb:+.3f}R - in marja statistica")


def elliott_filter_decision(store, previous):
    """Filtrul de conflict Elliott se justifica doar cat timp datele RECENTE arata
    ca planurile contra Elliott rezulta mai slab. Decis din nou la fiecare rulare,
    cu histerezis: in zona nesigura pastreaza starea anterioara."""
    plans = [p for p in store.get("plans") or [] if p.get("realized_r") is not None
             and p.get("state") != "NO_ENTRY" and (p.get("components") or {}).get("ev_elliott") is not None]
    plans.sort(key=lambda p: p.get("closed_ts") or 0)
    rec = plans[-3000:]
    con = [p["realized_r"] for p in rec if p["components"]["ev_elliott"] <= -0.1]
    rest = [p["realized_r"] for p in rec if p["components"]["ev_elliott"] > -0.1]
    if len(con) < 150 or len(rest) < 150:
        return previous, "prea putine date recente - pastrez starea"
    (mc, sc), (mr, sr) = _mean_se(con), _mean_se(rest)
    d, se = mr - mc, math.sqrt(sc ** 2 + sr ** 2)
    txt = (f"ultimele {len(rec)} planuri: contra Elliott {mc:+.3f}R, fara conflict {mr:+.3f}R, "
           f"diferenta {d:+.3f}R (IC95 {d - 1.96 * se:+.3f}..{d + 1.96 * se:+.3f})")
    if d - 1.96 * se > 0:
        return True, "justificat: " + txt
    if d <= 0:
        return False, "oprit automat, nu mai e justificat: " + txt
    return previous, "zona nesigura, pastrez starea: " + txt


# ---------------------------------------------------------------------------
def open_issue(title, body):
    tok, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not tok or not repo:
        return False
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/issues",
            data=json.dumps({"title": title, "body": body, "labels": ["auto-diagnostic"]}).encode(),
            headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"},
            method="POST")
        urllib.request.urlopen(req, timeout=20)
        return True
    except Exception as e:
        print(f"[!] nu am putut deschide issue-ul: {e}")
        return False


def run():
    details = _load("latest_details.json", {}) or {}
    store = _load("plans.json", {}) or {}
    prev = _load("self_check.json", {}) or {}
    pm = prev.get("mitigations") or {}

    checks = [check_targets_vs_extreme(details), check_plan_vs_forecast(details, store),
              check_evidence_vs_panel(details, store), check_plan_geometry(store),
              check_calibration_coverage(store)] + check_freshness(details)
    fh_checks, fh_table = feature_health(store)
    checks += fh_checks + [live_vs_backtest(store)]

    # CARANTINA: caracteristicile legate de verificari esuate. Iese din carantina
    # dupa RELEASE_AFTER rulari consecutive curate.
    quarantine = dict(pm.get("quarantine") or {})
    flagged = {f for c in checks if c["level"] in ("ERROR", "WARN") for f in c.get("features") or []}
    for f in flagged:
        q = quarantine.get(f) or {"since": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}
        q["clean_runs"] = 0
        q["reason"] = next(c["title"] for c in checks if f in (c.get("features") or []))
        quarantine[f] = q
    for f in list(quarantine):
        if f not in flagged:
            quarantine[f]["clean_runs"] = quarantine[f].get("clean_runs", 0) + 1
            if quarantine[f]["clean_runs"] >= RELEASE_AFTER:
                del quarantine[f]

    ew_on, ew_why = elliott_filter_decision(store, pm.get("elliott_filter", True))
    geo_err = any(c["id"] == "plan_geometry" and c["level"] == "ERROR" for c in checks)
    mitig = {"quarantine": quarantine, "elliott_filter": ew_on, "elliott_filter_reason": ew_why,
             "safe_mode": geo_err,
             "safe_reason": "niveluri de plan imposibile - vezi auto-diagnosticul" if geo_err else None}

    status = ("ERROR" if any(c["level"] == "ERROR" for c in checks)
              else "WARN" if any(c["level"] == "WARN" for c in checks) else "OK")
    reported = set(prev.get("reported") or [])
    current_err = {c["id"] for c in checks if c["level"] == "ERROR"}
    for c in checks:
        if c["level"] == "ERROR" and c["id"] not in reported:
            body = (f"**{c['title']}**\n\n{c['detail']}\n\nExemple:\n"
                    + "\n".join(f"- {e}" for e in c["examples"])
                    + f"\n\nAtenuare automata: carantina {sorted(quarantine)} · filtru Elliott "
                    f"{'activ' if ew_on else 'oprit'} · mod siguranta {'DA' if geo_err else 'nu'}"
                    "\n\n_Deschis automat de self_check.py._")
            if open_issue(f"[auto-diagnostic] {c['title']}", body):
                print(f"  Issue deschis: {c['title']}")
    hist = (prev.get("history") or [])[-199:] + [{"ts": int(time.time()), "status": status}]
    out = {"ts": time.time(), "when": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
           "status": status, "checks": checks, "mitigations": mitig, "feature_health": fh_table,
           "reported": sorted(reported & current_err | current_err), "history": hist}
    # calea se calculeaza ACUM, din DATA - fixata la import, nu urma un DATA
    # schimbat ulterior (prins de testul buclei de auto-reparare)
    out_path = os.path.join(DATA, "self_check.json")
    os.makedirs(DATA, exist_ok=True)
    with open(out_path + ".tmp", "w") as f:
        json.dump(out, f, separators=(",", ":"))
    os.replace(out_path + ".tmp", out_path)
    n = {lv: sum(1 for c in checks if c["level"] == lv) for lv in ("ERROR", "WARN", "OK")}
    print(f"Auto-diagnostic: {status} | {n['ERROR']} erori, {n['WARN']} avertismente, {n['OK']} OK | "
          f"carantina {sorted(quarantine) or '-'} | filtru Elliott {'activ' if ew_on else 'oprit'}"
          + (" | MOD SIGURANTA" if geo_err else ""))
    for c in checks:
        if c["level"] != "OK":
            print(f"  [{c['level']}] {c['title']}: {c['detail']}")
    return out


if __name__ == "__main__":
    try:
        run()
    except Exception as e:           # diagnosticul nu opreste niciodata workflow-ul
        print(f"[!] auto-diagnosticul a esuat: {e}")
