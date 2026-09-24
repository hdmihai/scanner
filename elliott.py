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
    if len(piv) < 4:
        return {"counts": [], "primary": None}

    counts = []
    # incerc etichetari pornind de la mai multe puncte de start: o numaratoare
    # buna nu depinde de unde se intampla sa inceapa fereastra
    for start in range(0, min(len(piv) - 3, 6)):
        seq = piv[start:]
        for fn in (_impulse, _zigzag, _abcd):
            res = fn(seq)
            if res:
                res["start_idx"] = seq[0]["idx"]
                counts.append(res)

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

    unique.sort(key=lambda c: -c["confidence"])
    for i, c in enumerate(unique):
        c["rank"] = ["Primary", "Alternative", "Secondary", "Family"][i] if i < 4 else f"#{i+1}"
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
        # o ipoteza deja invalidata de pret e marcata, nu ascunsa
        inv = c["invalidation"]
        c["invalidated"] = (px < inv) if c["direction"] == "LONG" else (px > inv)

    alive = [c for c in unique if not c["invalidated"]]
    primary = alive[0] if alive else None
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
    total = sum(c["confidence"] for c in alive)
    if total <= 0:
        return None
    agree = sum(c["confidence"] for c in alive if c["direction"] == direction)
    return round((2.0 * agree / total) - 1.0, 4)
