# -*- coding: utf-8 -*-
"""core.zones - zonele de suport si rezistenta (cerere / oferta) din lumanari, cu confluentele lor
si rata MASURATA cu care au fost respectate.

CE E O ZONA
-----------
Zona de CERERE (suport) se formeaza la un minim de swing confirmat - PIVOT bare de fiecare parte,
ca indicatorul de supply/demand din capturile de referinta ("5 5") - si merge de la minimul
fitilului pana la corpul lumanarii-pivot, intre 0.25 si 1 ATR latime. Zona de OFERTA (rezistenta)
e simetrica, la un maxim de swing. Ciclul de viata, bara cu bara dupa confirmare:
  - ATINSA: pretul intra in zona fara sa inchida dincolo de ea (fiecare atingere o slabeste);
  - SPARTA: o inchidere dincolo de marginea opusa. Cererea sparta devine rezistenta (flip /
    breaker block, in limbaj SMC) si traieste pana la o inchidere inapoi peste ea;
  - zonele suprapuse de acelasi tip se unesc (fara sa devina mai late de 2 ATR).
CONFLUENTELE sunt nivelurile pe care scanerul le calculeaza deja si care cad in zona: POC/VAL/VAH,
medii mobile, Fibonacci 0.618/0.786, lichiditate egala, clustere de lichidari. Forta (0-100)
combina desprinderea impulsiva din zona, prospetimea, confluentele si varsta.

CE NU E
-------
O promisiune ca pretul se intoarce din zona. hold_stats() masoara, pe lumanarile reale ale tuturor
simbolurilor, cat de des a tinut o zona la PRIMA retestare, fata de benzi-placebo cu aceeasi geometrie
asezate la un moment aleator - adica daca zonele aduc informatie peste o banda oarecare de pret.
Doar CPU; nicio decizie de plan nu foloseste zonele.
"""

import random

from core.indicators import atr_series

PIVOT = 5                 # bare de fiecare parte ale unui pivot confirmat
MIN_W_ATR = 0.25          # latimea minima a unei zone, in ATR
MAX_W_ATR = 1.0           # latimea maxima a zonei formate la un pivot
MERGE_GAP_ATR = 0.15      # zonele mai apropiate de atat se unesc
MERGED_MAX_ATR = 2.0      # ... dar o zona unita nu trece de 2 ATR
CONF_PAD_ATR = 0.25       # un nivel "e in zona" si cand e la 0.25 ATR de margine
DEPART_BARS = 6           # desprinderea se masoara pe urmatoarele 6 bare
MAX_PER_SIDE = 3          # zone afisate pe fiecare parte a pretului
NEAR_PCT = 25.0           # zonele mai departe de 25% de pret nu incadreaza graficul
HOLD_BARS = 12            # retestarea: 12 bare (2 zile pe 4h)
HOLD_MOVE_ATR = 1.0       # "a tinut" = 1 ATR de miscare din zona inainte de inchiderea dincolo de ea
PLACEBO_REPS = 3          # benzi-placebo per zona (aceeasi geometrie, alt moment)


def _atr(candles):
    """Seria ATR (Wilder) si ultima valoare. Barele de dinaintea primei valori primesc prima
    valoare calculata (nu ultima: ar insemna sa folosesti volatilitatea din viitor)."""
    a = atr_series([c[2] for c in candles], [c[3] for c in candles], [c[4] for c in candles], 14)
    first = next((v for v in a if v), None)
    last = next((v for v in reversed(a) if v), None)
    if last is None:            # serie prea scurta: media amplitudinii barelor
        rng = [c[2] - c[3] for c in candles if c[2] > c[3]]
        first = last = sum(rng) / len(rng) if rng else 0.0
    return [v or first for v in a], last


def _pivots(candles, k):
    """Indicii pivotilor confirmati: (minime, maxime). Strict la stanga, nestrict la dreapta,
    ca un platou cu doua minime egale sa dea un singur pivot."""
    lows = [c[3] for c in candles]
    highs = [c[2] for c in candles]
    pl, ph = [], []
    for i in range(k, len(candles) - k):
        if lows[i] < min(lows[i - k:i]) and lows[i] <= min(lows[i + 1:i + k + 1]):
            pl.append(i)
        if highs[i] > max(highs[i - k:i]) and highs[i] >= max(highs[i + 1:i + k + 1]):
            ph.append(i)
    return pl, ph


