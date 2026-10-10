# -*- coding: utf-8 -*-
"""dashboard.summaries - rezumatul de cel mult 3 randuri al fiecarei sectiuni pliate.

Fiecare sectiune a dashboard-ului e pliata implicit (dashboard.components.fold); deasupra ei
raman vizibile cel mult MAX_LINES randuri cu ce conteaza cel mai mult - starea, cifra cheie,
ce urmeaza. Fiecare functie primeste aceleasi date ca sectiunea ei si intoarce o lista de
texte simple (fara HTML; se escapeaza la afisare). Nicio cifra nu se inventeaza: o valoare
lipsa scoate randul, nu il umple cu "n/d". O functie care esueaza intoarce [] - sectiunea se
afiseaza oricum, doar fara rezumat (izolarea din dashboard.page._safe).
"""

import html as _html
import re

from dashboard.components import fmt_price

MAX_LINES = 3
_TAG = re.compile(r"<[^>]+>")


def plain(s):
    """Text simplu dintr-un fragment HTML (etichete scoase, entitati decodate)."""
    return re.sub(r"\s+", " ", _html.unescape(_TAG.sub(" ", str(s or "")))).strip()


def _lines(*items):
    return [plain(x) for x in items if x][:MAX_LINES]


def _pct(v, signed=True, nd=1):
    if v is None:
        return None
    return f"{v:+.{nd}f}%" if signed else f"{v:.{nd}f}%"


def _usd(v):
    if v is None:
        return None
    return f"${v:,.0f}" if v >= 1000 else f"${fmt_price(v)}"


# --------------------------------------------------------------------- pagina principala

def health(worst, items):
    """Starea sistemului: verdictul general + ce nu e OK (sau cifrele cheie, daca totul e OK)."""
    head = {"OK": "Totul funcționează", "WARN": "Atenție", "ERROR": "Problemă"}.get(worst, worst)
    bad = [t for t in items if t[0] != "OK"]
    out = [f"{head} · {len(items) - len(bad)}/{len(items)} verificări OK"]
    for lvl, title, value, _detail in (bad or items)[:2]:
        out.append(f"{'⚠ ' if lvl != 'OK' else ''}{plain(title)}: {plain(value)}")
    return _lines(*out)


def analyst(rep):
    if not rep or rep.get("error"):
        return _lines((rep or {}).get("error") and f"Raport indisponibil: {rep['error']}")
    st, mac = rep.get("strategy") or {}, rep.get("macro") or {}
    ph, bd = mac.get("phase") or {}, mac.get("btc_d") or {}
    l1 = f"Sentiment piață {st.get('sentiment', '?')} · {ph.get('label') or ''}".rstrip(" ·")
    top = [f"{s['name']} {s['rs30']:+.0f}%" for s in ((rep.get("sectors") or {}).get("top") or [])[:3]
           if s.get("rs30") is not None]
    l2 = " · ".join(x for x in [
        bd.get("value") is not None and f"BTC.D {bd['value']:.2f}% {(bd.get('status') or '').lower()}".strip(),
        top and "sectoare vs BTC/30z: " + ", ".join(top)] if x)
    toks = rep.get("tokens") or {}
    cnt = {}
    for t in toks.values():
        s = ((t or {}).get("strategy") or {}).get("sentiment")
        cnt[s] = cnt.get(s, 0) + 1
    guide = {"DCA": "DCA", "WAIT": "așteptare", "NONE": "fără intrare"}.get(st.get("guide_kind"), "")
    l3 = (f"{len(toks)} tokeni: " + " · ".join(f"{cnt[k]} {k}" for k in ("Bullish", "Neutru", "Bearish") if cnt.get(k))
          + (f" · ghid: {guide}" if guide else "")) if toks else (guide and f"Ghid: {guide}")
    return _lines(l1, l2, l3)


def altseason(state):
    if not state:
        return []
    c, i = state.get("classification") or {}, state.get("indicators") or {}
    cy = c.get("cycle") or {}
    conf = c.get("confidence")
    l1 = (f"Regim {c.get('regime_name') or c.get('regime') or '?'} · faza {c.get('phase')} {c.get('name') or ''}"
          + (f" ({conf * 100:.0f}%)" if conf is not None else ""))
    l2 = " · ".join(x for x in [
        i.get("alt_index_90d") is not None and f"indice alts {i['alt_index_90d']:.0f}/100",
        i.get("btc_d") is not None and f"BTC.D {i['btc_d']:.2f}%",
        i.get("eth_btc_30d") is not None and f"ETH/BTC {i['eth_btc_30d']:+.1f}%/30z"] if x)
    l3 = " · ".join(x for x in [
        cy.get("price") and f"BTC {_usd(cy['price'])}",
        cy.get("dd") is not None and f"{cy['dd']:.0f}% de la ATH",
        cy.get("above_ma200") is not None and ("peste" if cy["above_ma200"] else "sub") + " MA200",
        state.get("stale") and "DATE VECHI"] if x)
    return _lines(l1, l2, l3)


