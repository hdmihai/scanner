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


def _path_vs_levels(p, path):
    """Ce prevede prognoza pentru NIVELURILE planului (nu fata de pretul curent):
    'stop' daca dupa atingerea intrarii ajunge la SL inaintea TP1, 'fara_intrare'
    daca nu atinge intrarea, altfel None. Un ordin limita LONG sub pret are nevoie
    de o scadere pana la intrare - o prognoza descendenta nu il contrazice."""
    e, sl, t1 = p.get("entry"), p.get("sl"), p.get("tp1")
    if None in (e, sl, t1) or len(path) < 2:
        return None
    up = p["direction"] == "LONG"
    pts = [q["price"] for q in path]
    filled = False
    for x in pts:
        if not filled and ((x <= e) if up else (x >= e)):
            filled = True
        if filled:
            if (x <= sl) if up else (x >= sl):
                return "stop"
            if (x >= t1) if up else (x <= t1):
                return None
    return None if filled else "fara_intrare"


def check_plan_vs_forecast(details, store):
    """INVARIANTUL DE LOGICA: un plan emis in scanarea CURENTA nu are voie sa fie
    emis cand structura Elliott e in conflict cu el (prognoza 'elliott_conflict'),
    decat ca explorare. Asta e o contradictie reala intre decizie si analiza -
    EROARE, cu carantina evidentei Elliott.
    Planurile mai vechi, cu analiza schimbata DUPA creare, sunt doar AVERTISMENT:
    piata a adus informatie noua, nu e un defect de logica. Varianta anterioara le
    trata ca eroare comparand prognoza cu pretul curent - pe MANA, un LONG limita
    sub pret, o prognoza care cobora spre intrare a fost raportata gresit ca eroare
    si a pus degeaba ev_elliott in carantina."""
    ts = _scan_ts(details)
    err, warn = [], []
    for p in store.get("plans") or []:
        if p.get("source") == "backtest" or p.get("realized_r") is not None:
            continue
        mode = ((p.get("decision") or {}).get("mode") or "")
        if mode.startswith("EXPLORARE"):
            continue
        s = (details.get("symbols") or {}).get(p["symbol"]) or {}
        fc = s.get("forecast") or {}
        path = fc.get("path") or []
        fresh = ts is not None and (p.get("created_ts") or 0) >= ts - 1800
        if fresh and fc.get("source") == "elliott_conflict":
            err.append(f"{p['symbol']} {p['direction']} (plan #{p['id']}, emis acum): structura Elliott e in "
                       f"conflict, dar planul a fost emis ({mode or 'fara decizie'})")
            continue
        verdict = _path_vs_levels(p, path)
        if verdict == "stop":
            warn.append(f"{p['symbol']} {p['direction']} (plan #{p['id']}, {p.get('state')}): prognoza curenta "
                        f"atinge SL ({p.get('sl'):.6g}) inaintea TP1 ({p.get('tp1'):.6g})")
    if err:
        return _chk("plan_forecast", "ERROR", "Planuri emise contra structurii Elliott",
                    "Decizia a emis planuri in timp ce structura era in conflict cu ele - decizia si "
                    "analiza nu folosesc aceeasi logica.", err + warn, ["ev_elliott"])
    if warn:
        return _chk("plan_forecast", "WARN", "Planuri deschise contrazise de analiza curenta",
                    "Analiza s-a schimbat dupa crearea planului si prevede acum atingerea SL inaintea "
                    "TP1 - informatie noua, nu defect de logica.", warn)
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
    """Live fata de backtest, pe ACEEASI perioada (aceeasi functie ca alarma din
    dashboard: plan_tracker.divergence). Comparatia cu media 2018-azi amesteca piata
    de acum cu ani in care strategia castiga mai mult si raporta o divergenta care
    venea din piata, nu din calcul."""
    import plan_tracker
    closed = [p for p in store.get("plans") or []
              if p.get("realized_r") is not None and p.get("state") != "NO_ENTRY"]
    live = [p for p in closed if p.get("source") != "backtest"]
    bt = [p for p in closed if p.get("source") == "backtest"]
    dv = plan_tracker.divergence(live, bt)
    if dv.get("status") == "date_insuficiente":
        return _chk("live_bt", "OK", "Live vs backtest",
                    f"prea putine planuri live inchise pentru comparatie ({len(live)})")
    win = dv.get("window") or ["?", "?"]
    basis = (f"in aceeasi perioada ({win[0]} - {win[1]}: {dv['n_live_window']} live, {dv['n_bt_window']} backtest)"
             if dv.get("basis") == "aceeasi_perioada" else
             "fata de tot istoricul (fereastra comuna e prea mica pentru o comparatie corecta)")
    txt = (f"live {dv['live_r']:+.3f}R/plan fata de backtest {dv['bt_r']:+.3f}R {basis}; diferenta "
           f"{dv['diff']:+.3f}R (IC95 {dv['ci_low']:+.3f}..{dv['ci_high']:+.3f})")
    if dv["status"] == "sub_backtest":
        return _chk("live_bt", "WARN", "Rezultatele live sub backtest", txt + " - depaseste zgomotul statistic.")
    return _chk("live_bt", "OK", "Live vs backtest", txt + " - in marja statistica.")


