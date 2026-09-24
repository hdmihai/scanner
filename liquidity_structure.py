#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
liquidity_structure.py
=======================
Nivelurile de lichiditate structurala din capturile de referinta:
STRUCT BUY-SIDE LIQ, EQUAL HIGHS, TWO-SIDED LIQUIDITY SWEEP.

IDEEA, IN DOUA PROPOZITII
--------------------------
Sub fiecare minim de swing stau ordine stop ale celor pozitionati long; peste
fiecare maxim de swing stau stopurile celor pozitionati short. Acele grupuri de
stopuri SUNT lichiditate: locul unde un participant mare poate executa volum
fara sa miste pretul impotriva lui, fiindca cineva e obligat sa ia cealalta
parte.

De aici vocabularul din capturi:
  - BUY-SIDE LIQ  = deasupra pretului, stopuri de SHORT (cumparari fortate)
  - SELL-SIDE LIQ = sub pret, stopuri de LONG (vanzari fortate)
  - EQUAL HIGHS / EQUAL LOWS = doua sau mai multe varfuri la acelasi nivel;
    stopurile se aduna exact acolo, deci concentrarea e mai mare
  - SWEEP = pretul trece de nivel, declanseaza stopurile, si se intoarce.
    Lichiditatea a fost "luata". Un sweep de o parte urmat de altul de partea
    cealalta e TWO-SIDED, semnul tipic de acumulare inaintea miscarii reale.

TARGET vs ENTRY
---------------
Un nivel neatins inca e o TINTA: pretul e atras spre el. Un nivel deja maturat
si recuperat devine un potential punct de INTRARE: lichiditatea de acolo s-a
consumat, iar continuarea in directia opusa sweep-ului e ce urmareste metoda.