def _raw(candles, atr, k):
    """Zonele formate la pivoti, inainte de ciclul de viata."""
    n = len(candles)
    pl, ph = _pivots(candles, k)
    out = []
    for i in pl:
        o, h, lo, c = candles[i][1:5]
        a = atr[i]
        top = lo + min(max(max(o, c) - lo, MIN_W_ATR * a), MAX_W_ATR * a)
        nxt = [x[2] for x in candles[i + 1:i + 1 + DEPART_BARS]]
        dep = max(0.0, (max(nxt) - top) / a) if nxt and a else 0.0
        out.append({"side": "S", "lo": lo, "hi": top, "i": i, "conf": min(i + k, n - 1), "dep": dep,
                    "kind": "cerere"})
    for i in ph:
        o, h, lo, c = candles[i][1:5]
        a = atr[i]
        bot = h - min(max(h - min(o, c), MIN_W_ATR * a), MAX_W_ATR * a)
        nxt = [x[3] for x in candles[i + 1:i + 1 + DEPART_BARS]]
        dep = max(0.0, (bot - min(nxt)) / a) if nxt and a else 0.0
        out.append({"side": "R", "lo": bot, "hi": h, "i": i, "conf": min(i + k, n - 1), "dep": dep,
                    "kind": "oferta"})
    return out


def _evolve(z, candles):
    """Ciclul de viata dupa confirmare: atingeri, spargere, flip. False daca zona a murit."""
    side, touches, flipped = z["side"], 0, False
    cf = candles[z["conf"]]
    inside = (cf[3] <= z["hi"]) if side == "S" else (cf[2] >= z["lo"])
    for j in range(z["conf"] + 1, len(candles)):
        h, lo, c = candles[j][2], candles[j][3], candles[j][4]
        if side == "S" and c < z["lo"] or side == "R" and c > z["hi"]:
            if flipped:
                return False                     # flip-ul spart si el: zona nu mai conteaza
            side, flipped, touches, inside = ("R" if side == "S" else "S"), True, 0, True
            z["flip_i"] = j
            continue
        entered = (lo <= z["hi"]) if side == "S" else (h >= z["lo"])
        if entered and not inside:
            touches += 1
        inside = entered
    z.update(side=side, touches=touches, flipped=flipped)
    if flipped:
        z["kind"] = "flip"
    return True


def _merge(zones, atr_last):
    out = []
    for side in ("S", "R"):
        group = sorted((z for z in zones if z["side"] == side), key=lambda z: z["lo"])
        cur = None
        for z in group:
            if (cur is not None and z["lo"] <= cur["hi"] + MERGE_GAP_ATR * atr_last
                    and max(cur["hi"], z["hi"]) - cur["lo"] <= MERGED_MAX_ATR * atr_last):
                cur.update(hi=max(cur["hi"], z["hi"]), i=min(cur["i"], z["i"]), dep=max(cur["dep"], z["dep"]),
                           touches=max(cur["touches"], z["touches"]), flipped=cur["flipped"] or z["flipped"],
                           merged=cur.get("merged", 1) + 1)
                if cur["flipped"]:
                    cur["kind"] = "flip"
                continue
            if cur is not None:
                out.append(cur)
            cur = dict(z)
        if cur is not None:
            out.append(cur)
    return out


def _strength(z, n, sources):
    age = n - 1 - z["i"]
    s = (20 + min(30.0, 10 * z["dep"]) + (12 if z["touches"] == 0 else 6 if z["touches"] == 1 else 0)
         + min(32, 8 * len(sources)) + (5 if z["flipped"] else 0) - min(15.0, age / 20))
    return int(max(0, min(100, round(s))))


def _round(v):
    a = abs(v)
    nd = 2 if a >= 1000 else 4 if a >= 1 else 6 if a >= 0.01 else 8
    return round(v, nd)


