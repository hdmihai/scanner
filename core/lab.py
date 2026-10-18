# -*- coding: utf-8 -*-
"""core.lab - LABORATORUL AGENTULUI: ce invata agentul din memorie si cum foloseste ce a invatat
(NUCLEU, fara I/O; antrenarea cu numpy e in research_lab.py, scanarea live in lab_live.py).

DE CE EXISTA
------------
Pana aici, singurul semnal era o regula scrisa de mana (EMA20/50 + banda RSI + pullback), iar partea
care invata (core/agent.py) doar FILTRA planurile acelei reguli. Masurat corect, regula nu are avantaj
(+0.001R/plan pe 10 ani), deci un filtru peste ea nu are ce gasi. Depozitul de memorie are insa milioane
de lumanari 4h (703 monede Binance din 2017 + OKX), pe care nu le folosea nimeni ca sa invete.

Laboratorul invata DIRECT din memorie, fara regula de semnal:
  1. CARACTERISTICI identice live si pe istoric (aceeasi functie, `FeatureStream`), calculate la
     inchiderea fiecarei bare de 4h: trend, momentum, volatilitate, volum relativ, pozitia in
     intervalul de 7 si 30 de zile, distanta fata de maximul/minimul de 30 de zile (lichiditatea
     de pe ambele parti), forta relativa fata de BTC, regimul BTC, forma lumanarii.
  2. ETICHETA fiecarei bare: cat R ar fi facut o tranzactie LONG si una SHORT intrata la deschiderea
     barei urmatoare, cu SL la 1.5 ATR si TP la 2R, inchisa dupa cel mult 48 de bare, cu toate
     ambiguitatile din bara rezolvate impotriva tranzactiei si cu comisioane (`bracket_outcome`).
  3. MODELE antrenate pe aceste exemple (regresie si arbori gradient boosting), validate
     walk-forward pe ani si pe o perioada finala REZERVATA, pe care nu s-a ales nimic.
  4. PROMOVARE cu dovada: CERCETARE -> SHADOW (trece walk-forward si perioada rezervata) -> ACTIV
     (confirmat de TESTUL INAINTE: fiecare model publicat alege pe toate monedele bursei, pe barele de
     dupa publicarea lui, etichetate din lumanarile reale) -> RETRAS (testul inainte il infirma).
     Doar un model ACTIV recomanda tranzactii pe dashboard.
  5. INVATARE DIN FIECARE SCANARE: scanarea salveaza caracteristicile tuturor candidatilor (nu doar
     ale planurilor emise) si scorul modelului de atunci; jobul de memorie le eticheteaza cand se
     inchid si le adauga la datele de antrenare.

Modelul se publica ca JSON (praguri si valori, fara numpy), deci scanarea orara il aplica in Python pur.
"""

import math
from collections import deque

LAB_VERSION = 1

# ------------------------------------------------------------------ tranzactia invatata
SL_ATR = 1.5            # stopul, in ATR(14) la inchiderea barei de semnal
TP_R = 2.0              # tinta, in R
MAX_HOLD = 48           # bare (8 zile pe 4h)
COST_FRAC = 0.002       # comision + alunecare dus-intors, ca fractiune din pret (0.1% pe parte)