def exchanges(store, scans_store):
    cards = (store or {}).get("exchanges") or []
    if not cards:
        return []
    on = [c for c in cards if c.get("connected")]
    used = (store or {}).get("used")
    lbl = {c.get("id"): c.get("label", c.get("id")) for c in cards}
    scans = (scans_store or {}).get("scans") or {}
    l1 = f"Bursa activă (planuri + învățare): {lbl.get(used, used or '?')} · {len(on)}/{len(cards)} conectate"
    l2 = ("Analizate: " + ", ".join(f"{lbl.get(k, k)} {len(v.get('results') or [])}"
                                    for k, v in scans.items() if not v.get("error"))) if scans else None
    off = [c for c in cards if not c.get("connected")]
    l3 = ("Indisponibile: " + ", ".join(c.get("label", c.get("id")) for c in off)) if off else None
    return _lines(l1, l2, l3)


def briefing(brief):
    f = (brief or {}).get("facts") or {}
    real, sim = (f.get("real") or {}).get("all"), (f.get("simulated") or {}).get("all")
    if real is not None or sim is not None:
        lv, al = f.get("live") or {}, (f.get("real") or {}).get("agent_live") or {}
        l1 = (f"Real: {real['n']} închise · {real['win_rate']}% câștig · {real['avg_r']:+.3f}R/plan"
              + (f" (din {lv['since']})" if lv.get("since") else "")) if real else "Real: niciun plan live închis încă"
        l2 = (f"Simulat: {sim['n']} închise · {sim['win_rate']}% · {sim['avg_r']:+.3f}R/plan"
              + (f" (backtest din {(f.get('simulated') or {}).get('since')})" if (f.get('simulated') or {}).get('since') else "")
              ) if sim else "Simulat: niciun backtest integrat"
        l3 = (f"Agent {f.get('agent_status') or '?'}"
              + (f" · confirmare live {al['n']}/100" if al.get("n") is not None and f.get("agent_status") != "ACTIVE" else ""))
        return _lines(l1, l2, l3)
    t = plain((brief or {}).get("text"))
    if not t:
        return []
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", t) if p.strip()]
    return _lines(*parts[:MAX_LINES])


def chart(best, deep, analyst_tok):
    if not best:
        return []
    pl = (deep or {}).get("plan") or {}
    l1 = (f"{best.get('symbol')} {best.get('direction')} · scor {best.get('risk_adjusted')} · "
          f"preț {fmt_price(best.get('price'))}")
    l2 = (f"Plan: intrare {fmt_price(pl['entry'])} · SL {fmt_price(pl['sl'])} · TP1 {fmt_price(pl['tp1'])} · "
          f"TP2 {fmt_price(pl['tp2'])}") if pl.get("entry") is not None else None
    st = (analyst_tok or {}).get("strategy") or {}
    l3 = (f"Analist: {st['sentiment']}" + (f" · invalidare {fmt_price(st['inv_price'])}" if st.get("inv_price") else "")
          ) if st.get("sentiment") else None
    return _lines(l1, l2, l3)


def indicators(deep):
    ind = (deep or {}).get("indicators") or {}
    if not ind:
        return []
    px, vw = ind.get("price"), ind.get("vwap")
    st, md, vp = ind.get("supertrend") or {}, ind.get("macd") or {}, ind.get("volume_profile") or {}
    l1 = " · ".join(x for x in [
        vw and px and f"preț {fmt_price(px)} {'peste' if px > vw else 'sub'} VWAP {fmt_price(vw)}",
        st.get("direction") and f"SuperTrend {st['direction'].lower()}"] if x)
    l2 = " · ".join(x for x in [
        md.get("histogram") is not None and f"MACD {'pozitiv' if md.get('bullish') else 'negativ'} "
                                            f"(histogramă {md['histogram']:+.4g})",
        ind.get("price_vs_value_area") and f"{ind['price_vs_value_area'].lower()}"] if x)
    l3 = (f"POC {fmt_price(vp['poc'])} · zonă de valoare {fmt_price(vp.get('val'))}–{fmt_price(vp.get('vah'))}"
          if vp.get("poc") else None)
    return _lines(l1, l2, l3)


