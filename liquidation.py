#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
liquidation.py
===============
Harta clusterelor de lichidare, reconstruita din date publice.

DE CE NU FOLOSESC DIRECT COINGLASS
-----------------------------------
CoinGlass calculeaza heatmap-ul din open interest agregat plus nivelurile de
levier. API-ul lor e platit, iar datele istorice nu sunt accesibile la scara
necesara pentru un backtest pe 5 ani. O evidenta care exista doar live si
lipseste in backtest ar face cele doua incomparabile - aceeasi problema pe care
semnatura de capabilitati o rezolva pentru order flow.

MECANICA, REPRODUSA DIN OHLCV
------------------------------
Un cluster de lichidare se formeaza acolo unde multe pozitii cu acelasi levier
ating pragul de lichidare. Cele doua ingrediente sunt:

  1. UNDE s-au deschis pozitiile. Aproximat prin profilul de volum: zonele cu
     volum mare sunt zonele in care s-a tranzactionat mult, deci unde s-au
     acumulat pozitii.
  2. LA CE DISTANTA se lichideaza. Pentru un levier L, pretul de lichidare e la
     aproximativ 1/L de la intrare - un long cu 25x se lichideaza la ~4% sub
     intrare, unul cu 100x la ~1%.

Proiectand fiecare nod de volum la nivelurile de levier uzuale si insumand
densitatile, obtin o harta care aproximeaza acelasi lucru: unde se aduna
pozitiile vulnerabile.

CUM SE CITESTE, SI CUM O FOLOSESC
----------------------------------
Clusterele actioneaza ca magneti: pretul tinde sa se deplaseze spre ele, pentru
ca lichidarile fortate genereaza ordine in acea directie, iar participantii mari
au interes sa le atinga. Regula de citire e simpla:
  - clusterele DEASUPRA pretului sunt lichidari de SHORT -> magnet in sus
  - clusterele SUB pret sunt lichidari de LONG -> magnet in jos

Deci, pentru un plan LONG, un cluster dens deasupra SUSTINE directia, iar unul
dens dedesubt o CONTRAZICE. Exact invers pentru SHORT.