# ------------------------------------------------------------------ caracteristicile
MIN_BARS = 200          # bare necesare pentru o valoare valida (fereastra cea mai lunga: 180 + 1)
WIN_LONG = 180          # 30 de zile de bare 4h
WIN_MID = 42            # 7 zile
FEATURES = [
    "ret1", "ret6", "ret42", "ret180",            # randamente log: 4h, 1 zi, 7 zile, 30 de zile
    "atr_pct", "vol_ratio",                        # volatilitatea si contractia/expansiunea ei
    "z_ema20", "ema20_50", "z_sma180", "ema50_slope",   # trend, in ATR
    "rsi14", "macd_hist",                          # momentum
    "pos180", "dist_hi180", "dist_lo180", "pos42",  # pozitia fata de lichiditatea de 30 si 7 zile
    "bars_hi180", "bars_lo180",                    # cat de recent s-a facut maximul / minimul de 30 de zile
    "vol_z", "vol_trend",                          # volumul relativ (invariant la bursa)
    "rs42", "rs180",                               # forta relativa fata de BTC
    "btc_ret42", "btc_z_sma180", "btc_atr_pct",    # regimul pietei
    "body", "upper_wick", "lower_wick",            # forma ultimei lumanari, in ATR
]
FEATURE_TEXT = {
    "ret1": "randamentul ultimei bare de 4h", "ret6": "randamentul pe 1 zi", "ret42": "randamentul pe 7 zile",
    "ret180": "randamentul pe 30 de zile", "atr_pct": "volatilitatea (ATR % din pret)",
    "vol_ratio": "volatilitatea recenta fata de cea pe 30 de zile", "z_ema20": "distanta fata de EMA20, in ATR",
    "ema20_50": "EMA20 fata de EMA50, in ATR", "z_sma180": "distanta fata de media pe 30 de zile, in ATR",
    "ema50_slope": "panta EMA50 pe o zi, in ATR", "rsi14": "RSI 14", "macd_hist": "histograma MACD, in ATR",
    "pos180": "pozitia in intervalul de 30 de zile", "dist_hi180": "distanta pana la maximul de 30 de zile (ATR)",
    "dist_lo180": "distanta pana la minimul de 30 de zile (ATR)", "pos42": "pozitia in intervalul de 7 zile",
    "bars_hi180": "cat de demult s-a facut maximul de 30 de zile", "bars_lo180": "cat de demult s-a facut minimul de 30 de zile",
    "vol_z": "volumul barei fata de media pe 5 zile", "vol_trend": "volumul pe o zi fata de cel pe 10 zile",
    "rs42": "forta relativa fata de BTC pe 7 zile", "rs180": "forta relativa fata de BTC pe 30 de zile",
    "btc_ret42": "randamentul BTC pe 7 zile", "btc_z_sma180": "BTC fata de media lui pe 30 de zile (ATR)",
    "btc_atr_pct": "volatilitatea BTC", "body": "corpul ultimei lumanari (ATR)",
    "upper_wick": "fitilul de sus al ultimei lumanari (ATR)", "lower_wick": "fitilul de jos al ultimei lumanari (ATR)",
}

# monede care nu se tranzactioneaza ca riscul pietei (stabile, ambalate, cu efect de levier)
EXCLUDE_BASES = {
    "USDT", "USDC", "BUSD", "TUSD", "DAI", "FDUSD", "USDP", "PAX", "UST", "USTC", "USDD", "PYUSD", "USDE",
    "USDS", "EUR", "GBP", "AUD", "TRY", "BRL", "EURI", "AEUR", "XUSD", "BFUSD", "RLUSD", "USD1", "SUSD",
    "WBTC", "WETH", "WBETH", "BETH", "STETH", "WSTETH", "CBBTC", "PAXG", "XAUT",
}
LEVERAGED_SUFFIX = ("UP", "DOWN", "BULL", "BEAR")
MIN_DOLLAR_BAR = 250_000.0     # lichiditate minima: valoarea medie tranzactionata pe bara de 4h (7 zile)


def tradable_base(symbol):
    """Simbolul intra in laborator? Fara monede stabile, ambalate sau cu efect de levier."""
    base = str(symbol).split("/")[0].upper()
    if base in EXCLUDE_BASES:
        return False
    return not (len(base) > 4 and base.endswith(LEVERAGED_SUFFIX))


class _Window:
    """Suma, suma patratelor si extremele pe o fereastra glisanta (deque monotone)."""
    __slots__ = ("n", "vals", "s", "k")

    def __init__(self, n):
        self.n, self.vals, self.s, self.k = n, deque(), 0.0, 0

    def push(self, v):
        self.vals.append(v)
        self.s += v
        if len(self.vals) > self.n:
            self.s -= self.vals.popleft()
        # suma glisanta acumuleaza erori de rotunjire (si poate iesi negativa pe volume zero):
        # o recalculez exact la fiecare n valori - cost O(1) amortizat
        self.k += 1
        if self.k >= self.n:
            self.k = 0
            self.s = math.fsum(self.vals)

    def full(self):
        return len(self.vals) >= self.n

    def mean(self):
        return self.s / len(self.vals)

    def pos_mean(self):
        """Media unei serii nenegative (volum): erorile de rotunjire nu o pot face negativa."""
        return max(self.s, 0.0) / len(self.vals)

    def std(self):
        k = len(self.vals)
        if k < 2:
            return 0.0
        # recalculez exact la nevoie: sumele glisante acumuleaza erori de rotunjire
        m = sum(self.vals) / k
        return math.sqrt(max(sum((x - m) ** 2 for x in self.vals) / (k - 1), 0.0))