def backtest_edge(store):
    """Edge-ul strategiei in timp, pe backtest: castiga ACUM, nu doar in medie?
    Avertisment cand ultimele 12 luni nu sunt distinct pozitive."""
    import plan_tracker
    bt = [p for p in store.get("plans") or [] if p.get("source") == "backtest"
          and p.get("realized_r") is not None and p.get("state") != "NO_ENTRY"]
    e = plan_tracker.edge_by_period(bt)
    rec = e.get("recent")
    if e.get("status") == "date_insuficiente" or not rec:
        return _chk("edge", "OK", "Edge backtest", "prea putine planuri de backtest pentru o masurare pe perioade")
    years = " &middot; ".join(f"{y} {v['r']:+.3f}" for y, v in list(e["by_year"].items())[-4:])
    txt = (f"ultimele {e['recent_days']} zile (pana la {e['until']}): {rec['r']:+.3f}R/plan "
           f"(IC95 {rec['ci_low']:+.3f}..{rec['ci_high']:+.3f}, n={rec['n']}); pe ani: {years}")
    if e["status"] == "negativ":
        return _chk("edge", "WARN", "Edge-ul recent e negativ", txt)
    if e["status"] == "neconcludent":
        return _chk("edge", "WARN", "Edge-ul recent nu e distinct de zero",
                    txt + " - planurile au un avantaj masurat mai mic decat sugereaza media istorica.")
    return _chk("edge", "OK", "Edge backtest pozitiv", txt)


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


RESEARCH_FILE = "research.json"
# Investigatia ruleaza la FIECARE scanare (cateva secunde, pe planurile deja salvate).
# Repetarea nu creste riscul de reguli false: pe aceleasi date rezultatul e identic, iar
# o regula acceptata intra oricum in SHADOW si devine activa doar dupa confirmarea live.
# Singurul efect nedorit al rularii orare ar fi "palpairea" unei reguli aflate exact la
# prag (acceptata intr-o ora, respinsa in urmatoarea) - de aceea o regula in shadow sau
# activa e retrasa pe motiv de investigatie doar daca n-a mai fost acceptata de
# RETIRE_AFTER_DAYS zile (histerezis).
RETIRE_AFTER_DAYS = 7


