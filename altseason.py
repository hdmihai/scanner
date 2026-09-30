#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
altseason.py
=============
Faza ciclului altcoin season, evaluata la fiecare scanare pe date REALE de piata.

CADRUL (9 faze, dupa un bear market)
-------------------------------------
0 bear/capitulare -> 1 acumulare BTC -> 2 bull BTC -> 3 recuperare ETH ->
4 altseason large-cap -> 5 rotatie mid-cap -> 6 faza speculativa small-cap ->
7 euforie -> 8 distributie -> (inapoi la 0)

DATELE
------
- CoinGecko /global: dominanta BTC si ETH, capitalizare si volum total.
- CoinGecko /coins/markets (top 250): randamente 7z/30z/200z, distanta fata de
  ATH, volum - pentru latimea pietei pe niveluri (large/mid/small cap) si
  pentru a ESTIMA dominanta BTC de acum 30 si 200 de zile (capitalizarea de
  atunci = capitalizarea de acum / (1 + randament); ignora schimbarile de
  ofera, deci e o aproximare, declarata ca atare).
- Lumanari zilnice de pe exchange: randamentul EXACT pe 90 de zile, pentru
  indicele Altcoin Season in definitia CoinMarketCap - procentul din primele
  100 de altcoins (fara stablecoins) care au batut BTC in ultimele 90 de zile.
  Peste 75 = Altcoin Season, sub 25 = Bitcoin Season.

