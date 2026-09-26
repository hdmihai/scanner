#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
elliott.py
===========
Numaratoare Elliott cu ipoteze CONCURENTE, fiecare cu increderea si nivelul ei
de invalidare - forma din capturile de referinta (Primary / Alternative /
Secondary, fiecare cu procent si cu "E INV").

DE CE MAI MULTE NUMARATORI, SI NU UNA
--------------------------------------
Elliott e ambiguu prin constructie: aceeasi serie de preturi admite mai multe
etichetari valide. Un sistem care afiseaza O SINGURA numaratoare ascunde acea
ambiguitate si da o falsa impresie de certitudine. Capturile de referinta arata
corect 3-4 ipoteze simultan, fiecare cu increderea ei. Fac la fel.

CE FACE INCREDEREA SA FIE UN NUMAR, NU O PARERE
------------------------------------------------
Increderea nu e aleasa; se compune din doua lucruri masurabile:
  1. REGULILE DURE, care sunt binare si elimina complet o numaratoare:
     - Unda 2 nu retrage niciodata peste 100% din unda 1
     - Unda 3 nu e niciodata cea mai scurta dintre 1, 3 si 5
     - Unda 4 nu intra in teritoriul undei 1
     O numaratoare care incalca oricare dintre ele nu primeste incredere mica,
     ci e ELIMINATA. Asta e diferenta dintre reguli si preferinte.
  2. PROPORTIILE FIBONACCI, care sunt tendinte, nu reguli: cat de aproape sunt
     retragerile si extensiile de valorile tipice (0.5/0.618 pentru unda 2,
     1.618 pentru unda 3, 0.382 pentru unda 4).

NIVELUL DE INVALIDARE
---------------------
Fiecare numaratoare spune la ce pret devine imposibila. E singura parte cu
adevarat actionabila: daca pretul il atinge, ipoteza e moarta, indiferent cat
de convingatoare parea. Il calculez din regula care s-ar incalca prima.