def research_cycle(store, now=None):
    """CERCETAREA AUTONOMA (core/research.py), cu ciclul de viata al regulilor:

      investigatie -> regula acceptata -> SHADOW (calculata pe planurile live, nu filtreaza)
                   -> ACTIVA dupa confirmarea live (filtreaza, cu explorare)
                   -> RETRASA daca live o infirma sau o investigatie noua n-o mai accepta.

    Investigatia ruleaza la fiecare scanare (vezi RETIRE_AFTER_DAYS). Pe planurile
    existente dureaza cateva secunde: nu are nevoie de o scanare de backtest, doar de
    planurile de backtest deja salvate.
    Intoarce (regulile ACTIVE pentru decizie, verificarea pentru auto-diagnostic)."""
    import plan_tracker
    from core import research as R
    now = now or time.time()
    st = _load(RESEARCH_FILE, {}) or {}
    rules = st.get("rules") or {}
    plans = store.get("plans") or []
    closed = [p for p in plans if p.get("realized_r") is not None and p.get("state") != "NO_ENTRY"]
    bt = [p for p in closed if p.get("source") == "backtest"]
    live = [p for p in closed if p.get("source") != "backtest"]
    edge = plan_tracker.edge_by_period(bt)
    tracked = [r for r in rules.values() if r.get("state") in ("shadow", "activa")]
    due = True                                          # la fiecare scanare
    stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(now))
    if due:
        rep = R.investigate(plans)
        st["report"] = {k: v for k, v in rep.items()}
        st["last_run_ts"], st["last_run"] = now, stamp
        st["trigger"] = (f"scanare; edge recent {edge.get('status')}" if edge.get("status") in ("neconcludent", "negativ")
                         else ("scanare; reguli de urmarit" if tracked else "scanare"))
        st["runs"] = int(st.get("runs") or 0) + 1
        if rep.get("status") == "ok":
            ok_ids = {a["id"] for a in rep["accepted"]}
            for a in rep["accepted"]:
                r = rules.get(a["id"])
                if not r or r.get("state") == "retrasa":
                    rules[a["id"]] = {"id": a["id"], "text": a["text"], "rule": a["rule"], "state": "shadow",
                                      "since_ts": now, "since": stamp, "evidence": a}
                else:
                    r["evidence"] = a
                rules[a["id"]]["last_accepted_ts"] = now
            for rid, r in rules.items():
                if r.get("state") in ("shadow", "activa") and rid not in ok_ids:
                    gone = (now - (r.get("last_accepted_ts") or r.get("since_ts") or now)) / 86400
                    if gone >= RETIRE_AFTER_DAYS:
                        r.update(state="retrasa", retired=stamp,
                                 retired_reason=f"investigatiile nu o mai accepta de {gone:.0f} zile")
    for r in rules.values():
        if r.get("state") not in ("shadow", "activa"):
            continue
        verdict, info = R.live_verdict({**r["rule"]}, live, r.get("since_ts") or now)
        r["live"] = {**info, "verdict": verdict, "when": stamp}
        if r["state"] == "shadow" and verdict == "confirma":
            r.update(state="activa", activated=stamp)
        elif verdict == "infirma":
            r.update(state="retrasa", retired=stamp,
                     retired_reason="planurile live potrivite regulii au castigat mai mult decat restul")
    st["rules"] = rules
    st["edge_status"] = edge.get("status")
    out_path = os.path.join(DATA, RESEARCH_FILE)
    os.makedirs(DATA, exist_ok=True)
    with open(out_path + ".tmp", "w") as f:
        json.dump(st, f, separators=(",", ":"))
    os.replace(out_path + ".tmp", out_path)
    active = [{"id": r["id"], "text": r["text"], **r["rule"]} for r in rules.values() if r.get("state") == "activa"]
    rep = st.get("report") or {}
    n_sh = sum(1 for r in rules.values() if r.get("state") == "shadow")
    if rep.get("status") != "ok":
        chk = _chk("research", "OK", "Cercetare autonoma",
                   f"nicio investigatie inca ({rep.get('status') or 'neprogramata'})")
    else:
        chk = _chk("research", "OK", "Cercetare autonoma",
                   f"ultima investigatie {st.get('last_run')}: {rep['n_rules']} reguli testate pe "
                   f"{rep['n_plans']} planuri, {len(rep['accepted'])} acceptate; "
                   f"{len(active)} active, {n_sh} in shadow")
    return active, chk


def run():
    details = _load("latest_details.json", {}) or {}
    store = _load("plans.json", {}) or {}
    prev = _load("self_check.json", {}) or {}
    pm = prev.get("mitigations") or {}

    checks = [check_targets_vs_extreme(details), check_plan_vs_forecast(details, store),
              check_evidence_vs_panel(details, store), check_plan_geometry(store),
              check_calibration_coverage(store)] + check_freshness(details)
    fh_checks, fh_table = feature_health(store)
    checks += fh_checks + [live_vs_backtest(store), backtest_edge(store)]

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
    try:
        research_rules, research_chk = research_cycle(store)
    except Exception as e:                              # cercetarea nu are voie sa opreasca diagnosticul
        research_rules = list((pm.get("research_rules") or []))
        research_chk = _chk("research", "WARN", "Cercetarea autonoma a esuat", f"{type(e).__name__}: {e}")
    checks.append(research_chk)
    mitig = {"quarantine": quarantine, "elliott_filter": ew_on, "elliott_filter_reason": ew_why,
             "research_rules": research_rules,
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