def structure(deep):
    s, f = (deep or {}).get("structure") or {}, (deep or {}).get("fibonacci") or {}
    if not s and not f:
        return []
    l1 = ("Rezistențe: " + " · ".join(fmt_price(v) for v in (s.get("resistance") or [])[:3])) if s.get("resistance") else None
    l2 = ("Suporturi: " + " · ".join(fmt_price(v) for v in (s.get("support") or [])[:3])) if s.get("support") else None
    l3 = (f"Fibonacci pe swing {fmt_price(f['swing_low'])} – {fmt_price(f['swing_high'])}"
          if f.get("swing_low") is not None and f.get("swing_high") is not None else None)
    return _lines(l1, l2, l3)


def liquidity(deep):
    lq = (deep or {}).get("liquidity") or {}
    bids, asks = lq.get("bids") or [], lq.get("asks") or []
    if not bids and not asks:
        return ["Fără order book pentru candidatul principal"]
    b = max(bids, key=lambda x: x.get("amount") or 0) if bids else None
    a = max(asks, key=lambda x: x.get("amount") or 0) if asks else None
    sb, sa = sum(x.get("amount") or 0 for x in bids), sum(x.get("amount") or 0 for x in asks)
    l1 = b and f"Cel mai mare zid de cumpărare: {fmt_price(b['price'])} ({b['amount']:,.0f})"
    l2 = a and f"Cel mai mare zid de vânzare: {fmt_price(a['price'])} ({a['amount']:,.0f})"
    l3 = (sb + sa) and f"Cumpărare {100 * sb / (sb + sa):.0f}% / vânzare {100 * sa / (sb + sa):.0f}% din nivelurile afișate"
    return _lines(l1, l2, l3)


def tokens_moved(cards):
    on = [c.get("label", c.get("id")) for c in cards or [] if c.get("connected")]
    return _lines("Graficele și analiza completă per token sunt pe paginile burselor",
                  on and ", ".join(on))


def top(rows, label):
    rows = rows or []
    if not rows:
        return [f"Niciun semnal {label}"]
    l1 = f"{len(rows)} semnale {label} · " + ", ".join(
        f"{r['symbol'].split('/')[0]} {r['risk_adjusted']}" for r in rows[:4])
    b = rows[0]
    l2 = f"Primul: {b['symbol']} scor {b['risk_adjusted']} · persistență {b.get('persistence')} scanări"
    return _lines(l1, l2)


def plan(best, decision, open_plan):
    if open_plan:
        lvl = f"{open_plan.get('symbol')} {open_plan.get('direction')} · plan urmărit: {open_plan.get('state_detail') or open_plan.get('state')}"
        return _lines(lvl, f"intrare {fmt_price(open_plan.get('entry'))} · SL {fmt_price(open_plan.get('sl'))} · "
                           f"TP2 {fmt_price(open_plan.get('tp2'))}")
    if not best:
        return ["Niciun candidat la această scanare"]
    d = decision or {}
    l1 = f"{best.get('symbol')} {best.get('direction')} · decizie: {d.get('action') or 'n/a'}" + (
        f" ({d.get('mode')})" if d.get("mode") else "")
    l2 = d.get("reason") and plain(d["reason"])[:140]
    l3 = " · ".join(x for x in [
        d.get("calibrated_prob") is not None and f"probabilitate măsurată {d['calibrated_prob']}%",
        isinstance(d.get("agent_prob"), (int, float)) and f"agent {d['agent_prob'] * 100:.0f}%"] if x)
    return _lines(l1, l2, l3)


def self_check(sc):
    if not sc:
        return ["Auto-diagnosticul nu a rulat (data/self_check.json lipsește)"]
    checks = sc.get("checks") or []
    bad = [c for c in checks if c.get("level") != "OK"]
    l1 = f"{sc.get('status', '?')} · {len(checks) - len(bad)}/{len(checks)} verificări OK · {sc.get('when', '')}".strip(" ·")
    l2 = bad and "; ".join(c.get("title", "") for c in bad[:2])
    m = sc.get("mitigations") or {}
    q = sorted(m.get("quarantine") or {})
    l3 = " · ".join(x for x in [
        f"carantină: {', '.join(q)}" if q else "fără caracteristici în carantină",
        "filtru Elliott " + ("activ" if m.get("elliott_filter") else "oprit"),
        m.get("safe_mode") and "MOD DE SIGURANȚĂ"] if x)
    return _lines(l1, l2, l3)