Totul din OHLCV, deci exista identic in live si in backtest.
"""

# Proportiile tipice, cu toleranta. Nu sunt reguli - o unda care nu le respecta
# ramane valida, doar primeste incredere mai mica.
FIB_W2 = (0.382, 0.5, 0.618, 0.786)
FIB_W3 = (1.618, 2.618)
FIB_W4 = (0.236, 0.382, 0.5)
TOLERANCE = 0.12


def find_pivots(highs, lows, left=3, right=3):
    """Varfuri si funduri confirmate.

    `right` bare la dreapta trebuie sa existe ca pivotul sa fie confirmat -
    altfel as eticheta drept varf o bara care inca nu s-a dovedit. Asta e
    diferenta dintre "CONFIRMED" si "FORMING" din capturi.
    """
    piv = []
    n = len(highs)
    for i in range(left, n - right):
        window_h = highs[i - left:i + right + 1]
        window_l = lows[i - left:i + right + 1]
        if highs[i] == max(window_h) and highs[i] > highs[i - 1]:
            piv.append({"idx": i, "price": highs[i], "type": "H"})
        elif lows[i] == min(window_l) and lows[i] < lows[i - 1]:
            piv.append({"idx": i, "price": lows[i], "type": "L"})
    # PUNCTUL DE START. find_pivots incepe de la indicele `left`, deci pierde
    # inceputul seriei - exact punctul pe care capturile il numesc MAJOR START.
    # Fara el, o secventa de 6 pivoti incepe de la primul VARF, iar etichetarea
    # ca impuls 1-2-3-4-5 devine imposibila: lipseste originea undei 1.
    if piv:
        first = piv[0]
        head_h, head_l = highs[:first["idx"]], lows[:first["idx"]]
        if first["type"] == "H" and head_l:
            lo = min(head_l)
            piv.insert(0, {"idx": head_l.index(lo), "price": lo, "type": "L"})
        elif first["type"] == "L" and head_h:
            hi = max(head_h)
            piv.insert(0, {"idx": head_h.index(hi), "price": hi, "type": "H"})

    # alternez H/L: doua varfuri consecutive fara fund intre ele nu au sens
    clean = []
    for p in piv:
        if clean and clean[-1]["type"] == p["type"]:
            better = (p["price"] > clean[-1]["price"]) if p["type"] == "H" \
                else (p["price"] < clean[-1]["price"])
            if better:
                clean[-1] = p
            continue
        clean.append(p)
    return clean


def _label_points(pivots, labels, prices, confirm_bars=3):
    """Eticheteaza punctele undelor si marcheaza care sunt CONFIRMATE.

    Distinctia din capturile de referinta - "W1 CONFIRMED" fata de "W3 FORMING"
    - nu e cosmetica. Un pivot e confirmat abia dupa ce urmeaza destule bare
    care nu l-au depasit; pana atunci pretul inca poate merge mai departe si
    varful se muta. A eticheta un varf neconfirmat drept unda incheiata
    inseamna a pretinde certitudine care nu exista inca.

    Ultimul punct al unei structuri e aproape mereu neconfirmat, fiindca e cel
    mai recent - de asta in capturi ultima unda apare ca FORMING.
    """
    if not pivots:
        return []
    last_idx = max(p["idx"] for p in pivots)
    out = []
    for i, (lbl, price) in enumerate(zip(labels, prices)):
        if i >= len(pivots):
            break
        idx = pivots[i]["idx"]
        confirmed = (last_idx - idx) >= confirm_bars
        out.append({"label": lbl, "price": price, "idx": idx,
                    "confirmed": confirmed,
                    "display": lbl if lbl == "MAJOR START"
                               else f"{lbl} CONFIRMED" if confirmed
                               else f"{lbl} FORMING"})
    return out


def _fib_score(ratio, targets, tol=TOLERANCE):
    """Cat de aproape e un raport de cea mai apropiata proportie tipica."""
    if ratio is None or ratio <= 0:
        return 0.0
    best = min(abs(ratio - t) / t for t in targets)
    return max(0.0, 1.0 - best / tol) if best < tol else 0.0


def _impulse(points):
    """Verifica o secventa de 6 pivoti ca impuls in 5 unde.

    Returneaza None daca incalca o REGULA DURA - nu o incredere mica. O
    numaratoare invalida nu e o numaratoare slaba; nu e o numaratoare deloc.
    """
    if len(points) < 6:
        return None
    p0, p1, p2, p3, p4, p5 = [p["price"] for p in points[:6]]
    up = p1 > p0

    w1 = abs(p1 - p0)
    w2 = abs(p2 - p1)
    w3 = abs(p3 - p2)
    w4 = abs(p4 - p3)
    w5 = abs(p5 - p4)
    if min(w1, w3, w5) <= 0:
        return None

    # REGULA 1: unda 2 nu retrage peste 100% din unda 1
    if w2 >= w1:
        return None
    # REGULA 2: unda 3 nu e cea mai scurta dintre impulsuri
    if w3 < w1 and w3 < w5:
        return None
    # REGULA 3: unda 4 nu intra in teritoriul undei 1
    if up and p4 <= p1:
        return None
    if not up and p4 >= p1:
        return None

    prop = (_fib_score(w2 / w1, FIB_W2)
            + _fib_score(w3 / w1, FIB_W3)
            + _fib_score(w4 / w3, FIB_W4)) / 3.0
    # alternanta: undele 2 si 4 ar trebui sa difere ca amplitudine
    alt = 1.0 if abs((w2 / w1) - (w4 / w3)) > 0.15 else 0.4
    conf = 0.45 + 0.40 * prop + 0.15 * alt

    return {
        "pattern": "IMPULS 1-2-3-4-5",
        "direction": "LONG" if up else "SHORT",
        "confidence": round(min(conf, 0.95), 4),
        # invalidarea: capatul undei 1, unde regula 3 s-ar incalca
        "invalidation": p1,
        "labels": [("MAJOR START", p0), ("W1", p1), ("W2", p2),
                   ("W3", p3), ("W4", p4), ("W5", p5)],
        "points": _label_points(points[:6],
                                ["MAJOR START", "W1", "W2", "W3", "W4", "W5"],
                                [p0, p1, p2, p3, p4, p5]),
        "targets": _impulse_targets(p0, p1, p2, p3, p4, up),
    }


def _impulse_targets(p0, p1, p2, p3, p4, up):
    """Tintele undei 5, proiectate Fibonacci din unda 1 si unda 3."""
    w1 = abs(p1 - p0)
    sign = 1 if up else -1
    return {
        "tp1": p4 + sign * w1 * 0.618,
        "tp2": p4 + sign * w1 * 1.0,
        "tp3": p4 + sign * w1 * 1.618,
    }


def _zigzag(points):
    """Corectie A-B-C. Regula dura: B nu retrage complet A."""
    if len(points) < 4:
        return None
    p0, pa, pb, pc = [p["price"] for p in points[:4]]
    a = abs(pa - p0)
    b = abs(pb - pa)
    c = abs(pc - pb)
    if a <= 0 or b >= a:          # B peste 100% din A -> nu e zigzag
        return None
    down = pa < p0                # A descendent = corectie intr-un trend sus

    prop = (_fib_score(b / a, (0.5, 0.618, 0.786))
            + _fib_score(c / a, (0.618, 1.0, 1.618))) / 2.0
    conf = 0.40 + 0.45 * prop
    return {
        "pattern": "CORECTIE A-B-C",
        # dupa o corectie descendenta completa, directia asteptata e in sus
        "direction": "LONG" if down else "SHORT",
        "confidence": round(min(conf, 0.92), 4),
        "invalidation": p0,
        "points": _label_points(points[:4], ["MAJOR START", "A", "B", "C"],
                                [p0, pa, pb, pc]),
        "targets": {"tp1": pc + (1 if down else -1) * a * 0.618,
                    "tp2": pc + (1 if down else -1) * a * 1.0},
    }


def _abcd(points):
    """Armonica AB=CD, cu zona de inversare PRZ."""
    if len(points) < 4:
        return None
    pa, pb, pc, pd = [p["price"] for p in points[:4]]
    ab = abs(pb - pa)
    cd = abs(pd - pc)
    if ab <= 0 or cd <= 0:
        return None
    ratio = cd / ab
    score = _fib_score(ratio, (1.0, 1.272, 1.618), tol=0.15)
    if score <= 0:
        return None
    down = pb < pa
    lo, hi = sorted((pc + (-1 if down else 1) * ab * 1.0,
                     pc + (-1 if down else 1) * ab * 1.272))
    return {
        "pattern": "ARMONICA AB=CD",
        "direction": "LONG" if down else "SHORT",
        "confidence": round(0.40 + 0.45 * score, 4),
        "invalidation": pa,
        "prz": {"low": lo, "high": hi},
        "points": _label_points(points[:4], ["A", "B", "C", "D"],
                                [pa, pb, pc, pd]),
        "targets": {"tp1": pc, "tp2": pa},
    }


def projection(count, price):
    """Traiectoria urmatoarelor unde, proiectata din numaratoarea curenta.

    CE ESTE, SI CE NU ESTE
    ----------------------
    Un SCENARIU conditionat: "DACA numaratoarea e corecta, pretul ar trebui sa
    urmeze aproximativ acest drum". Nu e o predictie in sensul statistic - nu
    are o probabilitate masurata in spate, doar increderea numaratoarii. De
    asta se deseneaza punctat, pleaca de la nivelul de invalidare, si dispare
    in momentul in care pretul il atinge.

    CUM SE CONSTRUIESTE
    -------------------
    Ritmul undelor viitoare vine din ritmul celor CONFIRMATE ale aceleiasi
    numaratori (durata medie in bare), iar amplitudinile din proportiile
    Fibonacci deja folosite pentru tinte. Asta leaga proiectia de structura
    observata, nu de parametri alesi arbitrar.
      - IMPULS: unda 5 se incheie la prima tinta neatinsa, urmata de o
        corectie A-B-C (A = 38.2% din impuls, B = 50% din A, C = A).
      - CORECTIE A-B-C: C se incheie, apoi trendul reia spre tintele
        numaratorii, cu o retragere de 38.2% intre ele.
      - AB=CD: D se incheie in zona PRZ.
    Etichetele sunt intre paranteze - (W5), (A) - conventia Elliott pentru
    unde proiectate, spre deosebire de cele confirmate.
    """
    if not count or count.get("invalidated"):
        return None
    pts = count.get("points") or []
    if len(pts) < 3:
        return None
    last = pts[-1]
    confirmed = [p for p in pts if p.get("confirmed")]
    durs = [b["idx"] - a["idx"] for a, b in zip(pts, pts[1:])
            if b.get("idx") is not None and a.get("idx") is not None]
    step = max(4, int(sum(durs) / len(durs))) if durs else 12
    up = count["direction"] == "LONG"
    sign = 1 if up else -1
    tg = count.get("targets") or {}
    out = [{"label": last.get("label", ""), "price": last["price"],
            "idx": last["idx"], "projected": False}]

    now_idx = count.get("_now_idx", last["idx"])

    def add(label, price):
        # primul punct proiectat vine DUPA bara curenta, nu dupa ultimul pivot:
        # intre pivot si acum au trecut cateva bare, iar un punct proiectat nu
        # are voie sa cada in trecut, peste pretul real.
        base = max(out[-1]["idx"], now_idx) if len(out) == 1 else out[-1]["idx"]
        # Un pret proiectat nu poate fi zero sau negativ. Pe miscari abrupte in
        # jos, la preturi mici, extensiile aditive ajungeau sub zero - masurat
        # de testul de proprietati. Plafonez la 5% din pretul de pornire.
        floor = 0.05 * abs(last["price"])
        if not (price == price) or price < floor:      # NaN sau sub prag
            price = floor
        out.append({"label": f"({label})", "price": price,
                    "idx": base + step, "projected": True})

    if count["pattern"] == "IMPULS IN DEZVOLTARE":
        # W3 spre prima tinta neatinsa, W4 retrage 38.2% din W3, W5 = W1 din W4.
        len1 = count.get("len1") or abs(pts[1]["price"] - pts[0]["price"])
        w2p = pts[2]["price"]
        if count.get("stage") == "W3":
            ahead = sorted((v for v in tg.values() if v and (v - price) * sign > 0),
                           key=lambda v: abs(v - price))
            w3 = ahead[0] if ahead else last["price"]
            if ahead:
                add("W3", w3)
        else:
            w3 = pts[3]["price"]
        len3 = abs(w3 - w2p)
        w4 = w3 - sign * len3 * 0.382
        if count.get("stage") == "W3" or sign * (last["price"] - w4) > 0:
            add("W4", w4)
        # W5: ghidul standard - egal cu W1 SAU 61.8% din distanta START->W3,
        # care e mai mare. Doar "egal cu W1" dadea un W5 trunchiat (sub varful
        # lui W3) cand W4 retragea exact cat W1 - caz rar in practica.
        w5 = w4 + sign * max(len1, 0.618 * abs(w3 - pts[0]["price"]))
        if sign * (w5 - w3) <= 0:
            w5 = w3 + sign * 0.382 * len1
        add("W5", w5)
    elif count["pattern"].startswith("IMPULS"):
        # unda 5 spre prima tinta inca neatinsa in directia trendului
        ahead = sorted((v for v in tg.values() if v and (v - price) * sign > 0),
                       key=lambda v: abs(v - price))
        # Daca pretul a depasit deja toate tintele, unda 5 e practic incheiata:
        # nu mai proiectez un (W5) pe acelasi pret - ar fi un segment de
        # lungime zero - ci trec direct la corectia A-B-C.
        if ahead:
            w5 = ahead[0]
            add("W5", w5)
        else:
            w5 = last["price"]
        span = abs(w5 - pts[0]["price"])
        a = w5 - sign * span * 0.382
        add("A", a)
        add("B", a + sign * abs(w5 - a) * 0.5)
        add("C", a - sign * abs(w5 - a) * 0.5)
    elif count["pattern"].startswith("CORECTIE"):
        levels = [tg[k] for k in ("tp1", "tp2") if tg.get(k)]
        prev = last["price"]
        for i, lvl in enumerate(levels):
            add("1" if i == 0 else "3", lvl)
            if i < len(levels) - 1:
                add("2", lvl - (lvl - prev) * 0.382)
            prev = lvl
    else:
        # ARMONICA: D se incheie in PRZ, apoi pretul se intoarce spre tinte
        # (C, apoi A) - logica standard de tranzactionare a unui AB=CD. Doar D
        # in PRZ dadea un segment aproape nul, fara informatie.
        prz = count.get("prz") or {}
        if prz:
            d_end = (prz["low"] + prz["high"]) / 2.0
            if abs(d_end - last["price"]) / (abs(last["price"]) or 1) > 0.003:
                add("D", d_end)
            for lbl, key in (("1", "tp1"), ("2", "tp2")):
                if tg.get(key):
                    add(lbl, tg[key])

    # puncte consecutive la acelasi pret (ex. doua extensii plafonate) nu
    # descriu o unda - le comprim ca sa nu apara segmente de lungime zero
    comp = [out[0]]
    for pt in out[1:]:
        if abs(pt["price"] - comp[-1]["price"]) > 1e-12 * max(1.0, abs(comp[-1]["price"])):
            comp.append(pt)
    out = comp
    if len(out) < 2:
        return None
    return {"path": out, "step_bars": step,
            "invalidation": count.get("invalidation"),
            "confidence": count.get("confidence")}


def _developing_motive(piv, highs, lows, closes):
    """Impuls in DEZVOLTARE: START, W1, W2 confirmate si unda 3 in formare.

    Motorul recunostea doar impulsuri complete (6 pivoti). Cand unda 3 se
    extinde - faza cea mai puternica a unui trend - varfurile si minimele ei
    interne erau luate drept unde 3-4-5 complete, iar pretul le depasea apoi.
    Masurat pe FET real: "W5 FORMING" la 0.2160 cu pretul la 0.2435, desi dupa
    acel "W5" pretul coborase SUB W4, ceea ce exclude un W5 real. Citirea
    corecta e un W3 in extindere, exact "Primary Developing Motive W3 FORMING"
    din capturile de referinta.

    Reguli: W2 < 100% din W1; niciun minim dupa W2 sub W2 (altfel nu e W3 pornit
    din acel W2); extremul curent a depasit W1 (W3 a iesit din zona W1). Daca
    pretul a retras peste 23.6% din W3, W3 e probabil incheiat si W4 e in formare.
    """
    conf = [p for p in piv if not p.get("provisional")]
    n = len(closes)
    found = []
    for k in range(len(conf) - 1, max(1, len(conf) - 8), -1):
        s0, w1, w2 = conf[k - 2], conf[k - 1], conf[k]
        up = w1["price"] > s0["price"]
        want = ("L", "H", "L") if up else ("H", "L", "H")
        if (s0["type"], w1["type"], w2["type"]) != want:
            continue
        a = w2["idx"] + 1
        if a >= n:
            continue
        seg_h, seg_l = highs[a:n], lows[a:n]
        if up:
            ext = max(seg_h); ei = a + seg_h.index(ext)
            if min(seg_l) <= w2["price"] or ext <= w1["price"]:
                continue
        else:
            ext = min(seg_l); ei = a + seg_l.index(ext)
            if max(seg_h) >= w2["price"] or ext >= w1["price"]:
                continue
        len1, len2, len3 = (abs(w1["price"] - s0["price"]), abs(w2["price"] - w1["price"]),
                            abs(ext - w2["price"]))
        if len1 <= 0 or len2 >= len1:
            continue
        sign = 1 if up else -1
        retr = sign * (ext - closes[-1]) / (len3 or 1e-12)
        prop = (_fib_score(len2 / len1, FIB_W2)
                + _fib_score(len3 / len1, (1.618, 2.618, 4.236), tol=0.25)) / 2.0
        conf_ = round(min(0.50 + 0.30 * prop, 0.85), 4)
        pseudo = [s0, w1, w2, {"idx": ei, "price": ext}]
        labels = ["MAJOR START", "W1", "W2", "W3"]
        prices = [s0["price"], w1["price"], w2["price"], ext]
        stage = "W3"
        if retr > 0.236 and ei < n - 1:
            stage = "W4"
            tail = lows[ei + 1:n] if up else highs[ei + 1:n]
            w4 = min(tail) if up else max(tail)
            pseudo.append({"idx": ei + 1 + tail.index(w4), "price": w4})
            labels.append("W4"); prices.append(w4)
        found.append({
            "pattern": "IMPULS IN DEZVOLTARE",
            "direction": "LONG" if up else "SHORT",
            "confidence": conf_, "stage": stage,
            # in W3 invalidarea e sub startul lui W3 (W2); in W4, intrarea in
            # teritoriul lui W1 (regula 3)
            "invalidation": w2["price"] if stage == "W3" else w1["price"],
            "points": _label_points(pseudo, labels, prices),
            "targets": {"tp1": w2["price"] + sign * len1 * 1.618,
                        "tp2": w2["price"] + sign * len1 * 2.618,
                        "tp3": w2["price"] + sign * len1 * 4.236},
            "len1": len1, "start_idx": s0["idx"], "end_idx": pseudo[-1]["idx"],
        })
        break    # cel mai recent W2 valid e citirea relevanta
    return found


def stage_view(c, price):
    """Ce miscare URMEAZA, dupa stadiul structurii - nu directia ei statica.

    Un impuls ascendent in unda 5 nu e un semnal de LONG: dupa W5 urmeaza
    corectia, iar cumpararea in W5 e cea mai slaba intrare din Elliott. Asta
    returneaza directia asteptata a urmatoarei miscari, ponderea (0 = neutru)
    si o explicatie pentru dashboard.
    """
    d, opp = c["direction"], ("SHORT" if c["direction"] == "LONG" else "LONG")
    sign = 1 if d == "LONG" else -1
    tg = c.get("targets") or {}
    pat = c["pattern"]
    pts = c.get("points") or []
    # SCENARIU REALIZAT: toate tintele atinse. Pe POL real, armonica isi
    # atinsese ambele tinte, dar stadiul spunea tot "inversare asteptata".
    vals = [v for v in tg.values() if v is not None]
    if vals and all(sign * (price - v) >= 0 for v in vals):
        return d, 0.2, "scenariul s-a realizat - toate tintele atinse; potential ramas redus"
    last = pts[-1] if pts else {}
    if pat == "IMPULS IN DEZVOLTARE":
        if c.get("stage") == "W3":
            return d, 1.0, "unda 3 in formare - faza cea mai puternica a trendului"
        return d, 0.4, "unda 4 corectiva - continuarea (W5) e asteptata dupa ea"
    if pat.startswith("IMPULS"):
        t1, t3 = tg.get("tp1"), tg.get("tp3")
        if last.get("confirmed") or (t3 and sign * (price - t3) >= 0):
            return opp, 0.7, "impulsul e complet sau dincolo de tinte - urmeaza corectia A-B-C"
        if t1 and sign * (price - t1) < 0:
            return d, 0.5, "unda 5 are inca spatiu pana la prima tinta"
        return d, 0.0, "unda 5 in zona tintelor - potential ramas redus"
    if pat.startswith("CORECTIE"):
        if len(pts) >= 3:
            a_len = abs(pts[1]["price"] - pts[0]["price"])
            c_tgt = pts[2]["price"] - sign * a_len        # C = A, in sensul corectiei
            if not last.get("confirmed") and sign * (price - c_tgt) > 0:
                return opp, 0.5, "unda C inca in desfasurare, contra trendului"
        return d, 0.8, "corectia e aproape incheiata - trendul e asteptat sa reia"
    prz = c.get("prz") or {}
    if prz and not (prz["low"] <= price <= prz["high"]) and not last.get("confirmed"):
        return opp, 0.4, "D inca nu a ajuns in zona PRZ"
    return d, 0.7, "pretul e in zona PRZ - inversare asteptata"


def forecast_path(count, highs, lows, closes, plan=None, hist=None, plan_dir=None):
    """Prognoza afisata pe grafic: linie CONTINUA prin pretul real pana la
    lumanarea curenta, apoi linie PUNCTATA din prezent spre viitor.

    DE CE ASA
    ---------
    Proiectia anterioara pornea din ultimul punct al structurii, oricat de vechi,
    si ignora lumanarile de dupa el. Masurat pe POL real: pornea din D (bara 62)
    spre tinte aflate SUB pretul curent (bara 79), desi ambele fusesera deja
    atinse. Acum:

    1. LINIA CONTINUA (realitatea): de la ultimul punct al structurii, prin
       pivotii formati de atunci, pana la inchiderea curenta. Urmeaza lumanarile.
    2. LINIA PUNCTATA (prognoza) porneste din ACUM:
       a) scenariul Elliott ramas - doar punctele pe care pretul real nu le-a
          atins inca, si doar daca directia asteptata nu contrazice planul;
       b) altfel, drumul planului evaluat de agent: pullback la intrare (daca e
          cazul), apoi TP1 si TP2 - nivelurile pe care agentul le evalueaza.
    3. DURATA vine din istoric: mediana, in bare, a planurilor castigatoare din
       aceeasi directie si acelasi interval de scor. Probabilitatea afisata e
       rata de castig masurata pe aceleasi planuri - nu o estimare aleasa.
    """
    n = len(closes)
    if n < 5:
        return None
    now, px = n - 1, closes[-1]
    solid, points, source = [], [], None
    step = 12

    if count and count.get("points") and not count.get("invalidated"):
        pts = count["points"]
        last = pts[-1]
        durs = [b["idx"] - a["idx"] for a, b in zip(pts, pts[1:])]
        step = max(4, int(sum(durs) / len(durs))) if durs else 12
        if last["idx"] < now:
            solid = [{"idx": last["idx"], "price": last["price"]}]
            seg_h, seg_l = highs[last["idx"]:], lows[last["idx"]:]
            for tp in find_pivots(seg_h, seg_l, left=2, right=2):
                gi = last["idx"] + tp["idx"]
                if last["idx"] < gi < now - 1:
                    solid.append({"idx": gi, "price": tp["price"]})
            solid.append({"idx": now, "price": px})

    hstep = max(3, int(hist["median_bars_win"])) if (hist or {}).get("median_bars_win") else None

    # a) scenariul Elliott ramas, fara punctele deja atinse de pretul real
    remaining = []
    if count and count.get("projection") and not count.get("invalidated"):
        proj = count["projection"].get("path") or []
        base = count["points"][-1]["price"]
        since = count["points"][-1]["idx"]
        hi_s, lo_s = max(highs[since:]), min(lows[since:])
        prev, skipping = base, True
        for pt in [q for q in proj if q.get("projected")]:
            up = pt["price"] > prev
            passed = (hi_s >= pt["price"]) if up else (lo_s <= pt["price"])
            prev = pt["price"]
            if skipping and passed:
                continue
            skipping = False
            remaining.append(pt)
    exp = (count or {}).get("expected")
    if remaining and (plan_dir is None or exp == plan_dir or (count or {}).get("expected_weight") == 0):
        source = "elliott"
        k = now
        for i, pt in enumerate(remaining):
            k += (hstep or step) if i == 0 else step
            points.append({"label": pt["label"], "price": pt["price"], "idx": k,
                           "projected": True})
    elif plan and plan_dir in ("LONG", "SHORT"):
        # b) drumul planului evaluat de agent
        source = "plan"
        dsign = 1 if plan_dir == "LONG" else -1
        total = hstep or (2 * step)
        seq = []
        if plan.get("entry") and dsign * (px - plan["entry"]) > 0.001 * abs(px):
            seq.append(("(ENTRY)", plan["entry"], max(2, int(total * 0.15))))
        for lbl, key, frac in (("(TP1)", "tp1", 0.5), ("(TP2)", "tp2", 1.0)):
            v = plan.get(key)
            if v and dsign * (v - px) > 0:
                seq.append((lbl, v, max(3, int(total * frac))))
        last_k = now
        for lbl, v, k in seq:
            k = max(last_k + 2, now + k)
            points.append({"label": lbl, "price": v, "idx": k, "projected": True})
            last_k = k

    if not points:
        return {"solid": solid, "path": [], "source": None, "hist": hist}
    anchor = {"label": "ACUM", "price": px, "idx": now, "projected": False}
    floor = 0.05 * abs(px)
    for pt in points:
        if not (pt["price"] == pt["price"]) or pt["price"] < floor:
            pt["price"] = floor
    return {"solid": solid, "path": [anchor] + points, "source": source,
            "hist": hist, "confidence": (count or {}).get("confidence")}


def analyze(highs, lows, closes, price=None):
    """Toate ipotezele plauzibile, ordonate dupa incredere.

    Prima e "Primary", a doua "Alternative", a treia "Secondary" - exact
    denumirile din capturi. O lista goala inseamna ca nicio structura valida
    nu a fost gasita, si asta se afiseaza ca atare, nu se forteaza o eticheta.
    """
    if not closes or len(closes) < 60:
        return {"counts": [], "primary": None}
    px = price or closes[-1]
    # Fereastra de confirmare se ingusteaza daca seria da prea putini pivoti:
    # pe date putine, left/right=3 pierde capetele si nicio structura de 6
    # puncte nu mai e detectabila. Cobor pana la 2, nu mai jos - sub asta
    # zgomotul ar fi etichetat drept structura.
    piv = find_pivots(highs, lows)
    if len(piv) < 6:
        piv = find_pivots(highs, lows, left=2, right=2) or piv

    # UNDA IN FORMARE. Un pivot se confirma abia dupa cateva bare la dreapta,
    # deci unda care se desfasoara ACUM nu avea niciodata un punct terminal -
    # un impuls cu unda 5 in curs arata doar 5 pivoti si nu putea fi recunoscut.
    # Capturile de referinta arata exact aceasta unda ("W3 FORMING"), la pretul
    # curent. Adaug extremul atins de la ultimul pivot ca punct PROVIZORIU, doar
    # daca miscarea e semnificativa (peste 30% din unda anterioara) - altfel
    # orice fluctuatie de o bara ar deveni o "unda".
    if len(piv) >= 2:
        lastp, prevp = piv[-1], piv[-2]
        a, b0 = lastp["idx"] + 1, len(highs)
        if a < b0:
            leg = abs(lastp["price"] - prevp["price"]) or 1e-12
            if lastp["type"] == "L":
                m = max(highs[a:b0]); i = a + highs[a:b0].index(m)
                if m - lastp["price"] >= 0.3 * leg:
                    piv.append({"idx": i, "price": m, "type": "H", "provisional": True})
            else:
                m = min(lows[a:b0]); i = a + lows[a:b0].index(m)
                if lastp["price"] - m >= 0.3 * leg:
                    piv.append({"idx": i, "price": m, "type": "L", "provisional": True})
    if len(piv) < 4:
        return {"counts": [], "primary": None}

    counts = []
    # BUG FIX FUNDAMENTAL: structurile se ANCOREAZA la pivotii RECENTI.
    #
    # Varianta anterioara incerca doar primii 6 pivoti ca punct de start, iar
    # fiecare tipar lua primii N pivoti de acolo - deci structurile veneau
    # MEREU din partea cea mai veche a seriei. Masurat pe scanare: punctele
    # principalei la indicii 6-31 dintr-o serie de 259 de bare, adica piata de
    # acum ~40 de zile pe 4h. Undele din dashboard nu erau cele curente,
    # caracteristica `ev_elliott` a agentului descria trecutul, iar proiectia
    # pleca dintr-un punct vechi.
    #
    # Acum fiecare tipar se termina la unul dintre ultimii 4 pivoti. Increderea
    # scade usor cu fiecare pivot "in urma": o structura incheiata mai demult
    # e mai putin relevanta pentru ce face pretul acum.
    for fn, need in ((_impulse, 6), (_zigzag, 4), (_abcd, 4)):
        for back in range(0, 4):
            end = len(piv) - back
            start = end - need
            if start < 0:
                continue
            seq = piv[start:end]
            res = fn(seq)
            if res:
                res["start_idx"] = seq[0]["idx"]
                res["end_idx"] = seq[-1]["idx"]
                res["confidence"] = round(res["confidence"] * (1 - 0.08 * back), 4)
                counts.append(res)

    counts.extend(_developing_motive(piv, highs, lows, closes))

    # DEPASIRE DE PRET. O numaratoare al carei ultim punct a fost depasit clar
    # de pret (peste 25% din ultima unda, in continuarea ei) nu mai descrie
    # structura curenta: pe FET, "W5 FORMING" la 0.2160 cu pretul la 0.2435.
    # Ramane in lista, dar cu incredere redusa, ca sa nu mai poata fi Primary
    # cand exista o citire actuala.
    for c in counts:
        if c["pattern"] == "IMPULS IN DEZVOLTARE":
            continue
        pts = c.get("points") or []
        if len(pts) < 2:
            continue
        lp, pp = pts[-1], pts[-2]
        leg = abs(lp["price"] - pp["price"]) or 1e-12
        after = range(lp["idx"] + 1, len(closes))
        if lp["price"] > pp["price"]:
            beyond = (max((highs[i] for i in after), default=lp["price"]) - lp["price"]) / leg
        else:
            beyond = (lp["price"] - min((lows[i] for i in after), default=lp["price"])) / leg
        if beyond > 0.25:
            c["confidence"] = round(c["confidence"] * 0.45, 4)
            c["stale"] = True

    # STRUCTURI EXPIRATE. O numaratoare care s-a incheiat demult nu descrie
    # piata de acum: pretul s-a miscat de atunci, iar o proiectie pornita din
    # ea s-ar desena peste pretul real, in trecut. Cand actiunea recenta nu
    # formeaza nicio structura valida, e onest sa arat "nicio structura
    # curenta", nu una expirata. Fereastra: ultimele 20% din bare, minim 25.
    fresh_from = len(closes) - max(25, int(0.2 * len(closes)))
    counts = [c for c in counts if c.get("end_idx", 0) >= fresh_from]

    if not counts:
        return {"counts": [], "primary": None}

    # elimin duplicatele: acelasi tipar de la acelasi start e aceeasi ipoteza
    seen, unique = set(), []
    for c in counts:
        key = (c["pattern"], c["start_idx"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(c)

    # CONFIRMAREA SE JUDECA FATA DE BARA CURENTA. Varianta anterioara compara
    # fiecare punct cu ULTIMUL punct al structurii: pe POL real, C (bara 60) si
    # D (bara 62) apareau amandoua "FORMING", desi D se incheiase de 17 bare si
    # pretul urcase de atunci cu 20%. Acum:
    #   - un punct intermediar e confirmat dupa 3 bare;
    #   - ULTIMUL punct e confirmat doar daca pretul nu l-a depasit si s-a
    #     indepartat de el cu cel putin 23.6% din ultima unda. Altfel unda se
    #     inca formeaza (ex. un W3 aflat chiar la extremul curent).
    now_idx = len(closes) - 1
    for c in unique:
        pts = c.get("points") or []
        for j, pt in enumerate(pts):
            ok = (now_idx - pt["idx"]) >= 3
            if ok and j == len(pts) - 1 and j > 0:
                leg = abs(pt["price"] - pts[j - 1]["price"]) or 1e-12
                after = range(pt["idx"] + 1, now_idx + 1)
                if pt["price"] > pts[j - 1]["price"]:       # varf
                    exceeded = any(highs[i] > pt["price"] for i in after)
                    away = (pt["price"] - closes[-1]) / leg
                else:                                      # minim
                    exceeded = any(lows[i] < pt["price"] for i in after)
                    away = (closes[-1] - pt["price"]) / leg
                ok = (not exceeded) and away >= 0.236
            pt["confirmed"] = ok
            pt["display"] = (pt["label"] if pt["label"] == "MAJOR START"
                             else f'{pt["label"]} {"CONFIRMED" if ok else "FORMING"}')

    # Invalidarea se calculeaza INAINTE de rang: Primary / Alternative /
    # Secondary numesc scenariile inca VII, ca in capturile de referinta. O
    # ipoteza deja anulata de pret nu are voie sa ocupe locul de Primary doar
    # pentru ca avea incredere mare inainte.
    for c in unique:
        inv = c["invalidation"]
        c["invalidated"] = (px < inv) if c["direction"] == "LONG" else (px > inv)
    unique.sort(key=lambda c: (c["invalidated"], -c["confidence"]))
    # Rangurile Primary / Alternative / Secondary se dau DOAR scenariilor vii,
    # ca in capturile de referinta. Cand toate au fost anulate de pret, niciunul
    # nu e "Primary" - altfel dashboard-ul ar eticheta drept principal un
    # scenariu mort.
    _ranks = ["Primary", "Alternative", "Secondary", "Family"]
    _alive_i = 0
    for c in unique:
        if c["invalidated"]:
            c["rank"] = "Invalidat"
        else:
            c["rank"] = _ranks[_alive_i] if _alive_i < 4 else f"#{_alive_i + 1}"
            _alive_i += 1
        # Descrierea din capturi: "Primary Developing Motive W3 FORMING 48%"
        # sau "Primary Zigzag C 68%" - numele numaratorii, tipul structurii,
        # unda curenta si starea ei, apoi increderea.
        pts = c.get("points") or []
        forming = next((p for p in pts if not p.get("confirmed")), None)
        kind = ("Motive" if c["pattern"].startswith("IMPULS")
                else "Zigzag" if c["pattern"].startswith("CORECTIE") else "Harmonic")
        if forming:
            c["headline"] = (f"{c['rank']} Developing {kind} "
                             f"{forming['label']} FORMING "
                             f"{c['confidence'] * 100:.0f}%")
        else:
            tail = pts[-1]["label"] if pts else ""
            c["headline"] = (f"{c['rank']} {kind} {tail} "
                             f"{c['confidence'] * 100:.0f}%")
        # invalidarea e deja calculata mai sus, inainte de rang

    for c in unique:
        c["expected"], c["expected_weight"], c["stage_text"] = stage_view(c, px)
        sgn = 1 if c["direction"] == "LONG" else -1
        c["targets_hit"] = {k: sgn * (px - v) >= 0 for k, v in (c.get("targets") or {}).items()
                            if v is not None}
    alive = [c for c in unique if not c["invalidated"]]
    primary = alive[0] if alive else None
    # Proiectia doar pentru numaratoarea principala VALIDA - o proiectie pentru
    # fiecare ipoteza ar umple graficul de drumuri contradictorii.
    if primary:
        primary["_now_idx"] = len(closes) - 1
        primary["projection"] = projection(primary, px)
        primary.pop("_now_idx", None)
    return {"counts": unique[:4], "primary": primary,
            "alive": len(alive), "total": len(unique)}


def bias(result, direction):
    """Cat sustin numaratorile VALIDE directia planului, in [-1, 1].

    Ipotezele deja invalidate de pret sunt excluse complet - o structura moarta
    nu are voie sa contribuie la o decizie, oricat de mare i-ar fi fost
    increderea inainte.
    """
    if not result or not result.get("counts"):
        return None
    alive = [c for c in result["counts"] if not c.get("invalidated")]
    if not alive:
        return None
    # Directia ASTEPTATA pe stadiu, nu directia statica a structurii: un impuls
    # in W5 dincolo de tinte vota LONG pana acum, desi urmeaza corectia.
    total = sum(c["confidence"] for c in alive)
    if total <= 0:
        return None
    net = 0.0
    for c in alive:
        exp = c.get("expected", c["direction"])
        w = c.get("expected_weight", 1.0)
        net += c["confidence"] * w * (1 if exp == direction else -1)
    return round(max(-1.0, min(1.0, net / total)), 4)
