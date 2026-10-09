# -*- coding: utf-8 -*-
"""core.analyst - analistul de rotatie a capitalului: raportul in patru sectiuni, la fiecare scanare.

  1. DIAGNOSTIC MACRO (ALT SEASON CHECK): dominanta BTC (directie si nivel cheie), faza rotatiei
     capitalului (cele 4 faze clasice, din clasificarea pe 9 faze validata pe 10 ani de istoric) si
     indicele Altcoin Season cu trendul pe 90 de zile - raportate la statistica ultimilor 10 ani;
  2. SECTOARE MOMENTUM: cel mult 3 naratiuni care bat BTC, cu motorul miscarii si durabilitatea ei;
  3. FILTRARE ALFA: proiectele care bat BTC, cu R:R din zonele de suport/rezistenta 1D si riscul
     critic MASURAT (diluare, lichiditate, dependenta de BTC);
  4. STRATEGIE EXECUTIVA: sentiment, ghid de intrare si trigger de invalidare (pret BTC, BTC.D).

REGULA: nicio fraza fara o cifra masurata in spate. "Catalizatorul" unui sector e motorul MASURAT
al miscarii (forta relativa fata de BTC, latimea), nu o stire - stirile nu au aici o sursa gratuita
si verificabila. Textele scurte respecta limita de 10 cuvinte din format. R:R e geometrie (cat
castigi la tinta fata de cat pierzi la stop), nu o probabilitate. Pur: fara retea si fara fisiere
(adaptorul e analyst.py, din radacina).
"""

import math
from datetime import datetime, timedelta, timezone

from core.analysis import daily_zones

WORDS = 10                  # limita textelor scurte din format
BTCD_MOVE_PP = 0.5          # directia dominantei: cel putin 0.5 pp pe 30 de zile
BTCD_TURN_PP = 0.3          # o miscare opusa pe 7 zile peste 0.3 pp = inflexiune -> consolidare
AI_TREND_PTS = 10.0         # indicele: +/-10 puncte pe 90 de zile = trend
SECTORS_MAX = 3
SECTOR_MIN_COINS = 5
SECTOR_MIN_BREADTH = 50.0   # un sector "bate BTC" doar daca macar jumatate din monedele lui o fac
STRUCT_BREADTH = 60.0
ALPHA_MAX = 5
ASYM_RR = 3.0               # R:R asimetric
STOP_PAD_ATR = 0.25         # stopul sub baza zonei de suport, la 0.25 ATR zilnic
NEAR_SUPPORT_ATR = 1.5      # "langa suport": cel mult 1.5 ATR zilnic deasupra zonei
DILUTION_CIRC = 0.6         # sub 60% din oferta in circulatie = deblocari importante
DILUTION_FDV = 1.6
LIQ_VOL_USD = 5e6
LIQ_VOL_MCAP = 0.02
BETA_HIGH = 1.5
CORR_HIGH = 0.6
SAME_ASSET_PCT = 25.0       # pretul de pe bursa la peste 25% de cel din CoinGecko = alt activ, acelasi ticker

PHASE4 = {0: (0, "Înainte de Faza 1 (piață bear)"), 1: (1, "Faza 1 (BTC Pump)"), 2: (1, "Faza 1 (BTC Pump)"),
          3: (2, "Faza 2 (Consolidare)"), 4: (3, "Faza 3 (Large Caps)"), 5: (4, "Faza 4 (Alt Season)"),
          6: (4, "Faza 4 (Alt Season)"), 7: (4, "Faza 4 (Alt Season)"), 8: (5, "După Faza 4 (distribuție)")}
PHASE4_SUB = {1: "BTC conduce recuperarea", 2: "BTC face mișcarea principală", 3: "BTC încetinește, capitalul intră în ETH",
              4: "large caps bat BTC", 5: "rotație spre mid caps", 6: "speculație small caps",
              7: "euforie: aproape orice crește", 8: "capitalul iese din risc", 0: "altcoins lovite, lichiditatea dispare"}


def _words(text, n=WORDS):
    return " ".join(str(text).split()[:n])


def _usd(v):
    if v is None:
        return "n/d"
    a = abs(v)
    if a >= 1000:
        return f"${v:,.0f}"
    if a >= 1:
        return f"${v:.2f}"
    if a >= 0.01:
        return f"${v:.4f}"
    return f"${v:.8f}"


def _rel(r, rb):
    if r is None or rb is None:
        return None
    return ((1 + r / 100) / (1 + rb / 100) - 1) * 100