LIMITE, DECLARATE
-----------------
Harta e o APROXIMARE, nu o masuratoare. Nu stiu open interest-ul real, nu stiu
distributia reala a levierului, si multi traderi isi inchid pozitia inainte de
lichidare - deci densitatile sunt supraestimate. O tratez ca pe o evidenta
printre altele, cu intensitate proportionala cu densitatea, nu ca pe un semnal
de sine statator.
"""

# Niveluri de levier si ponderea lor aproximativa in piata retail. Levierele
# mari sunt mai populare la retail dar tin pozitii mai mici; ponderile
# incearca sa reflecte contributia neta, nu numarul de conturi.
LEVERAGE_TIERS = ((10, 0.30), (25, 0.30), (50, 0.25), (100, 0.15))

# Marja de mentinere tipica, care apropie pretul de lichidare fata de 1/L pur.
MAINTENANCE_MARGIN = 0.005


def _volume_nodes(candles, bins=48):
    """Zonele cu volum mare: aproximarea pentru "unde s-au deschis pozitiile"."""
    if not candles:
        return []
    prices, vols = [], []
    for c in candles:
        hi, lo, vol = c[2], c[3], (c[5] or 0)
        prices.append((hi + lo) / 2.0)
        vols.append(vol)
    lo_p, hi_p = min(prices), max(prices)
    if hi_p <= lo_p:
        return []
    step = (hi_p - lo_p) / bins
    buckets = [0.0] * bins
    for p, v in zip(prices, vols):
        idx = min(int((p - lo_p) / step), bins - 1)
        buckets[idx] += v
    total = sum(buckets) or 1.0
    return [((lo_p + (i + 0.5) * step), buckets[i] / total)
            for i in range(bins) if buckets[i] > 0]


def liquidation_price(entry, leverage, is_long):
    """Pretul la care o pozitie cu levierul dat e lichidata."""
    move = (1.0 / leverage) - MAINTENANCE_MARGIN
    move = max(move, 0.001)
    return entry * (1.0 - move) if is_long else entry * (1.0 + move)


def build_map(candles, price, bins=60, oi_weight=None):
    """Construieste harta de densitate a lichidarilor.

    `oi_weight` (optional) scaleaza intreaga harta cu open interest-ul curent,
    daca bursa il ofera. Lipsa lui nu schimba FORMA hartii, doar magnitudinea
    absoluta - iar deciziile se iau pe densitate relativa, deci harta ramane
    utilizabila si fara el. Asta e si motivul pentru care nu e o capabilitate
    obligatorie.
    """
    nodes = _volume_nodes(candles)
    if not nodes or not price or price <= 0:
        return {"clusters": [], "above": None, "below": None, "bins": 0}

    lows = [c[3] for c in candles]
    highs = [c[2] for c in candles]
    lo_p = min(lows) * 0.82
    hi_p = max(highs) * 1.18
    step = (hi_p - lo_p) / bins
    if step <= 0:
        return {"clusters": [], "above": None, "below": None, "bins": 0}

    long_liq = [0.0] * bins     # lichidari de LONG (sub pret) -> magnet in jos
    short_liq = [0.0] * bins    # lichidari de SHORT (peste pret) -> magnet in sus

    for node_price, node_w in nodes:
        for lev, lev_w in LEVERAGE_TIERS:
            w = node_w * lev_w
            for is_long, target in ((True, long_liq), (False, short_liq)):
                lp = liquidation_price(node_price, lev, is_long)
                if not (lo_p <= lp < hi_p):
                    continue
                target[min(int((lp - lo_p) / step), bins - 1)] += w

    scale = float(oi_weight) if oi_weight else 1.0
    clusters = []
    for i in range(bins):
        p_mid = lo_p + (i + 0.5) * step
        for side, arr in (("LONG", long_liq), ("SHORT", short_liq)):
            if arr[i] <= 0:
                continue
            clusters.append({"price": p_mid, "density": round(arr[i] * scale, 6),
                             "side": side,
                             "distance_pct": round(100 * (p_mid - price) / price, 2)})
    if not clusters:
        return {"clusters": [], "above": None, "below": None, "bins": bins}

    peak = max(c["density"] for c in clusters) or 1.0
    for c in clusters:
        c["intensity"] = round(c["density"] / peak, 4)

    # Cel mai dens cluster de fiecare parte: magnetii candidati.
    above = max((c for c in clusters if c["price"] > price),
                key=lambda c: c["density"], default=None)
    below = max((c for c in clusters if c["price"] < price),
                key=lambda c: c["density"], default=None)

    clusters.sort(key=lambda c: -c["density"])
    return {"clusters": clusters[:24], "above": above, "below": below,
            "bins": bins, "oi_scaled": bool(oi_weight)}


def magnet_bias(liq_map, price, direction):
    """Cat de mult sustine harta directia planului.

    Returneaza un scor in [-1, 1]: pozitiv cand magnetul dominant e in directia
    planului, negativ cand e impotriva. Foloseste densitatea relativa a celor
    doi magneti si distanta pana la ei - un cluster foarte departe trage mai slab.
    """
    if not liq_map or not price:
        return None
    above, below = liq_map.get("above"), liq_map.get("below")
    if not above and not below:
        return None

    def pull(c):
        if not c:
            return 0.0
        d = abs(c["distance_pct"]) or 0.1
        # atenuare cu distanta: un cluster la 1% trage mult mai tare decat unul la 20%
        return c["density"] / (1.0 + d / 5.0)

    up, down = pull(above), pull(below)
    total = up + down
    if total <= 0:
        return None
    net = (up - down) / total
    return round(net if direction == "LONG" else -net, 4)