def _declutter(zones, px, min_w):
    """Zonele fara suprapuneri, ca pe un grafic citibil. Se accepta in ordinea primita (ordinea
    prioritatii); fiecare zona urmatoare pierde partea care se suprapune cu cele deja acceptate -
    suportul pastreaza partea de jos (cu minimul pivotului), rezistenta pe cea de sus. O zona care
    ramane mai ingusta de `min_w(zona)` sau ajunge pe partea gresita a pretului cade: aria ei e
    deja reprezentata de o zona mai importanta."""
    taken = []
    for z in zones:
        lo, hi = z["lo"], z["hi"]
        if z["side"] == "S":
            for t in sorted(taken, key=lambda t: t["lo"]):
                if t["hi"] <= lo or t["lo"] >= hi:
                    continue
                if t["lo"] > lo:
                    hi = t["lo"]              # partea de sub zona acceptata
                    break
                lo = t["hi"]                  # zona acceptata acopera baza: ramane partea de sus
        else:
            for t in sorted(taken, key=lambda t: -t["hi"]):
                if t["hi"] <= lo or t["lo"] >= hi:
                    continue
                if t["hi"] < hi:
                    lo = t["hi"]              # partea de deasupra zonei acceptate
                    break
                hi = t["lo"]                  # zona acceptata acopera varful: ramane partea de jos
        if hi - lo < min_w(z) or (z["side"] == "S" and lo > px) or (z["side"] == "R" and hi < px):
            continue
        if (lo, hi) != (z["lo"], z["hi"]):
            z = dict(z, lo=_round(lo), hi=_round(hi), clipped=True)
            edge = z["hi"] if z["side"] == "S" else z["lo"]
            z["dist_pct"] = 0.0 if z["lo"] <= px <= z["hi"] else round((edge - px) / px * 100, 2)
        taken.append(z)
    return taken


def _priority(z):
    """Ordinea in care zonele isi pastreaza aria: cea in care e pretul, apoi cele mai apropiate."""
    return (z["dist_pct"] != 0.0, abs(z["dist_pct"]), -z["strength"])


def find_zones(candles, extra_levels=None, price=None, pivot=PIVOT, max_per_side=MAX_PER_SIDE):
    """Zonele active care incadreaza pretul: cel mult `max_per_side` de suport sub el si tot
    atatea de rezistenta deasupra, cele mai apropiate intai.

    `candles`: [[ts, o, h, l, c, v], ...] INCHISE. `extra_levels`: [(eticheta, pret), ...] -
    confluentele. Intoarce {"zones": [...], "support": zona|None, "resistance": zona|None,
    "atr", "pivot", "bars"}; {} daca seria e prea scurta."""
    n = len(candles or [])
    if n < 2 * pivot + 10:
        return {}
    atr, atr_last = _atr(candles)
    if not atr_last:
        return {}
    px = price if price else candles[-1][4]
    live = [z for z in _raw(candles, atr, pivot) if _evolve(z, candles)]
    merged = _merge(live, atr_last)
    pad = CONF_PAD_ATR * atr_last
    levels = [(str(lbl), float(v)) for lbl, v in (extra_levels or []) if v]
    sup, res = [], []
    for z in merged:
        # ROLUL DUPA POZITIA PRETULUI DE ACUM: o zona de cerere aflata deasupra pretului curent
        # (de exemplu: pe 1D nu s-a inchis inca nicio zi sub ea, dar pretul intraday e deja
        # dedesubt) lucreaza acum ca rezistenta - si invers. Fara asta, aceeasi arie aparea
        # "suport 1D" si "rezistenta 4h" in acelasi timp.
        side, pending = z["side"], False
        if side == "S" and z["lo"] > px:
            side, pending = "R", True
        elif side == "R" and z["hi"] < px:
            side, pending = "S", True
        edge = z["hi"] if side == "S" else z["lo"]
        if abs(edge - px) / px * 100 > NEAR_PCT:
            continue
        sources = sorted({lbl for lbl, v in levels if z["lo"] - pad <= v <= z["hi"] + pad})
        inside = z["lo"] <= px <= z["hi"]
        rec = {"side": side, "lo": _round(z["lo"]), "hi": _round(z["hi"]), "kind": z["kind"],
               "t0": candles[z["i"]][0], "touches": z["touches"], "fresh": z["touches"] == 0,
               "strength": _strength(z, n, sources), "sources": sources,
               "dist_pct": 0.0 if inside else round((edge - px) / px * 100, 2),
               "dep_atr": round(z["dep"], 2)}
        if pending:
            rec["pending"] = True     # pretul a trecut de zona fara inchidere pe timeframe-ul ei
        (sup if side == "S" else res).append(rec)
    # FARA SUPRAPUNERI: doua zone care nu s-au putut uni (ar fi trecut de 2 ATR) sau un suport si
    # o rezistenta reclasificata care acopera aceeasi arie s-ar desena una peste alta
    kept = _declutter(sorted(sup + res, key=_priority), px, lambda z: MIN_W_ATR * atr_last)
    sup = sorted((z for z in kept if z["side"] == "S"), key=lambda z: -z["hi"])
    res = sorted((z for z in kept if z["side"] == "R"), key=lambda z: z["lo"])

    def pick(zs):
        # cea mai apropiata zona ramane mereu (ea incadreaza pretul); dintre urmatoarele,
        # cele slabe (sub 25) cad cand sunt destule altele
        if not zs:
            return []
        keep = [zs[0]] + [z for z in zs[1:] if z["strength"] >= 25]
        return keep[:max_per_side]

    sup, res = pick(sup), pick(res)
    return {"zones": sup + res, "support": sup[0] if sup else None, "resistance": res[0] if res else None,
            "atr": _round(atr_last), "price": _round(px), "pivot": pivot, "bars": n}