CE ESTE SI CE NU ESTE
---------------------
Clasificarea e un sistem de reguli TRANSPARENT: fiecare faza primeste un scor
din conditii masurabile (afisate pe dashboard ca "de ce"), iar faza cu scorul
cel mai mare castiga. Nu e un model antrenat - nu exista destule cicluri complete
de piata ca sa antrenezi statistic ceva pe 9 faze. Pragurile urmeaza descrierile
uzuale ale ciclului si sunt usor de ajustat. Predictia fazei urmatoare e ordinea
ciclului plus conditiile concrete care ar confirma trecerea - nu o certitudine.
"""

import json
import os
import time
import urllib.error
import urllib.request

CG = "https://api.coingecko.com/api/v3"
CG_KEY = os.environ.get("COINGECKO_API_KEY", "")
STATE_FILE = os.path.join("data", "altseason.json")
HISTORY_FILE = os.path.join("data", "altseason_history.json")
PERF90_TTL = 6 * 3600          # randamentele pe 90 de zile se reimprospateaza la 6 ore
HISTORY_KEEP = 720             # ~30 de zile la o scanare pe ora

STABLE = {"usdt", "usdc", "dai", "fdusd", "tusd", "usde", "usds", "pyusd", "usdd",
          "frax", "eurc", "eurt", "busd", "gusd", "usdp", "lusd", "crvusd", "susd",
          "usd0", "usdy", "rlusd", "usdg", "usd1", "buidl", "usyc", "ustb", "xaut", "paxg"}
WRAPPED = {"wbtc", "weth", "steth", "wsteth", "weeth", "cbbtc", "reth", "lbtc", "cbeth",
           "jitosol", "msol", "bnsol", "wbeth", "ezeth", "rseth", "meth", "solvbtc",
           "tbtc", "clbtc", "bbtc", "sweth", "oseth", "ethx", "stbtc", "fbtc", "susde"}

PHASES = [
    (0, "Bear market / capitulare",
     "Altcoins lovite puternic, lichiditatea dispare", "dominanta BTC, volume, capitalizare totala"),
    (1, "Acumulare / recuperare BTC",
     "Bitcoin incepe sa urce primul", "BTC in crestere, dominanta BTC de obicei in crestere"),
    (2, "Faza bull BTC",
     "BTC face miscarea principala; altcoins raman in urma", "dominanta BTC ridicata, ETH/BTC slab"),
    (3, "Recuperare ETH",
     "BTC incetineste, capitalul intra in ETH si large caps", "ETH/BTC in crestere, dominanta BTC incepe sa scada"),
    (4, "Altseason large-cap",
     "SOL, BNB si alte altcoins mari depasesc BTC", "latime in crestere, dominanta BTC in scadere"),
    (5, "Rotatie mid-cap",
     "Capitalul se muta spre capitalizari mai mici", "volume in crestere, multe sectoare pornesc simultan"),
    (6, "Faza speculativa small-cap",
     "Capitalul ajunge la proiecte mici, meme coins, narative", "explozie de volume, volatilitate, multipli"),
    (7, "Euforie / final",
     "Aproape orice pompeaza; FOMO si levier", "latime foarte mare, dominanta BTC foarte jos"),
    (8, "Distributie -> bear market",
     "Capitalul iese din risc; altcoins pierd rapid", "dominanta BTC poate reveni, alts/BTC se deterioreaza"),
]

# Nivelul de capitalizare pe care faza il favorizeaza - folosit pentru a alege
# unde se cauta proiectele cu semnale de crestere.
PHASE_TIER = {0: None, 1: None, 2: "large", 3: "large", 4: "large", 5: "mid",
              6: "small", 7: "small", 8: None}

# Cat favorizeaza faza pozitiile LONG pe altcoins, respectiv pe BTC, in [-1, 1].
# Alimenteaza evidenta "altseason" a agentului.
ALT_BIAS = {0: -0.8, 1: -0.5, 2: -0.4, 3: 0.2, 4: 0.5, 5: 0.7, 6: 0.6, 7: 0.2, 8: -0.8}
BTC_BIAS = {0: -0.6, 1: 0.5, 2: 0.7, 3: 0.3, 4: 0.2, 5: 0.0, 6: 0.0, 7: -0.2, 8: -0.6}


# ---------------------------------------------------------------------------
KEY_NOTE = []      # de ce a fost refuzata cheia Demo (raportat de diagnostic)


def _get_once(url, retries, use_key):
    headers = {"accept": "application/json"}
    if use_key and CG_KEY:
        headers["x-cg-demo-api-key"] = CG_KEY
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:
                time.sleep(15 * (attempt + 1))
                continue
            raise


def _get(url, retries=2):
    """Cu cheia Demo; daca e REFUZATA (invalida, sau cota lunara epuizata),
    o singura reincercare pe API-ul public gratuit, fara cheie - suficient pentru
    cele doua apeluri pe scanare. In productie, altseason a ramas fara date peste
    15 ore, in timp ce apelul scanerului, facut fara cheie, nu depindea de ea."""
    try:
        return _get_once(url, retries, use_key=True)
    except urllib.error.HTTPError as e:
        if not CG_KEY or e.code not in (400, 401, 403, 429):
            raise
        note = f"HTTP {e.code}"
        try:
            note += " " + e.read().decode()[:120]
        except Exception:
            pass
        KEY_NOTE.append(note.strip())
        return _get_once(url, 1, use_key=False)


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp, path)


def ramp(x, a, b):
    """0 sub a, 1 peste b, liniar intre ele (a > b pentru rampa descrescatoare)."""
    if x is None:
        return 0.5
    if a == b:
        return 1.0 if x >= b else 0.0
    t = (x - a) / (b - a)
    return max(0.0, min(1.0, t))


def band(x, a, b, c, d):
    """Trapez: 0 sub a, urca pana la 1 la b, 1 pana la c, coboara la 0 la d."""
    if x is None:
        return 0.5
    if x <= a or x >= d:
        return 0.0
    if x < b:
        return (x - a) / (b - a)
    if x <= c:
        return 1.0
    return (d - x) / (d - c)


def _median(v):
    v = sorted(x for x in v if x is not None)
    if not v:
        return None
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2


def _rel(r, rb):
    """Performanta relativa fata de BTC, in procente."""
    if r is None or rb is None:
        return None
    return ((1 + r / 100) / (1 + rb / 100) - 1) * 100


# ---------------------------------------------------------------------------
def fetch_market():
    glob = _get(f"{CG}/global").get("data") or {}
    markets = _get(f"{CG}/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250"
                   f"&page=1&price_change_percentage=7d,30d,200d")
    return glob, markets


def alt_universe(markets):
    """Altcoins eligibile: fara BTC, stablecoins si active impachetate/staked."""
    out = []
    for c in markets or []:
        sym = (c.get("symbol") or "").lower()
        if c.get("id") == "bitcoin" or sym in STABLE or sym in WRAPPED:
            continue
        out.append(c)
    return out


def perf_90d(exchange, bases, cache):
    """Randamentul exact pe 90 de zile, din lumanari zilnice de pe exchange.
    Cache de 6 ore: indicatorul pe 90 de zile se misca lent, nu merita
    ~100 de apeluri la fiecare scanare orara."""
    if cache and time.time() - cache.get("ts", 0) < PERF90_TTL and cache.get("perf"):
        return cache
    perf = {}
    if exchange is None:
        return cache or {"ts": 0, "perf": {}}
    markets = getattr(exchange, "markets", None) or {}
    for b in bases:
        sym = f"{b.upper()}/USDT"
        if markets and sym not in markets:
            continue
        try:
            o = exchange.fetch_ohlcv(sym, timeframe="1d", limit=92)
        except Exception:
            continue
        if not o or len(o) < 91:
            continue
        closes = [c[4] for c in o[:-1]]           # fara bara zilnica neinchisa
        if len(closes) >= 91 and closes[-91] > 0:
            perf[b.lower()] = round((closes[-1] / closes[-91] - 1) * 100, 3)
    return {"ts": time.time(), "perf": perf}


def indicators(glob, markets, perf90):
    by_id = {c.get("id"): c for c in markets or []}
    btc, eth = by_id.get("bitcoin") or {}, by_id.get("ethereum") or {}
    r = lambda c, k: c.get(f"price_change_percentage_{k}_in_currency")
    rb30, rb7, rb200 = r(btc, "30d"), r(btc, "7d"), r(btc, "200d")

    mcp = glob.get("market_cap_percentage") or {}
    btc_d = mcp.get("btc")

    # dominanta BTC estimata in trecut, din randamentele top 250
    def dom_then(k):
        now_tot = then_tot = btc_now = btc_then = 0.0
        for c in markets or []:
            cap, rk = c.get("market_cap") or 0, r(c, k)
            if not cap or rk is None or (c.get("symbol") or "").lower() in STABLE:
                continue
            then = cap / (1 + rk / 100)
            now_tot += cap
            then_tot += then
            if c.get("id") == "bitcoin":
                btc_now, btc_then = cap, then
        if not (now_tot and then_tot and btc_now and btc_d):
            return None
        return btc_d * (btc_then / then_tot) / (btc_now / now_tot)

    btc_d30, btc_d200 = dom_then("30d"), dom_then("200d")

    alts = alt_universe(markets)
    top100 = alts[:100]
    tiers = {"large": alts[:19], "mid": alts[19:99], "small": alts[99:]}

    def breadth(group, k, rb):
        vals = [r(c, k) for c in group if r(c, k) is not None]
        if not vals or rb is None:
            return None
        return round(100 * sum(1 for v in vals if v > rb) / len(vals), 1)

    perf = (perf90 or {}).get("perf") or {}
    btc90 = perf.get("btc")
    beat90 = [perf[(c.get("symbol") or "").lower()] > btc90 for c in top100
              if (c.get("symbol") or "").lower() in perf] if btc90 is not None else []
    alt_index = round(100 * sum(beat90) / len(beat90), 1) if len(beat90) >= 40 else None

    med_rel = {t: _median([_rel(r(c, "30d"), rb30) for c in g]) for t, g in tiers.items()}
    small = tiers["small"]
    spec = (sum(1 for c in small if (r(c, "30d") or 0) > 50) / len(small)) if small else None
    vol_alts = sum(c.get("total_volume") or 0 for c in alts)
    vol_all = vol_alts + (btc.get("total_volume") or 0)

    return {
        "btc_d": round(btc_d, 2) if btc_d else None,
        "btc_d_30d_ago": round(btc_d30, 2) if btc_d30 else None,
        "btc_d_200d_ago": round(btc_d200, 2) if btc_d200 else None,
        "btc_d_delta30": round(btc_d - btc_d30, 2) if (btc_d and btc_d30) else None,
        "eth_d": round(mcp.get("eth"), 2) if mcp.get("eth") else None,
        "eth_btc": round(eth["current_price"] / btc["current_price"], 5)
                   if eth.get("current_price") and btc.get("current_price") else None,
        "eth_btc_30d": round(_rel(r(eth, "30d"), rb30), 2) if _rel(r(eth, "30d"), rb30) is not None else None,
        "eth_btc_200d": round(_rel(r(eth, "200d"), rb200), 2) if _rel(r(eth, "200d"), rb200) is not None else None,
        "btc_r7": rb7, "btc_r30": rb30, "btc_r200": rb200,
        "btc_ath_dd": btc.get("ath_change_percentage"),
        "btc_price": btc.get("current_price"),
        "alt_index_90d": alt_index,
        "alt_index_coverage": len(beat90),
        "breadth30": breadth(top100, "30d", rb30),
        "breadth30_tier": {t: breadth(g, "30d", rb30) for t, g in tiers.items()},
        "med_rel30_tier": {t: (round(v, 2) if v is not None else None) for t, v in med_rel.items()},
        "spec_share": round(spec, 3) if spec is not None else None,
        "alt_vol_share": round(vol_alts / vol_all, 3) if vol_all else None,
        "total_mcap": (glob.get("total_market_cap") or {}).get("usd"),
        "total_vol": (glob.get("total_volume") or {}).get("usd"),
        "mcap_chg_24h": glob.get("market_cap_change_percentage_24h_usd"),
    }


def season_label(ai):
    if ai is None:
        return "necunoscut"
    return "Altcoin Season" if ai >= 75 else ("Bitcoin Season" if ai <= 25 else "zona mixta")


def classify(ind, history=None, prev_scores=None):
    """Scorul fiecarei faze, din conditii masurabile. Returneaza faza, increderea,
    motivele si conditiile care ar confirma trecerea la faza urmatoare."""
    g = ind.get
    ai = g("alt_index_90d") if g("alt_index_90d") is not None else g("breadth30")
    br = g("breadth30")
    bt = g("breadth30_tier") or {}
    mr = g("med_rel30_tier") or {}
    d30 = g("btc_d_delta30")
    dd = g("btc_ath_dd")
    hist_ai = [h.get("ai") for h in (history or [])[-720:] if h.get("ai") is not None]
    ai_max = max(hist_ai) if hist_ai else None

    f = {   # conditii normalizate in [0, 1]
        "bear": max(ramp(-(g("btc_r200") or 0), 10, 40), ramp(-(dd or 0), 45, 70)),
        "btc_down30": ramp(-(g("btc_r30") or 0), 0, 20),
        "btc_up30": ramp(g("btc_r30"), 0, 15),
        "btc_strong": ramp(g("btc_r200"), 10, 60),
        "below_ath": ramp(-(dd or 0), 20, 50),
        "near_ath": ramp(dd, -35, -10),
        "btcd_high": ramp(g("btc_d"), 50, 60),
        "btcd_low": ramp(-(g("btc_d") or 100), -50, -40),
        "btcd_up": ramp(d30, 0, 2),
        "btcd_down": ramp(-(d30 if d30 is not None else 0), 0, 2) if d30 is not None else 0.5,
        "ethbtc_up": ramp(g("eth_btc_30d"), 0, 15),
        "ethbtc_down": ramp(-(g("eth_btc_30d") or 0), 0, 10),
        "btc_flat": 1 - ramp(abs(g("btc_r30") or 0), 5, 20),
        "large_lead": ramp(mr.get("large"), 0, 15),
        "mid_lead": ramp((mr.get("mid") or 0) - (mr.get("large") or 0), 0, 15) * ramp(mr.get("mid"), 0, 20),
        "small_lead": ramp((mr.get("small") or 0) - (mr.get("mid") or 0), 0, 20) * ramp(mr.get("small"), 0, 30),
        "spec": ramp(g("spec_share"), 0.05, 0.30),
        "alt_vol": ramp(g("alt_vol_share"), 0.40, 0.60),
        "deterioration": (ramp((ai_max or 0) - (ai or 0), 10, 30) * ramp(ai_max, 50, 75)
                          if ai_max is not None else ramp((ai or 0) - (br or 0), 10, 30)),
        "alts_weak": ramp(-(mr.get("large") or 0), 0, 10),
    }
    raw = {
        0: 0.6 * f["bear"] + 0.2 * f["btc_down30"] + 0.2 * (1 - (br or 50) / 100),
        1: (0.35 * f["below_ath"] + 0.25 * f["btc_up30"] + 0.2 * f["btcd_up"]
            + 0.2 * ramp(25 - (ai or 50), 0, 20)) * (1 - 0.5 * f["btc_down30"]),
        2: (0.25 * f["btc_strong"] + 0.2 * f["near_ath"] + 0.2 * f["btcd_high"]
            + 0.15 * f["ethbtc_down"] + 0.2 * ramp(35 - (ai or 50), 0, 25)),
        3: (0.35 * f["ethbtc_up"] + 0.2 * f["btcd_down"] + 0.15 * f["btc_flat"]
            + 0.15 * f["btcd_high"] + 0.15 * band(ai, 15, 30, 50, 62)),
        4: (0.3 * f["large_lead"] + 0.25 * ramp(bt.get("large"), 50, 75)
            + 0.2 * f["btcd_down"] + 0.25 * band(ai, 38, 50, 65, 78)),
        5: (0.35 * f["mid_lead"] + 0.25 * ramp(br, 55, 75) + 0.15 * f["alt_vol"]
            + 0.25 * band(ai, 52, 62, 75, 88)),
        # trapez, nu rampa: peste ~85 piata intra in euforie (faza 7), iar faza 6
        # trebuie sa piarda teren - altfel cele doua ieseau la egalitate
        6: 0.35 * f["small_lead"] + 0.3 * f["spec"] + 0.15 * f["btcd_down"] + 0.2 * band(ai, 62, 72, 84, 95),
        7: 0.3 * ramp(ai, 75, 90) + 0.25 * f["btcd_low"] + 0.25 * ramp(br, 75, 90) + 0.2 * f["spec"],
        8: (0.35 * f["deterioration"] + 0.25 * f["btcd_up"] + 0.2 * f["alts_weak"]
            + 0.2 * ramp(ai_max if ai_max is not None else ai, 50, 75)),
    }
    # netezire cu scorurile anterioare: faza nu sare de la o ora la alta
    # pe zgomot. Media ponderata 50/50 cu evaluarea precedenta.
    scores = {k: (0.5 * v + 0.5 * prev_scores[str(k)]) if prev_scores and str(k) in prev_scores else v
              for k, v in raw.items()}
    order = sorted(scores.items(), key=lambda kv: -kv[1])
    phase, top = order[0]
    second = order[1][1]
    confidence = round(max(0.0, min(1.0, 0.5 * top + 0.5 * (top - second) / (top or 1))), 3)

    reasons, missing = _reasons(phase, ind, f)
    nxt = (phase + 1) % 9
    # TRANZITIE: cand faza de pe locul 2 e vecina si scorurile sunt apropiate,
    # o spun explicit - piata e intre doua faze, nu ferm intr-una.
    ru = order[1][0]
    transition = (abs(ru - phase) == 1 or {ru, phase} == {0, 8}) and (top - second) < 0.1
    return {"phase": phase, "name": PHASES[phase][1], "what": PHASES[phase][2],
            "watch": PHASES[phase][3], "confidence": confidence,
            "scores": {str(k): round(v, 4) for k, v in scores.items()},
            "runner_up": {"phase": order[1][0], "name": PHASES[order[1][0]][1]},
            "reasons": reasons, "missing": missing,
            "transition": (f"{min(ru, phase)} -> {max(ru, phase)}" if transition else None),
            "next": {"phase": nxt, "name": PHASES[nxt][1],
                                         "triggers": _triggers(phase, ind)},
            "season": season_label(g("alt_index_90d"))}


def _fmt(v, suf="", nd=1, sign=False):
    if v is None:
        return "n/d"
    return f"{v:+.{nd}f}{suf}" if sign else f"{v:.{nd}f}{suf}"


def _reasons(phase, ind, f):
    g = ind.get
    bt = g("breadth30_tier") or {}
    mr = g("med_rel30_tier") or {}
    items = {
        "bear": f"BTC {_fmt(g('btc_r200'), '%', 0, True)} pe 200 de zile, {_fmt(g('btc_ath_dd'), '%', 0)} sub ATH",
        "btc_up30": f"BTC {_fmt(g('btc_r30'), '%', 1, True)} pe 30 de zile",
        "btc_strong": f"BTC {_fmt(g('btc_r200'), '%', 0, True)} pe 200 de zile",
        "near_ath": f"BTC la {_fmt(g('btc_ath_dd'), '%', 1)} de maximul istoric",
        "btcd_high": f"dominanta BTC {_fmt(g('btc_d'), '%')}",
        "btcd_up": f"dominanta BTC {_fmt(g('btc_d_delta30'), ' pp', 1, True)} fata de acum 30 de zile (estimat)",
        "btcd_down": f"dominanta BTC {_fmt(g('btc_d_delta30'), ' pp', 1, True)} fata de acum 30 de zile (estimat)",
        "ethbtc_up": f"ETH/BTC {_fmt(g('eth_btc_30d'), '%', 1, True)} pe 30 de zile",
        "ethbtc_down": f"ETH/BTC {_fmt(g('eth_btc_30d'), '%', 1, True)} pe 30 de zile",
        "large_lead": f"large caps: mediana {_fmt(mr.get('large'), '%', 1, True)} fata de BTC, "
                      f"{_fmt(bt.get('large'), '%', 0)} bat BTC",
        "mid_lead": f"mid caps conduc: mediana {_fmt(mr.get('mid'), '%', 1, True)} vs large "
                    f"{_fmt(mr.get('large'), '%', 1, True)}",
        "small_lead": f"small caps conduc: mediana {_fmt(mr.get('small'), '%', 1, True)} fata de BTC",
        "spec": f"{_fmt((g('spec_share') or 0) * 100, '%', 0)} din small caps au peste +50% in 30 de zile",
        "btcd_low": f"dominanta BTC {_fmt(g('btc_d'), '%')} - foarte jos",
        "deterioration": "latimea altcoins scade fata de maximul recent",
        "alts_weak": f"large caps: mediana {_fmt(mr.get('large'), '%', 1, True)} fata de BTC",
    }
    keys = {0: ["bear", "btcd_high"], 1: ["btc_up30", "btcd_up"],
            2: ["btc_strong", "near_ath", "btcd_high", "ethbtc_down"],
            3: ["ethbtc_up", "btcd_down", "btcd_high"], 4: ["large_lead", "btcd_down"],
            5: ["mid_lead"], 6: ["small_lead", "spec"], 7: ["btcd_low", "spec"],
            8: ["deterioration", "btcd_up", "alts_weak"]}[phase]
    out = [items[k] for k in sorted(keys, key=lambda k: -f.get(k, 0)) if f.get(k, 0) >= 0.3]
    # CE NU SE POTRIVESTE INCA: conditiile tipice fazei care lipsesc acum. Pe
    # piata reala din sep. 2026, faza 3 cere dominanta BTC in scadere, dar ea
    # inca urca - asta trebuie spus, nu ascuns sub eticheta fazei.
    miss = [items[k] for k in keys if f.get(k, 0) < 0.3]
    ai = g("alt_index_90d")
    out.append(f"indice Altcoin Season 90z: {_fmt(ai, '', 0)} ({season_label(ai)})"
               if ai is not None else f"latime 30z: {_fmt(g('breadth30'), '%', 0)} din top 100 bat BTC")
    return out, miss


def _triggers(phase, ind):
    """Conditiile concrete care ar confirma trecerea la faza urmatoare."""
    g = ind.get
    bt = g("breadth30_tier") or {}
    mr = g("med_rel30_tier") or {}
    t = {
        0: [f"BTC pozitiv pe 30 de zile (acum {_fmt(g('btc_r30'), '%', 1, True)})",
            "capitalizarea totala se stabilizeaza, volumele revin"],
        1: [f"BTC peste +30% pe 200 de zile (acum {_fmt(g('btc_r200'), '%', 0, True)})",
            f"dominanta BTC peste 55% (acum {_fmt(g('btc_d'), '%')})"],
        2: [f"ETH/BTC peste +5% pe 30 de zile (acum {_fmt(g('eth_btc_30d'), '%', 1, True)})",
            f"dominanta BTC incepe sa scada (acum {_fmt(g('btc_d_delta30'), ' pp/30z', 1, True)})"],
        3: [f"peste 60% din large caps bat BTC (acum {_fmt(bt.get('large'), '%', 0)})",
            f"dominanta BTC sub {_fmt((g('btc_d') or 0) - 2, '%')} (acum {_fmt(g('btc_d'), '%')})"],
        4: [f"mid caps depasesc large caps (mediana mid {_fmt(mr.get('mid'), '%', 1, True)} "
            f"vs large {_fmt(mr.get('large'), '%', 1, True)})",
            f"latimea top 100 peste 60% (acum {_fmt(g('breadth30'), '%', 0)})"],
        5: [f"small caps conduc (mediana {_fmt(mr.get('small'), '%', 1, True)} fata de BTC)",
            f"peste 15% din small caps cu +50% in 30 de zile (acum {_fmt((g('spec_share') or 0) * 100, '%', 0)})"],
        6: [f"indice Altcoin Season peste 75 (acum {_fmt(g('alt_index_90d'), '', 0)})",
            f"dominanta BTC sub 45% (acum {_fmt(g('btc_d'), '%')})"],
        7: ["latimea incepe sa scada de la maxim",
            f"dominanta BTC isi revine (acum {_fmt(g('btc_d_delta30'), ' pp/30z', 1, True)})"],
        8: [f"BTC sub -20% pe 200 de zile (acum {_fmt(g('btc_r200'), '%', 0, True)})",
            "volumele scad, altcoins continua sa piarda fata de BTC"],
    }
    return t[phase]


def growth_candidates(markets, perf90, phase, scan_results=None, top=8):
    """Proiecte cu semnale de crestere: forta relativa fata de BTC pe 7 si 30 de
    zile (si pe 90 unde exista), in nivelul de capitalizare favorizat de faza.
    Cele confirmate si de scanerul nostru (semnal LONG activ) sunt marcate."""
    by_id = {c.get("id"): c for c in markets or []}
    btc = by_id.get("bitcoin") or {}
    r = lambda c, k: c.get(f"price_change_percentage_{k}_in_currency")
    rb7, rb30 = r(btc, "7d"), r(btc, "30d")
    perf = (perf90 or {}).get("perf") or {}
    btc90 = perf.get("btc")
    alts = alt_universe(markets)
    tier_of = {c.get("id"): ("large" if i < 19 else "mid" if i < 99 else "small")
               for i, c in enumerate(alts)}
    want = PHASE_TIER.get(phase)
    longs = {}
    for s in scan_results or []:
        if s.get("direction") == "LONG":
            longs[s["symbol"].split("/")[0].lower()] = s
    rows = []
    for c in alts:
        rs30, rs7 = _rel(r(c, "30d"), rb30), _rel(r(c, "7d"), rb7)
        sym = (c.get("symbol") or "").lower()
        rs90 = _rel(perf[sym], btc90) if (sym in perf and btc90 is not None) else None
        if rs30 is None or rs7 is None or rs30 <= 0 or rs7 <= 0:
            continue
        if want and tier_of.get(c.get("id")) != want:
            continue
        score = 0.5 * min(rs30, 100) + 0.3 * min(rs7, 50) + 0.2 * min(rs90 or 0, 150)
        rows.append({"symbol": sym.upper(), "name": c.get("name"), "rank": c.get("market_cap_rank"),
                     "tier": tier_of.get(c.get("id")), "rs7": round(rs7, 1), "rs30": round(rs30, 1),
                     "rs90": round(rs90, 1) if rs90 is not None else None,
                     "score": round(score, 2), "scanner": sym in longs,
                     "scanner_score": (longs.get(sym) or {}).get("risk_adjusted")})
    rows.sort(key=lambda x: (-x["scanner"], -x["score"]))
    return rows[:top]


def update(exchange=None, scan_results=None):
    """Punctul de intrare, apelat de scaner la fiecare rulare. Nu arunca exceptii:
    la o problema de date pastreaza ultima evaluare si o marcheaza ca veche."""
    state = _load(STATE_FILE, {})
    try:
        glob, markets = fetch_market()
    except Exception as e:
        # CAUZA EXACTA, salvata: in productie datele au ramas vechi peste 10 ore,
        # iar motivul aparea doar in jurnalul Actions. Acum il vede diagnosticul.
        err = str(e)[:200]
        try:
            err += " " + e.read().decode()[:120]
        except Exception:
            pass
        print(f"[!] altseason: CoinGecko indisponibil ({err}) - pastrez evaluarea anterioara.")
        state = state or {}
        state.update(stale=True, last_error=err.strip(),
                     last_error_at=time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()))
        _save(STATE_FILE, state)
        return state if state.get("classification") else None

    alts = alt_universe(markets)
    bases = ["btc"] + [(c.get("symbol") or "") for c in alts[:100]]
    perf90 = perf_90d(exchange, bases, state.get("perf90"))
    ind = indicators(glob, markets, perf90)
    history = _load(HISTORY_FILE, [])
    cls = classify(ind, history, (state.get("classification") or {}).get("scores"))
    cands = growth_candidates(markets, perf90, cls["phase"], scan_results)

    state = {"ts": time.time(), "when": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
             "stale": False, "last_error": None,
             "key_note": (f"cheia CoinGecko a fost refuzata ({KEY_NOTE[-1]}); datele vin din API-ul "
                          f"public gratuit" if KEY_NOTE else None), "indicators": ind, "classification": cls,
             "candidates": cands, "perf90": perf90,
             "alt_bias": round(ALT_BIAS[cls["phase"]] * cls["confidence"], 3),
             "btc_bias": round(BTC_BIAS[cls["phase"]] * cls["confidence"], 3)}
    # CONTEXTUL ISTORIC PE 10 ANI (altseason_history.py): ciclurile, pozitia de
    # acum fata de ele, analogiile si predictiile invatate. Actualizat zilnic
    # (zilele noi inchise) si reconstruit saptamanal; nu opreste niciodata evaluarea.
    try:
        import altseason_history as _AH
        state["history"] = _AH.update(exchange)
    except Exception as _e:
        print(f"[!] context istoric altseason: {_e}")
        state["history"] = None
    _save(STATE_FILE, state)
    history.append({"ts": int(state["ts"]), "phase": cls["phase"], "conf": cls["confidence"],
                    "btc_d": ind.get("btc_d"), "eth_btc": ind.get("eth_btc"),
                    "ai": ind.get("alt_index_90d"), "br": ind.get("breadth30")})
    _save(HISTORY_FILE, history[-HISTORY_KEEP:])
    print(f"Altseason: faza {cls['phase']} - {cls['name']} (incredere {cls['confidence']:.0%}) | "
          f"BTC.D {ind.get('btc_d')}% | ETH/BTC 30z {ind.get('eth_btc_30d')}% | "
          f"indice 90z {ind.get('alt_index_90d')} ({cls['season']})")
    return state