class _Extreme:
    """Maximul (sau minimul) pe ultimele n valori si pozitia lui - deque monoton, O(1) amortizat."""
    __slots__ = ("n", "dq", "is_max")

    def __init__(self, n, is_max):
        self.n, self.dq, self.is_max = n, deque(), is_max

    def push(self, i, v):
        dq = self.dq
        if self.is_max:
            while dq and dq[-1][1] <= v:
                dq.pop()
        else:
            while dq and dq[-1][1] >= v:
                dq.pop()
        dq.append((i, v))
        while dq[0][0] <= i - self.n:
            dq.popleft()

    def value(self):
        return self.dq[0][1]

    def index(self):
        return self.dq[0][0]


class _Market:
    """Starea unei serii (o moneda sau BTC): EMA, ATR/RSI Wilder, MACD, ferestre."""

    def __init__(self):
        self.i = -1
        self.closes = deque(maxlen=WIN_LONG + 1)
        self.ema20 = self.ema50 = self.ema12 = self.ema26 = self.sig = None
        self.ema50_hist = deque(maxlen=7)
        self.atr = None
        self.avg_gain = self.avg_loss = None
        self.n_tr = 0
        self.tr_seed = []
        self.rsi_seed = []
        self.sma180 = _Window(WIN_LONG)
        self.prev_close = None

    def push(self, o, h, l, c):
        self.i += 1
        a20, a50, a12, a26, a9 = 2 / 21, 2 / 51, 2 / 13, 2 / 27, 2 / 10
        self.ema20 = c if self.ema20 is None else self.ema20 + a20 * (c - self.ema20)
        self.ema50 = c if self.ema50 is None else self.ema50 + a50 * (c - self.ema50)
        self.ema12 = c if self.ema12 is None else self.ema12 + a12 * (c - self.ema12)
        self.ema26 = c if self.ema26 is None else self.ema26 + a26 * (c - self.ema26)
        macd = self.ema12 - self.ema26
        self.sig = macd if self.sig is None else self.sig + a9 * (macd - self.sig)
        self.macd_hist = macd - self.sig
        self.ema50_hist.append(self.ema50)
        pc = self.prev_close
        tr = (h - l) if pc is None else max(h - l, abs(h - pc), abs(l - pc))
        if self.atr is None:
            self.tr_seed.append(tr)
            if len(self.tr_seed) == 14:
                self.atr = sum(self.tr_seed) / 14
        else:
            self.atr = (self.atr * 13 + tr) / 14
        if pc is not None:
            ch = c - pc
            g, lo = max(ch, 0.0), max(-ch, 0.0)
            if self.avg_gain is None:
                self.rsi_seed.append((g, lo))
                if len(self.rsi_seed) == 14:
                    self.avg_gain = sum(x for x, _ in self.rsi_seed) / 14
                    self.avg_loss = sum(y for _, y in self.rsi_seed) / 14
            else:
                self.avg_gain = (self.avg_gain * 13 + g) / 14
                self.avg_loss = (self.avg_loss * 13 + lo) / 14
        self.closes.append(c)
        self.sma180.push(c)
        self.prev_close = c

    def ret(self, k):
        if len(self.closes) <= k or self.closes[-1 - k] <= 0 or self.closes[-1] <= 0:
            return None
        return math.log(self.closes[-1] / self.closes[-1 - k])

    def rsi(self):
        if self.avg_gain is None:
            return None
        if self.avg_loss == 0:
            return 100.0
        return 100 - 100 / (1 + self.avg_gain / self.avg_loss)