FRAME_PER_SIDE = 2        # zone desenate pe grafic, de fiecare parte a pretului


def _summary(z):
    """Ce se pastreaza dintr-un timeframe pe langa cadru: ATR, pretul, cate zone active."""
    if not z:
        return None
    return {"atr": z.get("atr"), "price": z.get("price"), "bars": z.get("bars"), "pivot": z.get("pivot"),
            "zones": len(z.get("zones") or [])}


def bundle(scan, d1, scan_tf="4h", per_side=FRAME_PER_SIDE, keep_d1=True):
    """Zonele unui simbol pe doua timeframe-uri (al scanarii si 1D) si cadrul desenat pe grafic:
    cele mai apropiate `per_side` zone de fiecare parte a pretului. O zona de pe timeframe-ul
    scanarii acoperita de o zona 1D de acelasi tip e reprezentata de cea 1D (marcata
    "confirmata" si pe timeframe-ul mic) - aceeasi arie nu se deseneaza de doua ori. Cand
    pretul e la minimul seriei scurte, zonele 1D sunt cele care il incadreaza.

    Se pastreaza doar ce se foloseste: cadrul (graficele), rezultatul 1D intreg pe bursa activa
    (`keep_d1`: analistul ia de aici suportul/rezistenta pentru R:R, bursele secundare refolosesc
    zonele) si un rezumat pentru rest - detaliile se salveaza la fiecare scanare, pe 5 burse."""
    scan, d1 = scan or {}, d1 or {}
    small = [dict(z, tf=scan_tf) for z in scan.get("zones") or []]
    big = [dict(z, tf="1d") for z in d1.get("zones") or []]
    covered = set()
    for z in big:
        for k, y in enumerate(small):
            if y["side"] == z["side"] and y["lo"] <= z["hi"] and y["hi"] >= z["lo"]:
                covered.add(k)
                z["confirmed"] = True
    small = [y for k, y in enumerate(small) if k not in covered]
    # zonele 1D isi pastreaza aria; o zona a timeframe-ului mic care o suprapune (de cealalta
    # parte - cele de aceeasi parte sunt deja reprezentate de 1D) pierde partea comuna
    px = scan.get("price") or d1.get("price")
    atr = {"1d": d1.get("atr") or 0, scan_tf: scan.get("atr") or 0}
    pool = _declutter(sorted(big, key=_priority) + sorted(small, key=_priority), px,
                      lambda z: MIN_W_ATR * atr.get(z["tf"], 0)) if px else big + small
    sup = sorted((z for z in pool if z["side"] == "S"), key=lambda z: -z["hi"])[:per_side]
    res = sorted((z for z in pool if z["side"] == "R"), key=lambda z: z["lo"])[:per_side]
    return {"tf": scan_tf, "frame": sup + res, "scan": _summary(scan),
            "d1": (d1 or None) if keep_d1 else _summary(d1)}


