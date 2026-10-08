# -*- coding: utf-8 -*-
"""tests.research_check - verifica cercetarea autonoma pe date controlate.

Uz (din radacina repo-ului): python -m tests.research_check

  1. PUTERE: un token care pierde constant (injectat in date) e gasit si acceptat;
  2. CONTROL: pe date amestecate aleator (fara nicio structura reala) nu se accepta
     nicio regula - garda impotriva regulilor false;
  3. CICLUL DE VIATA: regula acceptata -> shadow -> activa dupa confirmarea live ->
     filtreaza in decizie (cu explorare) -> retrasa cand live o infirma.
Foloseste planurile reale din data/plans.json ca baza; nu scrie nimic in repo.
"""

import json
import os
import random
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SCAN_TIMEFRAME", "4h")


def _plans():
    with open(os.path.join(ROOT, "data", "plans.json")) as f:
        return json.load(f)["plans"]


def main():
    from core import research as R
    fails = []
    base = [p for p in _plans() if p.get("source") == "backtest" and p.get("realized_r") is not None
            and p.get("state") != "NO_ENTRY"]
    # 1. PUTERE: un token cu pierdere constanta pe toata perioada
    rnd = random.Random(7)
    inj = []
    for p in base:
        q = dict(p)
        if str(p.get("symbol", "")).startswith("ADA/"):
            q["realized_r"] = -1.03 if rnd.random() < 0.85 else 1.6      # ~ -0.64R/plan, constant
        inj.append(q)
    rep = R.investigate(inj)
    got = [a["id"] for a in rep["accepted"]]
    ok = any(a["rule"].get("kind") == "symbol" and a["rule"].get("symbol") == "ADA" for a in rep["accepted"])
    print(f"  {'OK ' if ok else 'ERR'} putere: tokenul care pierde e gasit (acceptate: {got[:4]})")
    if not ok:
        fails.append("putere")
    # 2. CONTROL: R-urile amestecate intre planuri -> nicio structura reala
    for seed in (1, 2, 3):
        rs = [p["realized_r"] for p in base]
        random.Random(seed).shuffle(rs)
        sh = [{**p, "realized_r": r} for p, r in zip(base, rs)]
        acc = R.investigate(sh)["accepted"]
        print(f"  {'OK ' if not acc else 'ERR'} control (amestec {seed}): {len(acc)} reguli acceptate pe date fara structura")
        if acc:
            fails.append(f"control {seed}")
    # 3. CICLUL DE VIATA, intr-un dosar temporar
    work = tempfile.mkdtemp(prefix="cercetare-")
    cwd = os.getcwd()
    try:
        os.makedirs(os.path.join(work, "data"))
        os.chdir(work)
        import self_check
        import plan_tracker
        last = max(R._ts(p) for p in inj)
        store = {"plans": list(inj)}
        active, chk = self_check.research_cycle(store, now=last + 3600)
        st = json.load(open("data/research.json"))
        sh = [r for r in st["rules"].values() if r["state"] == "shadow"]
        ok = not active and any(r["rule"].get("symbol") == "ADA" for r in sh)
        print(f"  {'OK ' if ok else 'ERR'} acceptata -> shadow, fara filtrare ({len(sh)} in shadow, {len(active)} active)")
        if not ok:
            fails.append("shadow")
        ada = next(r for r in sh if r["rule"].get("symbol") == "ADA" and not r["rule"].get("direction"))

        def live(sym, r, i):
            return {"symbol": sym, "direction": "LONG", "realized_r": r, "state": "SL_HIT" if r < 0 else "TP2_HIT",
                    "created_ts": last + 7200 + i * 600, "components": {}, "score_at_entry": 60}
        lv = [live("ADA/USDT", -1.03, i) for i in range(25)] + [live("BTC/USDT", 0.4 if i % 2 else -0.2, i) for i in range(40)]
        store["plans"] = inj + lv
        active, _ = self_check.research_cycle(store, now=last + 4 * 86400)
        ok = any(a["id"] == ada["id"] for a in active)
        print(f"  {'OK ' if ok else 'ERR'} confirmare live -> activa ({len(active)} active)")
        if not ok:
            fails.append("activare")
        with open("data/self_check.json", "w") as f:
            json.dump({"mitigations": {"research_rules": active, "elliott_filter": False}}, f)
        sig = {"symbol": "ADA/USDT", "direction": "LONG", "risk_adjusted": 60, "components": {}, "price": 1.0}
        modes = {plan_tracker.decide({}, {**sig, "price": 1 + i / 1000})["mode"] for i in range(60)}
        other = plan_tracker.decide({}, {**sig, "symbol": "BTC/USDT"})["mode"]
        ok = modes == {"REGULA_CERCETARE", "EXPLORARE_REGULA"} and other not in modes
        print(f"  {'OK ' if ok else 'ERR'} decizia filtreaza tokenul, cu explorare ({sorted(modes)}); alt token: {other}")
        if not ok:
            fails.append("decizie")
        # HISTEREZIS: o investigatie care nu mai accepta regula (date fara tokenul care pierde)
        # nu o retrage imediat - doar dupa RETIRE_AFTER_DAYS zile fara acceptare
        clean = [p for p in inj if not str(p.get("symbol", "")).startswith("ADA/")] + lv
        self_check.research_cycle({"plans": clean}, now=last + 4 * 86400 + 3600)
        st = json.load(open("data/research.json"))
        kept = st["rules"][ada["id"]]["state"] == "activa"
        self_check.research_cycle({"plans": clean}, now=last + 12 * 86400)
        st = json.load(open("data/research.json"))
        gone = st["rules"][ada["id"]]["state"] == "retrasa"
        ok = kept and gone
        print(f"  {'OK ' if ok else 'ERR'} histerezis: pastrata la o respingere izolata, retrasa dupa "
              f"{self_check.RETIRE_AFTER_DAYS} zile fara acceptare ({st['rules'][ada['id']].get('retired_reason')})")
        if not ok:
            fails.append("histerezis")
        store["plans"] = inj + lv
        active, _ = self_check.research_cycle(store, now=last + 12 * 86400 + 3600)
        st = json.load(open("data/research.json"))
        ok = st["rules"][ada["id"]]["state"] == "shadow"
        print(f"  {'OK ' if ok else 'ERR'} reacceptata dupa retragere -> din nou shadow (confirmare live de la zero)")
        if not ok:
            fails.append("reacceptare")
        active, _ = self_check.research_cycle(store, now=last + 16 * 86400)
        lv2 = [live("ADA/USDT", 1.6, 2000 + i) for i in range(40)]
        lv2 += [live("BTC/USDT", 0.4 if i % 2 else -0.2, 2100 + i) for i in range(40)]
        store["plans"] = inj + lv + lv2
        active, _ = self_check.research_cycle(store, now=last + 30 * 86400)
        st = json.load(open("data/research.json"))
        ok = st["rules"][ada["id"]]["state"] == "retrasa" and not any(a["id"] == ada["id"] for a in active)
        print(f"  {'OK ' if ok else 'ERR'} infirmata live -> retrasa ({st['rules'][ada['id']].get('retired_reason')})")
        if not ok:
            fails.append("retragere")
    finally:
        os.chdir(cwd)
        shutil.rmtree(work, ignore_errors=True)
    print(f"\nCERCETARE: {'TRECUT' if not fails else 'RESPINS: ' + ', '.join(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