def research(state):
    rep = (state or {}).get("report") or {}
    if not rep:
        return ["Cercetarea nu a rulat încă"]
    acc = rep.get("accepted") or []
    rules = (state or {}).get("rules") or {}
    l1 = (f"{rep.get('n_rules', 0)} reguli testate pe {rep.get('n_plans', 0)} planuri · "
          f"{len(acc)} acceptate · {len(rules)} urmărite live")
    l2 = (state or {}).get("edge_status") and f"Edge recent: {state['edge_status']} · ultima rulare {state.get('last_run', '')}"
    rt = rep.get("rejected_top") or []
    l3 = (f"Cea mai apropiată respinsă: {rt[0].get('text')}" if rt and not acc else
          (f"Acceptată: {acc[0].get('text')}" if acc else None))
    return _lines(l1, l2, l3)


def similar(token_meta, narrative, symbol):
    if narrative and narrative.get("text"):
        return _lines(f"{symbol}: " + plain(narrative["text"])[:160])
    if token_meta:
        cats = token_meta.get("categories") or []
        return _lines(f"{symbol}: " + (", ".join(cats[:4]) if cats else "metadate disponibile"))
    return [f"Fără metadate pentru {symbol or 'candidatul curent'}"]


def weights(w, health, policy=None):
    if not w:
        return []
    l1 = (("Înghețate · " if (policy or {}).get("frozen") else "")
          + " · ".join(f"{k} {v:.2f}" for k, v in w.items() if isinstance(v, (int, float))))
    h = health or {}
    l2 = (f"{h.get('evaluated', 0)} semnale evaluate (minim {h.get('min_samples', '?')})"
          + (f" · hit-rate {h['hit_rate']}%" if h.get("hit_rate") is not None else "")
          + (f" · {h['status']}" if h.get("status") else ""))
    return _lines(l1, l2)


def evidence(plan_):
    if not plan_:
        return ["Niciun plan încă"]
    ev = plan_.get("evidence") or []
    d = plan_.get("direction")
    sup = [e for e in ev if e.get("direction") == d]
    opp = [e for e in ev if e.get("direction") not in (d, "NEUTRU")]
    fu = plan_.get("fusion") or {}
    l1 = (f"{plan_.get('symbol')} {d}: {len(sup)} pentru · {len(opp)} contra"
          + (f" · fuziune {fu['score']}" if fu.get("score") is not None else ""))
    strong = sorted(sup, key=lambda e: -(e.get("strength") or 0))[:2]
    l2 = strong and "Pentru: " + "; ".join(plain(e.get("label")) for e in strong)
    strong_o = sorted(opp, key=lambda e: -(e.get("strength") or 0))[:2]
    l3 = strong_o and "Contra: " + "; ".join(plain(e.get("label")) for e in strong_o)
    return _lines(l1, l2, l3)


def plan_memory(store):
    s = (store or {}).get("summary") or {}
    if not s:
        return []
    lv, bt = s.get("live") or {}, s.get("backtest") or {}
    l1 = lv.get("closed") and (f"Live: {lv['closed']} închise · {lv.get('win_rate')}% câștigătoare · "
                               f"{lv.get('avg_r', 0):+.3f}R/plan (din {lv.get('since', '?')})")
    l2 = bt.get("closed") and (f"Backtest: {bt['closed']} închise · {bt.get('win_rate')}% · "
                               f"{bt.get('avg_r', 0):+.3f}R/plan (din {bt.get('since', '?')})")
    l3 = f"Deschise acum: {s.get('open', 0)} · total planuri {s.get('total_plans', 0)}"
    return _lines(l1, l2, l3)


def calibration(store):
    cal = (store or {}).get("calibration") or {}
    rel = {b: e for b, e in cal.items() if e.get("reliable")}
    if not rel:
        return ["Se calibrează după primele planuri închise"]
    best = max(rel.items(), key=lambda kv: kv[1].get("avg_r") or 0)
    worst = min(rel.items(), key=lambda kv: kv[1].get("avg_r") or 0)
    f = lambda b, e: f"scor {b}-{int(b) + 19}: {e['win_rate']}% câștig, {e['avg_r']:+.2f}R"
    return _lines(f"{len(rel)} intervale de scor cu date suficiente",
                  "Cel mai bun: " + f(*best), "Cel mai slab: " + f(*worst))