class FeatureStream:
    """Caracteristicile la inchiderea fiecarei bare, calculate incremental - ACEEASI functie pentru
    scanarea live (cele ~260 de bare aduse de la bursa) si pentru istoricul complet din memorie.
    Ferestrele au cel mult WIN_LONG bare, iar mediile exponentiale (EMA20/50, MACD, ATR/RSI Wilder)
    uita conditia initiala dupa ~200 de bare: pe ultima bara, live si istoric dau aceleasi valori
    (verificat de tests/lab_check.py)."""

    def __init__(self):
        self.m = _Market()
        self.hi180, self.lo180 = _Extreme(WIN_LONG, True), _Extreme(WIN_LONG, False)
        self.hi42, self.lo42 = _Extreme(WIN_MID, True), _Extreme(WIN_MID, False)
        self.r30, self.r180 = _Window(30), _Window(WIN_LONG)
        self.v6, self.v30, self.v60 = _Window(6), _Window(30), _Window(60)
        self.dv42 = _Window(WIN_MID)

    def push(self, bar, btc=None):
        """`bar` = [ts, open, high, low, close, volume]; `btc` = FeatureStream-ul BTC, deja avansat la
        aceeasi bara (sau None pentru BTC insusi). Intoarce dict-ul caracteristicilor sau None cand
        nu exista inca destula istorie."""
        _ts, o, h, l, c, v = bar[:6]
        v = float(v or 0.0)
        m = self.m
        m.push(o, h, l, c)
        i = m.i
        self.hi180.push(i, h)
        self.lo180.push(i, l)
        self.hi42.push(i, h)
        self.lo42.push(i, l)
        r1 = m.ret(1)
        if r1 is not None:
            self.r30.push(r1)
            self.r180.push(r1)
        self.v6.push(v)
        self.v30.push(v)
        self.v60.push(v)
        self.dv42.push(v * c)
        if i + 1 < MIN_BARS or not m.atr or m.atr <= 0 or c <= 0:
            return None
        atr = m.atr
        hi, lo = self.hi180.value(), self.lo180.value()
        hi7, lo7 = self.hi42.value(), self.lo42.value()
        s180 = self.r180.std()
        f = {
            "ret1": r1, "ret6": m.ret(6), "ret42": m.ret(WIN_MID), "ret180": m.ret(WIN_LONG),
            "atr_pct": atr / c,
            "vol_ratio": (self.r30.std() / s180) if s180 > 0 else None,
            "z_ema20": (c - m.ema20) / atr, "ema20_50": (m.ema20 - m.ema50) / atr,
            "z_sma180": (c - m.sma180.mean()) / atr,
            "ema50_slope": (m.ema50_hist[-1] - m.ema50_hist[0]) / atr if len(m.ema50_hist) == 7 else None,
            "rsi14": m.rsi(), "macd_hist": m.macd_hist / atr,
            "pos180": (c - lo) / (hi - lo) if hi > lo else 0.5,
            "dist_hi180": (hi - c) / atr, "dist_lo180": (c - lo) / atr,
            "pos42": (c - lo7) / (hi7 - lo7) if hi7 > lo7 else 0.5,
            "bars_hi180": (i - self.hi180.index()) / WIN_LONG, "bars_lo180": (i - self.lo180.index()) / WIN_LONG,
            "vol_z": math.log((max(v, 0.0) + 1e-12) / (self.v30.pos_mean() + 1e-12)),
            "vol_trend": math.log((self.v6.pos_mean() + 1e-12) / (self.v60.pos_mean() + 1e-12)),
            "body": (c - o) / atr, "upper_wick": (h - max(o, c)) / atr, "lower_wick": (min(o, c) - l) / atr,
            "_atr": atr, "_close": c, "_dollar": self.dv42.pos_mean(),
        }
        if btc is not None:
            bm = btc.m
            if not bm.atr or bm.i + 1 < MIN_BARS:
                return None
            b42, b180 = bm.ret(WIN_MID), bm.ret(WIN_LONG)
            f["btc_ret42"] = b42
            f["btc_z_sma180"] = (bm.closes[-1] - bm.sma180.mean()) / bm.atr
            f["btc_atr_pct"] = bm.atr / bm.closes[-1]
            f["rs42"] = (f["ret42"] - b42) if f["ret42"] is not None and b42 is not None else None
            f["rs180"] = (f["ret180"] - b180) if f["ret180"] is not None and b180 is not None else None
        else:                                   # BTC insusi: forta relativa fata de el e zero
            f["btc_ret42"] = f["ret42"]
            f["btc_z_sma180"] = f["z_sma180"]
            f["btc_atr_pct"] = f["atr_pct"]
            f["rs42"] = f["rs180"] = 0.0
        if any(f[k] is None for k in FEATURES):
            return None
        return f


def aligned_features(candles, btc_candles, keep=None, is_btc=False):
    """Caracteristicile unei serii, bara cu bara, aliniate pe timp cu BTC. Intoarce lista de
    (index_bara, ts, caracteristici) pentru barele valide; `keep(ts)` alege ce bare se pastreaza
    (toate, implicit). BTC avanseaza in acelasi pas: la bara cu ts-ul T, BTC e la ultima lui bara cu
    ts <= T (fara date din viitor)."""
    out = []
    fs = FeatureStream()
    bs = None if is_btc else FeatureStream()
    j, nb = 0, len(btc_candles or [])
    for idx, bar in enumerate(candles):
        ts = bar[0]
        if bs is not None:
            while j < nb and btc_candles[j][0] <= ts:
                bs.push(btc_candles[j])
                j += 1
            if j == 0 or btc_candles[j - 1][0] != ts:
                fs.push(bar, None)               # bara fara BTC aliniat: starea avanseaza, fara exemplu
                continue
        f = fs.push(bar, bs)
        if f is not None and (keep is None or keep(ts)):
            out.append((idx, ts, f))
    return out