def _first_retest(z, candles, atr):
    """Rezultatul primei retestari a zonei: 'held', 'broke' sau None (nicio retestare sau
    nerezolvata in HOLD_BARS). Bara care sparge e verificata INAINTEA miscarii favorabile."""
    S = z["side"] == "S"
    cf = candles[z["conf"]]
    inside = (cf[3] <= z["hi"]) if S else (cf[2] >= z["lo"])
    for j in range(z["conf"] + 1, len(candles)):
        h, lo, c = candles[j][2], candles[j][3], candles[j][4]
        if (S and c < z["lo"]) or (not S and c > z["hi"]):
            # intrat si spart in aceeasi bara = retestare esuata; daca pretul era deja in zona
            # de la confirmare, n-a fost o retestare si nu se puncteaza
            return "broke" if not inside else None
        entered = (lo <= z["hi"]) if S else (h >= z["lo"])
        if entered and not inside:
            a = atr[j]
            for t in range(j, min(len(candles), j + HOLD_BARS + 1)):
                th, tl, tc = candles[t][2], candles[t][3], candles[t][4]
                if (S and tc < z["lo"]) or (not S and tc > z["hi"]):
                    return "broke"
                if (S and th >= z["hi"] + HOLD_MOVE_ATR * a) or (not S and tl <= z["lo"] - HOLD_MOVE_ATR * a):
                    return "held"
            return None
        inside = entered
    return None


def _wilson(k, n, z=1.96):
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    m = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return max(0.0, c - m), min(1.0, c + m)


def hold_stats(series, pivot=PIVOT):
    """Cat de des au tinut zonele la PRIMA retestare, pe lumanarile reale ale tuturor simbolurilor,
    fata de PLACEBO: aceeasi geometrie (distanta fata de pret si latimea, in ATR) asezata la un
    moment aleator din aceeasi serie - adica fara ancora intr-un pivot real. Daca zonele reale nu
    tin mai des decat placebo, pivotii nu aduc informatie peste o banda oarecare de pret.

    DE CE ACEST PLACEBO: o banda mutata doar in pret (1-3 ATR mai jos) NU e un etalon cinstit -
    masurat pe 25 de serii de mers aleator, benzile mai departe de pret "tin" mai des (70% fata de
    63%) din simpla geometrie, deci zonele ar fi parut mai slabe decat sunt. Mutata in TIMP, cu
    aceeasi pozitie relativa, diferenta pe mers aleator a fost in medie -0.7 si +0.1 puncte
    (volatilitate constanta / variabila, 6 universuri fiecare) - in zgomot."""
    real = {"held": 0, "broke": 0}
    plac = {"held": 0, "broke": 0}
    for sym in sorted(series or {}):
        candles = series[sym] or []
        n = len(candles)
        if n < 2 * pivot + 10 + HOLD_BARS:
            continue
        atr, _ = _atr(candles)
        rnd = random.Random("zones:" + sym)
        for z in _raw(candles, atr, pivot):
            r = _first_retest(z, candles, atr)
            if r:
                real[r] += 1
            cf = z["conf"]
            a, close = atr[cf], candles[cf][4]
            if not a:
                continue
            rel_lo, rel_hi = (z["lo"] - close) / a, (z["hi"] - close) / a
            for _ in range(PLACEBO_REPS):
                t = rnd.randrange(2 * pivot, n - HOLD_BARS)
                a2, c2 = atr[t], candles[t][4]
                r = _first_retest(dict(z, lo=c2 + rel_lo * a2, hi=c2 + rel_hi * a2, conf=t), candles, atr)
                if r:
                    plac[r] += 1

    def summ(d):
        n = d["held"] + d["broke"]
        lo, hi = _wilson(d["held"], n)
        return {"n": n, "held": d["held"], "rate": round(100 * d["held"] / n, 1) if n else None,
                "ci_low": round(100 * lo, 1), "ci_high": round(100 * hi, 1)}

    rs, ps = summ(real), summ(plac)
    verdict = None
    if rs["n"] >= 30 and ps["n"] >= 30:
        verdict = ("peste_placebo" if rs["ci_low"] > ps["ci_high"] else
                   "sub_placebo" if rs["ci_high"] < ps["ci_low"] else "in_marja")
    return {"zones": rs, "placebo": ps, "verdict": verdict, "horizon_bars": HOLD_BARS,
            "move_atr": HOLD_MOVE_ATR, "pivot": pivot}