LIMITE, DECLARATE
-----------------
Stopurile nu sunt vizibile; presupun ca exista sub minime si peste maxime,
ceea ce e o conventie, nu o masuratoare. Puterea unui nivel o derivez din
cate varfuri il formeaza si din volumul de la formare, nu din ordine reale.
Se calculeaza din OHLCV, deci exista identic in live si in backtest.
"""

# Doua varfuri se considera "egale" daca difera cu mai putin de atata din ATR.
EQUAL_TOLERANCE_ATR = 0.25


def find_swings(highs, lows, left=3, right=3):
    """Varfuri si minime de swing confirmate, cu indicii lor."""
    sw_h, sw_l = [], []
    n = len(highs)
    for i in range(left, n - right):
        win_h = highs[i - left:i + right + 1]
        win_l = lows[i - left:i + right + 1]
        if highs[i] == max(win_h):
            sw_h.append({"idx": i, "price": highs[i]})
        if lows[i] == min(win_l):
            sw_l.append({"idx": i, "price": lows[i]})
    return sw_h, sw_l


def _cluster(levels, tol):
    """Grupeaza nivelurile apropiate: doua varfuri la acelasi pret sunt UN
    singur bazin de lichiditate, cu concentrare dubla, nu doua niveluri."""
    if not levels:
        return []
    ordered = sorted(levels, key=lambda x: x["price"])
    groups = [[ordered[0]]]
    for lv in ordered[1:]:
        if abs(lv["price"] - groups[-1][-1]["price"]) <= tol:
            groups[-1].append(lv)
        else:
            groups.append([lv])
    out = []
    for g in groups:
        out.append({
            "price": sum(x["price"] for x in g) / len(g),
            "touches": len(g),
            "last_idx": max(x["idx"] for x in g),
            "first_idx": min(x["idx"] for x in g),
        })
    return out


def _swept(cluster, highs, lows, side):
    """A fost nivelul depasit si apoi recuperat? Adica lichiditatea luata.

    Pentru buy-side: pretul a urcat PESTE nivel dupa formarea lui, apoi a
    inchis inapoi sub el. Asta inseamna ca stopurile de deasupra au fost
    declansate si miscarea nu a continuat.
    """
    after = range(cluster["last_idx"] + 1, len(highs))
    pierced = False
    for i in after:
        if side == "BUY" and highs[i] > cluster["price"]:
            pierced = True
        elif side == "SELL" and lows[i] < cluster["price"]:
            pierced = True
        if pierced:
            # recuperat? pretul s-a intors de partea initiala
            if side == "BUY" and lows[i] < cluster["price"]:
                return True
            if side == "SELL" and highs[i] > cluster["price"]:
                return True
    return False


def build(highs, lows, closes, atr, price=None, timeframe="4h", top=4):
    """Nivelurile de lichiditate, etichetate ca in capturile de referinta."""
    if not highs or not lows or len(highs) < 30 or not atr:
        return {"levels": [], "two_sided_sweep": False}
    px = price or closes[-1]
    tol = atr * EQUAL_TOLERANCE_ATR

    sw_h, sw_l = find_swings(highs, lows)
    buy = _cluster(sw_h, tol)     # deasupra: stopuri de SHORT
    sell = _cluster(sw_l, tol)    # dedesubt: stopuri de LONG

    levels = []
    swept_buy = swept_sell = False

    for side, clusters, srclabel in (("BUY", buy, "BUY-SIDE"),
                                     ("SELL", sell, "SELL-SIDE")):
        for c in clusters:
            # doar nivelurile de partea corecta a pretului conteaza ca tinte
            if side == "BUY" and c["price"] <= px:
                continue
            if side == "SELL" and c["price"] >= px:
                continue
            was_swept = _swept(c, highs, lows, side)
            if was_swept:
                if side == "BUY":
                    swept_buy = True
                else:
                    swept_sell = True
            kind = ("EQUAL HIGHS" if (side == "BUY" and c["touches"] >= 2)
                    else "EQUAL LOWS" if (side == "SELL" and c["touches"] >= 2)
                    else "MAJOR SWING HIGH" if side == "BUY"
                    else "MAJOR SWING LOW")
            role = "ENTRY" if was_swept else "TARGET"
            levels.append({
                "price": c["price"], "side": side, "kind": kind, "role": role,
                "touches": c["touches"], "swept": was_swept,
                "distance_pct": round(100 * (c["price"] - px) / px, 2),
                "label": f"STRUCT {srclabel} LIQ - {timeframe} - {kind} - {role}",
                # concentrarea creste cu numarul de atingeri si scade cu distanta
                "strength": round(min(1.0, (c["touches"] / 3.0)
                                      / (1 + abs(c["price"] - px) / px / 0.05)), 4),
            })

    levels.sort(key=lambda l: -l["strength"])
    levels = levels[:top * 2]

    nearest_above = min((l for l in levels if l["price"] > px),
                        key=lambda l: l["price"], default=None)
    nearest_below = max((l for l in levels if l["price"] < px),
                        key=lambda l: l["price"], default=None)

    return {
        "levels": levels,
        # Sweep pe AMBELE parti: lichiditatea a fost luata sus si jos, semnul
        # tipic de acumulare inaintea miscarii directionale.
        "two_sided_sweep": bool(swept_buy and swept_sell),
        "swept_buy": swept_buy, "swept_sell": swept_sell,
        "above": nearest_above, "below": nearest_below,
    }


def bias(result, direction):
    """Cat sustine structura de lichiditate directia planului, in [-1, 1].

    Pretul e atras spre lichiditatea NEATINSA. O tinta buy-side deasupra trage
    in sus; una sell-side dedesubt trage in jos. Nivelurile deja maturate nu
    mai trag - lichiditatea de acolo s-a consumat - deci le exclud.
    """
    if not result or not result.get("levels"):
        return None
    pull_up = sum(l["strength"] for l in result["levels"]
                  if l["side"] == "BUY" and not l["swept"])
    pull_dn = sum(l["strength"] for l in result["levels"]
                  if l["side"] == "SELL" and not l["swept"])
    total = pull_up + pull_dn
    if total <= 0:
        return None
    net = (pull_up - pull_dn) / total
    return round(net if direction == "LONG" else -net, 4)