def agent(a, audit=None):
    a = a or {}
    if not (a.get("agent") or {}).get("total"):
        return ["Agentul nu s-a antrenat încă"]
    lv = a.get("live") or {}
    l1 = (f"{a.get('status', '?')} · {a['agent']['total']} exemple · AUC {a['auc']:.3f} vs scor brut "
          f"{a.get('auc_score_baseline', 0):.3f}") if a.get("auc") is not None else f"{a.get('status')} · {a['agent']['total']} exemple"
    l2 = lv.get("auc") is not None and (f"Live: AUC {lv['auc']:.3f} (IC {lv.get('ci_low')}–{lv.get('ci_high')}) pe "
                                        f"{lv.get('n')} planuri · {lv.get('avg_r', 0):+.3f}R/plan")
    w = (a.get("model") or {}).get("weights") or {}
    top_w = sorted(((v, k) for k, v in w.items() if v), key=lambda t: -abs(t[0]))[:3]
    pend = a.get("pending_features") or []
    au = audit or {}
    if au.get("blocked"):
        l3 = f"⚠ Audit memorie blocat: {au['blocked']}"
    elif au.get("when"):
        l3 = (f"Audit memorie: {au.get('checked', 0)} planuri verificate, {len(au.get('removed') or [])} scoase"
              + (f" · în așteptare: {', '.join(pend)}" if pend else ""))
    else:
        l3 = ("Ponderi mari: " + ", ".join(f"{k} {v:+.2f}" for v, k in top_w)
              + (f" · în așteptare: {', '.join(pend)}" if pend else "")) if top_w else None
    return _lines(l1, l2, l3)


def learning(agent_state, health):
    c = ((agent_state or {}).get("curve") or [])
    h = health or {}
    l1 = c and (f"Acuratețe cumulativă: agent {c[-1]['agent']}% vs euristică {c[-1]['baseline']}% "
                f"(recent {c[-1].get('agent_recent', '?')}%)")
    l2 = c and f"{c[-1].get('n')} exemple · {len(c)} puncte pe curbă"
    l3 = f"Hit-rate semnale: {h['hit_rate']}% din {h.get('evaluated')}" if h.get("hit_rate") is not None else None
    return _lines(l1, l2, l3)


def sessions(session):
    s = session or {}
    return _lines(f"UTC acum {s.get('utc_time', '?')} · active: {', '.join(s.get('active') or []) or '-'}")


# --------------------------------------------------------------------- paginile burselor

def ex_state(card, scan, details, trained):
    if not card.get("connected"):
        return _lines("Indisponibilă: " + plain(card.get("error") or "motiv necunoscut")[:150])
    scan = scan or {}
    res = scan.get("results") or []
    nl = sum(1 for r in res if r.get("direction") == "LONG")
    l1 = ("Bursa activă: aici agentul deschide planuri și învață" if trained
          else "Analiză completă, decizie simulată (agentul nu învață de aici)")
    l2 = (f"{len(scan.get('resolved') or [])} tokeni · {len(res)} cu semnal ({nl} long, {len(res) - nl} short)"
          + (f" · lipsesc: {', '.join(scan['missing'])}" if scan.get("missing") else ""))
    l3 = " · ".join(x for x in [
        (details or {}).get("scan_time") and f"date din {details['scan_time']}",
        scan.get("error") and f"eroare: {plain(scan['error'])[:80]}",
        scan.get("truncated") and "listă trunchiată la limita de timp"] if x)
    return _lines(l1, l2, l3)


def ex_proposals(props, trained):
    props = props or {}
    if not props:
        return ["Nicio propunere la această scanare"]
    acts = {}
    for p in props.values():
        a = ("plan deschis" if p.get("open_plan") else (p.get("decision") or {}).get("action") or "?")
        acts[a] = acts.get(a, 0) + 1
    lbl = {"ISSUE": "emise", "SKIP": "refuzate"}
    l1 = f"{len(props)} propuneri" + (" (simulate)" if not trained else "") + ": " + ", ".join(
        f"{n} {lbl.get(a, a)}" for a, n in sorted(acts.items(), key=lambda kv: -kv[1]))
    best = sorted(props.items(), key=lambda kv: -(kv[1].get("score") or 0))[:3]
    l2 = "Scor maxim: " + ", ".join(f"{s.split('/')[0]} {p.get('direction')} {p.get('score')}" for s, p in best)
    return _lines(l1, l2)


def ex_tokens(watchlist, symbols, analyst_tokens):
    l1 = f"{len(watchlist or [])} tokeni · {len(symbols or {})} cu analiză completă (grafic, Elliott, zone, plan)"
    cnt = {}
    for s in symbols or {}:
        t = (analyst_tokens or {}).get(s.split("/")[0].upper())
        k = ((t or {}).get("strategy") or {}).get("sentiment")
        if k:
            cnt[k] = cnt.get(k, 0) + 1
    l2 = ("Analist: " + " · ".join(f"{cnt[k]} {k}" for k in ("Bullish", "Neutru", "Bearish") if cnt.get(k))) if cnt else None
    return _lines(l1, l2)


def altseason_strip(state):
    return altseason(state)[:2]