def last_features(candles, btc_candles, is_btc=False):
    """Caracteristicile la ultima bara inchisa (scanarea live). None daca nu exista destula istorie."""
    rows = aligned_features(candles, btc_candles, keep=None, is_btc=is_btc)
    if not rows or rows[-1][0] != len(candles) - 1:
        return None
    return rows[-1][2]


# ------------------------------------------------------------------ eticheta: tranzactia de pe bara
def bracket_outcome(candles, i, direction, atr):
    """R-ul tranzactiei invatate, semnalata la inchiderea barei `i`: intrare la deschiderea barei i+1,
    SL la SL_ATR*ATR, TP la TP_R*risc, iesire la inchiderea barei i+MAX_HOLD. Orice ambiguitate din
    bara e rezolvata IMPOTRIVA tranzactiei (SL si TP in aceeasi bara -> SL; o deschidere dincolo de
    SL se executa la deschidere). Comisioanele scad COST_FRAC*pret/risc din R. None daca nu exista
    inca toate barele (tranzactia nu s-a terminat)."""
    n = len(candles)
    if i + 1 >= n or not atr or atr <= 0:
        return None
    entry = candles[i + 1][1]
    risk = SL_ATR * atr
    if entry <= 0 or risk <= 0:
        return None
    long_ = direction == "LONG"
    sl = entry - risk if long_ else entry + risk
    tp = entry + TP_R * risk if long_ else entry - TP_R * risk
    cost = COST_FRAC * entry / risk
    last = i + MAX_HOLD
    for k in range(i + 1, min(last, n - 1) + 1):
        _t, o, h, lo, c = candles[k][:5]
        if k > i + 1 and ((long_ and o <= sl) or (not long_ and o >= sl)):
            return ((o - entry) if long_ else (entry - o)) / risk - cost
        hit_sl = lo <= sl if long_ else h >= sl
        hit_tp = h >= tp if long_ else lo <= tp
        if hit_sl:
            return -1.0 - cost
        if hit_tp:
            return TP_R - cost
        if k == last:
            return ((c - entry) if long_ else (entry - c)) / risk - cost
    return None


# ------------------------------------------------------------------ modelul publicat (JSON, fara numpy)
def predict(model, feats):
    """Valoarea asteptata (R) a modelului publicat pentru caracteristicile date. `model`:
    {"kind": "gbm", "base", "lr", "trees": [[[f, prag, stanga, dreapta] sau [valoare], ...], ...]}
    sau {"kind": "linear", "mean", "scale", "lo", "hi", "coef", "intercept"}."""
    if not model:
        return None
    x = [feats.get(k) for k in model["features"]]
    if any(v is None for v in x):
        return None
    if model["kind"] == "linear":
        s = model["intercept"]
        for v, mu, sc, lo, hi, w in zip(x, model["mean"], model["scale"], model["lo"], model["hi"], model["coef"]):
            s += w * ((min(max(v, lo), hi) - mu) / sc)
        return s
    s = model["base"]
    lr = model["lr"]
    for tree in model["trees"]:
        node = tree[0]
        while len(node) == 4:
            f, thr, left, right = node
            node = tree[left] if x[f] <= thr else tree[right]
        s += lr * node[0]
    return s


def score_symbol(models, feats):
    """EV estimat pentru LONG si SHORT si directia aleasa. `models` = {"LONG": model, "SHORT": model}."""
    ev = {d: predict((models or {}).get(d), feats) for d in ("LONG", "SHORT")}
    if ev["LONG"] is None and ev["SHORT"] is None:
        return None
    best = max((d for d in ev if ev[d] is not None), key=lambda d: ev[d])
    return {"ev_long": ev["LONG"], "ev_short": ev["SHORT"], "direction": best, "ev": ev[best]}


def trade_levels(feats, direction):
    """Nivelurile tranzactiei invatate de la pretul de inchidere (orientativ: intrarea reala e la
    deschiderea barei urmatoare)."""
    c, atr = feats["_close"], feats["_atr"]
    risk = SL_ATR * atr
    if direction == "LONG":
        return {"entry": c, "sl": c - risk, "tp": c + TP_R * risk}
    return {"entry": c, "sl": c + risk, "tp": c - TP_R * risk}