def _median(v):
    v = sorted(x for x in v if x is not None)
    if not v:
        return None
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2


def _day(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


# ------------------------------------------------------------------ 1. DIAGNOSTIC MACRO
def btc_dominance(ind, series):
    """Directia dominantei BTC din seria ZILNICA reala (CoinGecko, salvata la fiecare scanare)
    si nivelul cheie din intervalul ei. Fara 30 de zile de serie, schimbarea pe 30 de zile vine
    din estimarea pe randamente (marcata ca estimata)."""
    cur = (ind or {}).get("btc_d")
    if cur is None:
        return {"status": None, "text": "dominanța BTC indisponibilă la această scanare (CoinGecko)"}
    days = sorted(series or {})

    def ago(n):
        """Valoarea de acum `n` zile (cea mai apropiata zi salvata, toleranta 2 zile inainte si 5
        dupa) - None daca seria nu ajunge atat de departe."""
        if len(days) < 2:
            return None
        target = _day(days[-1]) - timedelta(days=n)
        cands = [d for d in days[:-1] if _day(d) <= target + timedelta(days=2)]
        if not cands or _day(cands[-1]) < target - timedelta(days=5):
            return None
        return series[cands[-1]]

    v7, v30 = ago(7), ago(30)
    d7 = round(cur - v7, 2) if v7 is not None else None
    d30 = round(cur - v30, 2) if v30 is not None else None
    est = False
    if d30 is None and ind.get("btc_d_delta30") is not None:
        d30, est = round(ind["btc_d_delta30"], 2), True
    if d30 is None:
        status = "Consolidare"
    elif d30 >= BTCD_MOVE_PP and not (d7 is not None and d7 <= -BTCD_TURN_PP):
        status = "În creștere"
    elif d30 <= -BTCD_MOVE_PP and not (d7 is not None and d7 >= BTCD_TURN_PP):
        status = "Scădere"
    else:
        status = "Consolidare"
    last30 = [series[d] for d in days if _day(d) >= _day(days[-1]) - timedelta(days=30)] if days else []
    last90 = [series[d] for d in days if _day(d) >= _day(days[-1]) - timedelta(days=90)] if days else []
    span = (_day(days[-1]) - _day(days[0])).days + 1 if days else 0
    w30 = min(30, span)
    lo30, hi30 = (min(last30 + [cur]), max(last30 + [cur])) if span >= 5 else (None, None)
    span90 = span >= 60
    lo90, hi90 = (min(last90 + [cur]), max(last90 + [cur])) if span90 else (None, None)
    key = None
    if lo30 is None:
        key_text = "nivel cheie: seria zilnică are sub 5 zile - apare la scanările următoare"
    elif status == "În creștere":
        key = hi90 if span90 else hi30
        key_text = (f"rezistență {key:.2f}% (maximul pe {90 if span90 else w30} de zile): peste ea, "
                    "presiunea pe alts continuă")
    elif status == "Scădere":
        key = lo90 if span90 else lo30
        key_text = (f"suport {key:.2f}% (minimul pe {90 if span90 else w30} de zile): sub el, "
                    "rotația spre alts se confirmă")
    else:
        key_text = (f"interval {lo30:.2f}–{hi30:.2f}% pe {w30} de zile: peste {hi30:.2f}% presiune pe alts, "
                    f"sub {lo30:.2f}% rotație spre alts")
    mv = lambda v, s: "n/d" if v is None else f"{v:+.2f} pp{s}"
    text = (f"{status} · {cur:.2f}% · 7z {mv(d7, '')} · 30z {mv(d30, ' (estimat)' if est else '')} · {key_text}")
    return {"value": cur, "d7": d7, "d30": d30, "d30_estimated": est, "status": status, "lo30": lo30, "hi30": hi30,
            "window": w30, "lo90": lo90, "hi90": hi90, "key": key, "key_text": key_text, "days": len(days),
            "text": text}


def rotation_phase(cls, ind):
    """Faza rotatiei de capital (cele 4 faze clasice) din clasificarea pe 9 faze, cu regimul BTC
    si metricile relative care o sustin."""
    p = (cls or {}).get("phase")
    if p not in PHASE4:
        return {"n": None, "label": "nedeterminată", "text": "faza nu a putut fi evaluată la această scanare"}
    n, label = PHASE4[p]
    cy = (cls or {}).get("cycle") or {}
    g = (ind or {}).get
    facts = []
    if cy.get("dd") is not None:
        facts.append(f"BTC {cy['dd']:+.0f}% de la ATH")
    if cy.get("rec_from_low") is not None and cy.get("low"):
        facts.append(f"+{cy['rec_from_low']:.0f}% de la minim")
    if g("btc_d_delta30") is not None:
        facts.append(f"BTC.D {g('btc_d_delta30'):+.1f} pp/30z")
    if g("eth_btc_30d") is not None:
        facts.append(f"ETH/BTC {g('eth_btc_30d'):+.1f}%/30z")
    mr = g("med_rel30_tier") or {}
    if mr.get("large") is not None:
        facts.append(f"large caps {mr['large']:+.1f}% vs BTC/30z")
    note = PHASE4_SUB.get(p, "")
    if n == 1 and g("btc_r30") is not None and abs(g("btc_r30")) < 5:
        note += f"; BTC în consolidare ({g('btc_r30'):+.1f}% pe 30 de zile)"
    reg = (cls or {}).get("regime_name")
    text = (f"{label} — {note}" + (f" · regim {reg.lower()}" if reg else "") + (" · " + ", ".join(facts) if facts else "")
            + f" · clasificare pe 9 faze: {p} ({(cls or {}).get('name')}), încredere {((cls or {}).get('confidence') or 0) * 100:.0f}%")
    if (cls or {}).get("rotation"):
        text += " · rotație în interiorul regimului, nu altseason de ciclu"
    return {"n": n, "label": label, "phase9": p, "note": note, "regime": (cls or {}).get("regime"), "text": text}


def alt_index(ind, hist, live=None):
    """Indicele Altcoin Season de acum (definitia CoinMarketCap: cate din top 100 altcoins bat BTC
    pe 90 de zile) si trendul lui pe 90 de zile. Trendul vine din seria LIVE salvata la fiecare
    scanare cand ea acopera 90 de zile; pana atunci, din indicele istoric comparabil (seria zilnica
    reconstruita pe 10 ani, pe care se calculeaza si percentila) - marcat ca atare, pentru ca are
    alt univers de monede si valoarea lui de azi difera de cea live."""
    v, cov = (ind or {}).get("alt_index_90d"), (ind or {}).get("alt_index_coverage")
    pos = (hist or {}).get("position") or {}
    days = sorted(d for d, x in (live or {}).items() if x is not None)
    src, series = "istoric", pos.get("ai_trend90") or []
    a0, a1 = pos.get("ai_90d_ago"), pos.get("ai_now_hist")
    if days and (_day(days[-1]) - _day(days[0])).days >= 85:
        old = [d for d in days if _day(d) <= _day(days[-1]) - timedelta(days=88)] or days[:1]
        src, a0, a1 = "live", live[old[-1]], live[days[-1]]
        tail = [d for d in days if _day(d) >= _day(days[-1]) - timedelta(days=90)]
        series = [[d, live[d]] for d in tail[::7]] + ([[tail[-1], live[tail[-1]]]] if (len(tail) - 1) % 7 else [])
    delta = round(a1 - a0, 1) if a0 is not None and a1 is not None else None
    trend = ("în creștere" if delta is not None and delta >= AI_TREND_PTS else
             "în scădere" if delta is not None and delta <= -AI_TREND_PTS else "lateral" if delta is not None else "n/d")
    season = ("Altcoin Season" if (v or 0) >= 75 else "Bitcoin Season" if v is not None and v <= 25 else "zonă mixtă")
    parts = [f"{v:.0f}/100 ({season}" + (f", {cov} din top 100 altcoins" if cov else "") + ")" if v is not None
             else "indice indisponibil (prea puține randamente pe 90 de zile)"]
    if delta is not None:
        parts.append(f"trend 90 de zile: {a0:.0f} → {a1:.0f} ({delta:+.0f}, {trend})"
                     + ("" if src == "live" else " pe indicele istoric comparabil"))
    if pos.get("ai_percentile") is not None:
        parts.append(f"percentila {pos['ai_percentile']} din 10 ani (indicele istoric comparabil)")
    return {"value": v, "coverage": cov, "season": season, "from": a0, "to": a1, "delta": delta, "trend": trend,
            "trend_source": src, "series": series, "percentile": pos.get("ai_percentile"), "text": " · ".join(parts)}


def ten_year(hist, btcd):
    """Unde e piata de acum fata de ultimii 10 ani: probabilitatea unui altseason in 90 de zile
    (cu puterea ei predictiva masurata), durata fazei, directia BTC.D in altseason-urile trecute,
    pozitia in ciclul BTC si ce a urmat in ciclurile trecute in acelasi punct."""
    if not hist or not hist.get("position"):
        return {"lines": ["istoricul pe 10 ani se reconstruiește la următoarea scanare"], "p_alt90": None}
    pos, pred, lr = hist["position"], hist.get("prediction") or {}, hist.get("learn") or {}
    lines = []
    if pred.get("p") is not None:
        sk = lr.get("skill_analog")
        lines.append(f"Probabilitatea ca indicele să atingă 75 în 90 de zile: {pred['p'] * 100:.0f}% "
                     f"(frecvența istorică {(lr.get('base_rate') or 0) * 100:.0f}%"
                     + (f"; analogiile bat frecvența cu {sk * 100:+.0f}% pe {lr.get('evaluations')} momente testate"
                        if sk is not None else "") + ")")
    if pos.get("phase_days") is not None:
        lines.append(f"Faza curentă durează de {pos['phase_days']} zile (mediana istorică a fazei: "
                     f"{pos.get('phase_median_days')} zile)")
    if pos.get("alt_days_btcd_falling_pct") is not None:
        lines.append(f"În cele {pos.get('alt_days')} zile de altseason din 10 ani, BTC.D scădea pe 30 de zile în "
                     f"{pos['alt_days_btcd_falling_pct']}% din zile (în restul zilelor: "
                     f"{pos.get('other_days_btcd_falling_pct')}%) · acum: {(btcd or {}).get('status') or 'n/d'}")
    cyc = (hist.get("cycles") or [{}])[-1]
    if pos.get("days_since_top") is not None:
        lines.append(f"BTC: {pos['days_since_top']} zile de la vârful ciclului ({cyc.get('top', '')})"
                     + (f", {pos['days_since_low']} de la minim (+{pos.get('up_from_low')}%)"
                        if pos.get("days_since_low") is not None else ""))
    past = [c for c in pos.get("past_cycles") or [] if c.get("first_altseason_after_low")]
    if past:
        lines.append("Ciclurile trecute, după minim: primul altseason în "
                     + ", ".join(c["first_altseason_after_low"][:7] for c in past))
    return {"lines": lines, "p_alt90": pred.get("p"), "base_rate": lr.get("base_rate"),
            "skill": lr.get("skill_analog"), "last_day": hist.get("last_day")}


# ------------------------------------------------------------------ 2. SECTOARE
def sector_momentum(sectors, ind):
    """Forta relativa fata de BTC a fiecarui sector (mediana monedelor lui pe 7/30/200 de zile) si
    latimea (cate monede bat BTC pe 30 de zile). Un sector intra in raport doar daca mediana bate
    BTC pe 30 de zile SI macar jumatate din monede o fac - altfel e miscarea a 1-2 monede."""
    g = (ind or {}).get
    rb7, rb30, rb200 = g("btc_r7"), g("btc_r30"), g("btc_r200")
    items = ((sectors or {}).get("items") or {})
    rows = []
    for cid, it in items.items():
        coins = [c for c in it.get("coins") or [] if c.get("r30") is not None]
        if len(coins) < SECTOR_MIN_COINS or rb30 is None:
            continue
        rs30 = [(_rel(c["r30"], rb30), c) for c in coins]
        rs7 = [_rel(c.get("r7"), rb7) for c in coins]
        rs200 = [_rel(c.get("r200"), rb200) for c in coins]
        breadth = 100 * sum(1 for v, _ in rs30 if v > 0) / len(rs30)
        m30, m7, m200 = _median([v for v, _ in rs30]), _median(rs7), _median(rs200)
        lead = sorted(rs30, key=lambda t: -t[0])[:3]
        rows.append({"id": cid, "name": it.get("name") or cid, "n": len(coins), "rs30": round(m30, 1),
                     "rs7": round(m7, 1) if m7 is not None else None,
                     "rs200": round(m200, 1) if m200 is not None else None, "breadth": round(breadth),
                     "score": round(m30 + 0.5 * (m7 or 0), 2), "when": it.get("when"),
                     "leaders": [{"symbol": c["symbol"], "rs30": round(v, 1), "name": c.get("name")} for v, c in lead]})
    rows.sort(key=lambda s: -s["score"])
    top = [dict(s) for s in rows if s["rs30"] > 0 and s["breadth"] >= SECTOR_MIN_BREADTH][:SECTORS_MAX]
    for s in top:
        s["catalyst"] = _words(f"RS {s['rs30']:+.0f}% vs BTC/30z; {s['breadth']}% monede bat BTC")
        structural = (s["rs200"] is not None and s["rs200"] > 0 and s["breadth"] >= STRUCT_BREADTH)
        s["sustain"] = "Trend structural" if structural else "Hype pe termen scurt"
        s["sustain_why"] = (f"mediana vs BTC: 7z {s['rs7']:+.0f}%, 30z {s['rs30']:+.0f}%, 200z "
                            + (f"{s['rs200']:+.0f}%" if s["rs200"] is not None else "n/d")
                            + f" · {s['breadth']}% din {s['n']} monede bat BTC pe 30 de zile")
    return {"top": top, "all": rows, "covered": len(rows), "of": (sectors or {}).get("total") or len(items)}


# ------------------------------------------------------------------ 3. FILTRARE ALFA
def _returns(candles):
    return {c[0]: (c[4] / p[4] - 1) for p, c in zip(candles, candles[1:]) if p[4]}


def btc_link(candles, btc_candles, days=90):
    """Beta si corelatia randamentelor zilnice fata de BTC (aceleasi zile), pe ultimele `days`."""
    if not candles or not btc_candles:
        return None, None
    a, b = _returns(candles[-days - 1:]), _returns(btc_candles[-days - 1:])
    common = sorted(set(a) & set(b))
    if len(common) < 30:
        return None, None
    x = [b[t] for t in common]
    y = [a[t] for t in common]
    mx, my = sum(x) / len(x), sum(y) / len(y)
    vx = sum((v - mx) ** 2 for v in x)
    vy = sum((v - my) ** 2 for v in y)
    cov = sum((u - mx) * (v - my) for u, v in zip(x, y))
    if vx <= 0 or vy <= 0:
        return None, None
    return round(cov / vx, 2), round(cov / math.sqrt(vx * vy), 2)


def _risk(m, beta, corr):
    """Riscul critic MASURAT: diluare (oferta in circulatie, FDV/capitalizare), lichiditate (volum)
    si dependenta de BTC (beta, corelatie). Cel mai sever castiga; restul raman in detalii."""
    out = []
    circ, total, mcap, fdv, vol = m.get("circ"), m.get("total") or m.get("max"), m.get("mcap"), m.get("fdv"), m.get("vol")
    cr = (circ / total) if circ and total else None
    fm = (fdv / mcap) if fdv and mcap else None
    if (cr is not None and cr < DILUTION_CIRC) or (fm is not None and fm >= DILUTION_FDV):
        sev = max(1 - (cr if cr is not None else 1), ((fm or 1) - 1) / 3)
        txt = "Diluare: " + ", ".join(([f"{cr * 100:.0f}% în circulație"] if cr is not None else [])
                                      + ([f"FDV/MC {fm:.1f}x"] if fm is not None else [])) + "; deblocări viitoare"
        out.append((sev + 0.2, _words(txt)))
    vm = (vol / mcap) if vol and mcap else None
    if (vol is not None and vol < LIQ_VOL_USD) or (vm is not None and vm < LIQ_VOL_MCAP):
        out.append((0.6 if (vol or 0) < LIQ_VOL_USD else 0.4,
                    _words(f"Lichiditate scăzută: volum 24h ${(vol or 0) / 1e6:.1f}M ({(vm or 0) * 100:.1f}% din cap.)")))
    if beta is not None and corr is not None and beta >= BETA_HIGH and corr >= CORR_HIGH:
        out.append((0.3 + min(0.4, (beta - BETA_HIGH) / 2), _words(f"Dependent de BTC: β {beta:.1f}, corelație {corr:.2f}")))
    if not out:
        info = {"circ_ratio": cr, "fdv_mcap": fm, "vol_mcap": vm}
        if cr is None and fm is None and vol is None and beta is None:
            return "Date de risc indisponibile: ofertă, volum, β", info
        return "Fără risc critic măsurat; rămâne riscul de regim BTC", info
    out.sort(key=lambda t: -t[0])
    return out[0][1], {"circ_ratio": cr and round(cr, 3), "fdv_mcap": fm and round(fm, 2), "vol_mcap": vm and round(vm, 4),
                       "all": [t[1] for t in out]}


def alpha_symbols(alt, details, sectors_top):
    """Tickerele candidate, in ordinea prioritatii: tokenii scanati care bat BTC, candidatii de
    crestere din piata, liderii sectoarelor cu momentum. Adaptorul aduce lumanarile zilnice."""
    out = []
    markets = (alt or {}).get("markets") or {}
    ind = (alt or {}).get("indicators") or {}
    for sym in details or {}:
        base = sym.split("/")[0].upper()
        m = markets.get(base) or {}
        if (_rel(m.get("r30"), ind.get("btc_r30")) or -1) > 0 and (_rel(m.get("r7"), ind.get("btc_r7")) or -1) > 0:
            out.append(base)
    out += [c["symbol"].upper() for c in (alt or {}).get("candidates") or []]
    for s in sectors_top or []:
        out += [x["symbol"].upper() for x in s.get("leaders", [])[:2]]
    seen = set()
    return [b for b in out if not (b in seen or seen.add(b)) and b not in ("BTC", "USDT", "USDC")]


def alpha(alt, details, daily, sectors_top, btc_daily):
    """Proiectele cu semne de outperformance (bat BTC pe 7 SI pe 30 de zile), cu R:R din zonele
    1D: intrare la pretul curent, stop sub baza celui mai apropiat suport (0.25 ATR zilnic), tinta
    la marginea de jos a celei mai apropiate rezistente. Asimetric = R:R >= 3."""
    ind = (alt or {}).get("indicators") or {}
    markets = dict((alt or {}).get("markets") or {})
    for s in ((alt or {}).get("sectors") or {}).get("items", {}).values():
        for c in s.get("coins") or []:
            markets.setdefault(c["symbol"].upper(), c)
    det_by_base = {sym.split("/")[0].upper(): (sym, d) for sym, d in (details or {}).items()}
    src_of = {}
    for c in (alt or {}).get("candidates") or []:
        src_of.setdefault(c["symbol"].upper(), "piață (top 250)")
    for s in sectors_top or []:
        for x in s.get("leaders", [])[:2]:
            src_of.setdefault(x["symbol"].upper(), f"lider {s['name']}")
    cand = {c["symbol"].upper(): c for c in (alt or {}).get("candidates") or []}
    rows = []
    for base in alpha_symbols(alt, details, sectors_top):
        m = markets.get(base) or {}
        rs7, rs30 = _rel(m.get("r7"), ind.get("btc_r7")), _rel(m.get("r30"), ind.get("btc_r30"))
        rs200 = _rel(m.get("r200"), ind.get("btc_r200"))
        if (rs7 is None or rs30 is None) and base in cand:
            # candidatii de crestere au deja forta relativa fata de BTC (altseason.growth_candidates)
            rs7, rs30 = cand[base].get("rs7"), cand[base].get("rs30")
            m = {"name": cand[base].get("name"), "rank": cand[base].get("rank"), **m}
        if rs7 is None or rs30 is None or rs7 <= 0 or rs30 <= 0:
            continue
        sym, d = det_by_base.get(base, (None, None))
        candles = (daily or {}).get(base)
        other = False
        if not d and candles and m.get("price") and abs(candles[-1][4] / m["price"] - 1) > SAME_ASSET_PCT / 100:
            # acelasi ticker, alt activ: pe bursa pretul difera mult de cel din CoinGecko - zonele,
            # R:R si beta ar fi calculate pe alta moneda, deci nu se folosesc
            candles, other = None, True
        price = (d or {}).get("price") or m.get("price") or (candles[-1][4] if candles else None)
        if d:
            z = ((d.get("zones") or {}).get("d1")) or {}
        else:
            z = daily_zones(candles, price) if candles else {}
        sup, res, atr = z.get("support"), z.get("resistance"), z.get("atr")
        stop = (sup["lo"] - STOP_PAD_ATR * atr) if (sup and atr) else None
        target = res["lo"] if res else None
        if target is None and candles:
            hi = max(c[2] for c in candles[-120:])
            target = hi if price and hi > price * 1.02 else None
        rr = None
        if price and stop and target and price > stop and target > price:
            rr = round((target - price) / (price - stop), 2)
        near = bool(sup and atr and price and price - sup["hi"] <= NEAR_SUPPORT_ATR * atr)
        beta, corr = btc_link(candles, btc_daily)
        risk, rinfo = _risk(m, beta, corr)
        if rr is not None:
            arg = f"Bate BTC {rs30:+.0f}%/30z; R:R {rr:.1f} din suport 1D"
        else:
            arg = f"Bate BTC {rs30:+.0f}%/30z și {rs7:+.0f}%/7z" + ("; lângă suport" if near else "")
        sig = None
        if d:
            sig = {"direction": d.get("direction"), "score": d.get("score")}
        rows.append({"ticker": base, "name": m.get("name"), "rank": m.get("rank"), "other_asset": other,
                     "has_daily": bool(candles) or bool(d),
                     "source": "listă scanată" if d else src_of.get(base, "piață"),
                     "rs7": round(rs7, 1), "rs30": round(rs30, 1), "rs200": round(rs200, 1) if rs200 is not None else None,
                     "price": price, "support": sup, "resistance": res, "atr_d": atr, "stop": stop, "target": target,
                     "rr": rr, "asymmetric": bool(rr is not None and rr >= ASYM_RR), "near_support": near,
                     "beta": beta, "corr": corr, "risk_info": rinfo, "signal": sig,
                     "argument": _words(arg), "risk": _words(risk)})
    # ordinea: R:R asimetric intai, apoi proiectele cu R:R calculat (au zone 1D), apoi restul -
    # in fiecare grup dupa R:R, respectiv dupa forta relativa pe 30 de zile
    rows.sort(key=lambda r: (not r["asymmetric"], r["rr"] is None,
                             -(r["rr"] or 0) if r["asymmetric"] else -r["rs30"]))
    return rows[:ALPHA_MAX]


# ------------------------------------------------------------------ 4. STRATEGIE
def btc_context(btc_daily, price=None):
    """BTC pe 1D: mediile de 50 si 200 de zile, zonele de suport/rezistenta si seria pentru grafic."""
    if not btc_daily or len(btc_daily) < 30:
        return {}
    closes = [c[4] for c in btc_daily]
    px = price or closes[-1]

    def sma(n):
        return [None if i + 1 < n else sum(closes[i + 1 - n:i + 1]) / n for i in range(len(closes))]
    ma200, ma50 = sma(200), sma(50)
    z = daily_zones(btc_daily, px)
    keep = 120
    rnd = lambda v: None if v is None else round(v, 2)
    return {"price": px, "ma200": rnd(ma200[-1]), "ma50": rnd(ma50[-1]), "zones": z,
            "support": z.get("support"), "resistance": z.get("resistance"), "atr": z.get("atr"),
            "chart": {"candles": [[c[0]] + [round(x, 2) for x in c[1:5]] + [round(c[5] or 0, 2)]
                                  for c in btc_daily[-keep:]],
                      "ma200": [rnd(v) for v in ma200[-keep:]], "ma50": [rnd(v) for v in ma50[-keep:]]}}


def strategy(cls, ind, btcd, ai, sectors, btc, phase):
    """Sentimentul, ghidul de intrare si triggerul de invalidare, din reguli explicite:
    BEARISH - regim bear/distributie sau BTC sub media de 200 de zile; BULLISH - piata bull, sau
    recuperare peste media de 200 cu BTC.D in scadere si indicele >= 50 in crestere; altfel NEUTRU.
    DCA doar in fazele 3-4, cu sentiment ne-bearish si sectoare cu trend structural."""
    regime = (cls or {}).get("regime")
    price = (btc or {}).get("price") or (ind or {}).get("btc_price")
    # media de 200 de zile: intai cea din contextul ciclului (istoricul pe 10 ani, aceeasi care
    # decide regimul), apoi cea din lumanarile zilnice ale bursei
    ma200 = ((cls or {}).get("cycle") or {}).get("ma200") or (btc or {}).get("ma200")
    sup, res = (btc or {}).get("support"), (btc or {}).get("resistance")
    why = []
    below_ma = bool(price and ma200 and price < ma200)
    if regime in ("bear", "markdown") or below_ma:
        sentiment = "Bearish"
        why.append(f"regim {(cls or {}).get('regime_name') or regime}" if regime in ("bear", "markdown")
                   else f"BTC {_usd(price)} sub media de 200 de zile {_usd(ma200)}")
    elif regime == "bull" or (price and ma200 and price > ma200 and (btcd or {}).get("status") == "Scădere"
                              and (ai or {}).get("value") is not None and ai["value"] >= 50
                              and (ai or {}).get("trend") == "în creștere"):
        sentiment = "Bullish"
        why.append("piață bull BTC" if regime == "bull" else
                   "BTC peste media de 200 de zile, BTC.D în scădere, indicele ≥ 50 în creștere")
    else:
        sentiment = "Neutru"
        if price and ma200:
            why.append(f"BTC {_usd(price)} peste media de 200 de zile {_usd(ma200)}")
        why.append(f"BTC.D {((btcd or {}).get('status') or 'n/d').lower()}")
        if (ai or {}).get("value") is not None:
            why.append(f"indicele altseason {ai['value']:.0f}/100 ({(ai or {}).get('trend')})")
    structural = [s["name"] for s in (sectors or {}).get("top") or [] if s.get("sustain") == "Trend structural"]
    if (phase or {}).get("n") in (3, 4) and sentiment != "Bearish" and structural:
        guide = "DCA în sectoare puternice: " + ", ".join(structural)
        guide_kind = "DCA"
    else:
        lvl = []
        if res:
            lvl.append(f"BTC închidere zilnică peste {_usd(res['hi'])} (rezistența 1D {_usd(res['lo'])}–{_usd(res['hi'])})")
        if (btcd or {}).get("lo30") is not None:
            lvl.append(f"BTC.D sub {btcd['lo30']:.2f}% (minimul pe 30 de zile)")
        guide = "Așteptare confirmare breakout" + (": " + " sau ".join(lvl) if lvl else "")
        guide_kind = "WAIT"
    # INVALIDAREA: baza celui mai apropiat suport 1D al BTC (cand e la cel mult 15%), altfel media
    # de 200 de zile; pentru BTC.D, maximul pe 30 de zile (peste el, capitalul revine in BTC).
    inv_price, inv_why = None, None
    if sup and price and (price - sup["lo"]) / price <= 0.15:
        inv_price, inv_why = sup["lo"], f"baza suportului 1D {_usd(sup['lo'])}–{_usd(sup['hi'])}"
    elif ma200:
        inv_price, inv_why = ma200, "media de 200 de zile (sub ea: regim bear)"
    inv_btcd = round((btcd or {}).get("hi30") + 0.2, 2) if (btcd or {}).get("hi30") is not None else None
    parts = []
    if inv_price:
        parts.append(f"BTC închidere zilnică sub {_usd(inv_price)} ({inv_why})")
    if inv_btcd is not None:
        parts.append(f"BTC.D peste {inv_btcd:.2f}% (maximul pe 30 de zile + 0.2 pp)")
    return {"sentiment": sentiment, "why": why, "guide": guide, "guide_kind": guide_kind,
            "invalidation": " sau ".join(parts) if parts else "nivelurile BTC indisponibile la această scanare",
            "inv_price": inv_price, "inv_btcd": inv_btcd}


# ------------------------------------------------------------------ RAPORTUL
def build_report(alt, details, daily, zone_stats, now_ts):
    """Raportul complet. `alt` = starea altseason (altseason.json), `details` = detaliile tokenilor
    scanati (cu zonele), `daily` = {ticker: lumanari zilnice INCHISE} (BTC si candidatii),
    `zone_stats` = rata masurata de respectare a zonelor (core.zones.hold_stats)."""
    alt = alt or {}
    ind, cls, hist = alt.get("indicators") or {}, alt.get("classification") or {}, alt.get("history") or {}
    btc_daily = (daily or {}).get("BTC")
    btc = btc_context(btc_daily, ind.get("btc_price"))
    btcd = btc_dominance(ind, alt.get("btc_d_daily"))
    phase = rotation_phase(cls, ind)
    ai = alt_index(ind, hist, alt.get("alt_index_daily"))
    tenyr = ten_year(hist, btcd)
    sectors = sector_momentum(alt.get("sectors"), ind)
    projects = alpha(alt, details, daily, sectors["top"], btc_daily)
    strat = strategy(cls, ind, btcd, ai, sectors, btc, phase)
    now = datetime.fromtimestamp(now_ts, tz=timezone.utc)
    age_min = round((now_ts - (alt.get("ts") or 0)) / 60) if alt.get("ts") else None
    last_day = hist.get("last_day")
    lag = (now.date() - _day(last_day)).days if last_day else None
    fresh = {"alt_when": alt.get("when"), "alt_age_min": age_min, "alt_stale": bool(alt.get("stale")),
             # evaluata in aceeasi scanare: cel mult 90 de minute MAI VECHE decat raportul
             "same_scan": bool(age_min is not None and age_min <= 90 and not alt.get("stale")),
             "history_last_day": last_day, "history_lag_days": lag,
             "history_ok": bool(lag is not None and lag <= 2),
             "perf90_day": (alt.get("perf90") or {}).get("day"),
             "sectors_covered": sectors["covered"], "sectors_total": sectors["of"]}
    return {"ts": now_ts, "when": now.strftime("%Y-%m-%d %H:%M UTC"), "fresh": fresh,
            "macro": {"btc_d": btcd, "phase": phase, "alt_index": ai, "ten_year": tenyr},
            "sectors": sectors, "alpha": projects, "strategy": strat, "btc": btc,
            "zone_stats": zone_stats}