# ------------------------------------------------------------------ alegerea si promovarea
TOP_K = 3              # cate tranzactii pe bara, cel mult (cele cu EV-ul cel mai mare)
SELECT_Q = 0.95        # pragul de EV: cuantila 95% a predictiilor pe datele de antrenare


def select(rows, theta, k=TOP_K):
    """Alegerea agentului la fiecare bara: cel mult k candidati (simbol, directie) cu EV peste prag si
    peste zero, cei mai buni intai. `rows` = [(ts, simbol, directie, ev, ...)] -> subsetul ales."""
    by_ts = {}
    for r in rows:
        if r[3] is not None and r[3] > max(theta, 0.0):
            by_ts.setdefault(r[0], []).append(r)
    out = []
    for ts in sorted(by_ts):
        grp = sorted(by_ts[ts], key=lambda r: -r[3])
        seen = set()
        for r in grp:
            if r[1] in seen:
                continue
            seen.add(r[1])
            out.append(r)
            if len(seen) >= k:
                break
    return out


STATES = ("CERCETARE", "SHADOW", "ACTIV", "RETRAS")
PROMOTE_MIN_WF = 300           # alegeri in walk-forward, minim
PROMOTE_MIN_HOLD = 100         # alegeri in perioada rezervata, minim
LIVE_MIN = 60                  # alegeri incheiate in testul inainte (live), minim, pentru confirmare


def promotion(prev_state, wf, holdout, live):
    """Starea laboratorului din dovezi (fiecare: {"n", "mean", "lo", "hi"} pe R, IC95 pe blocuri de timp,
    sau None). Reguli, in ordine:
      - walk-forward: n >= PROMOTE_MIN_WF, IC95 peste zero, iar anii pozitivi >= 2/3 din ani;
      - perioada rezervata: n >= PROMOTE_MIN_HOLD si media peste zero;
      - live: cu cel putin LIVE_MIN alegeri incheiate, IC95 sub zero -> RETRAS, peste zero -> ACTIV.
    Fara walk-forward si perioada rezervata trecute -> CERCETARE (nimic nu se recomanda). Intoarce
    (stare, motiv)."""
    def _f(x):
        return "n/d" if x is None else f"{x:+.3f}"
    if not wf or wf.get("n", 0) < PROMOTE_MIN_WF:
        return "CERCETARE", f"prea putine alegeri in walk-forward ({(wf or {}).get('n', 0)} < {PROMOTE_MIN_WF})"
    years = wf.get("years") or {}
    pos = sum(1 for y in years.values() if (y.get("mean") or 0) > 0)
    if wf.get("lo") is None or wf["lo"] <= 0:
        return "CERCETARE", (f"walk-forward fara avantaj dovedit: {_f(wf.get('mean'))}R/alegere "
                             f"(IC95 {_f(wf.get('lo'))}..{_f(wf.get('hi'))}, n={wf['n']})")
    if years and pos * 3 < 2 * len(years):
        return "CERCETARE", f"walk-forward pozitiv doar in {pos} din {len(years)} ani"
    if not holdout or holdout.get("n", 0) < PROMOTE_MIN_HOLD or (holdout.get("mean") or 0) <= 0:
        return "CERCETARE", (f"perioada rezervata nu confirma: {_f((holdout or {}).get('mean'))}R/alegere "
                             f"(n={(holdout or {}).get('n', 0)})")
    if live and live.get("n", 0) >= LIVE_MIN and live.get("hi") is not None and live["hi"] < 0:
        return "RETRAS", f"live il infirma: {_f(live.get('mean'))}R/alegere (IC95 {_f(live.get('lo'))}..{_f(live['hi'])}, n={live['n']})"
    if live and live.get("n", 0) >= LIVE_MIN and live.get("lo") is not None and live["lo"] > 0:
        return "ACTIV", f"confirmat live: {_f(live.get('mean'))}R/alegere (IC95 {_f(live['lo'])}..{_f(live.get('hi'))}, n={live['n']})"
    if prev_state == "ACTIV" and live and live.get("n", 0) >= LIVE_MIN and (live.get("mean") or 0) > 0:
        return "ACTIV", f"ramane activ (histerezis): live {_f(live.get('mean'))}R/alegere, n={live['n']}"
    n_live = (live or {}).get("n", 0)
    return "SHADOW", (f"trece walk-forward si perioada rezervata; asteapta confirmarea live "
                      f"({n_live}/{LIVE_MIN} alegeri incheiate)")
