# -*- coding: utf-8 -*-
"""tests.self_audit_check - verifica auto-auditul (core/self_audit.py) pe cazuri construite si pe codul real.

Uz (din radacina repo-ului): python -m tests.self_audit_check

  1. CIFRE FIXE: un rezultat masurat scris in textul codului e gasit; docstring-urile,
     comentariile, f-string-urile cu format ({x:+.3f}R) si liniile marcate "# audit: ok" nu;
  2. IC NAIV: 1.96 * sqrt(var / n) e gasit; intervalul Wilson, comentariile si exceptiile marcate nu;
  3. CODUL REAL E CURAT: ambele sonde nu gasesc nimic in sursele de productie (garda de regresie -
     o actualizare care reintroduce o cifra fixa sau un IC naiv e semnalata de agent la prima scanare);
  4. URMA PLANURILOR LIVE: un plan disparut fara arhiva e gasit, unul arhivat cu motiv nu; o arhiva
     micsorata e gasita, una la plafon nu;
  5. PARGHII: poarta dovedita dar oprita, poarta activa fara dovada, walk-forward vechi sau lipsa,
     agent ACTIVE fara castig, filtrul Elliott activ fara dovada - fiecare da constatarea lui;
     castigul agentului e gasit cand exista si nu cand R-ul e amestecat;
  6. TEXT vs STARE: "se bazeaza pe aceste cifre" cu poarta oprita e gasit; "nu filtreaza" in SHADOW nu;
  7. REGISTRUL: deschisa -> actualizata -> rezolvata -> redeschisa; rezolvatele vechi dispar;
  8. WALK-FORWARD: masoara poarta PORNITA (emite mai putin decat toate) si reface starea portii.
Nu scrie nimic in repo.
"""

import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SCAN_TIMEFRAME", "4h")

from core import plans as P          # noqa: E402
from core import self_audit as SA    # noqa: E402

FAILS = []


def ok(cond, label):
    print(f"  {'OK ' if cond else 'ERR'} {label}")
    if not cond:
        FAILS.append(label)


def code_probes():
    bad = {"m.py": 'def f(x):\n    return "contra Elliott n-au avut avantaj (+0.04R/plan, fata de +0.11R)"\n'}
    clean = {"m.py": ('"""Docstring: masurat +0.04R pe 10 oct."""\n'
                      '# comentariu: +0.11R/plan\n'
                      'def f(x, y):\n'
                      '    """+0.25R in docstring de functie."""\n'
                      '    s = f"medie {x:+.3f}R, total {y:.2f}R/plan"\n'
                      '    t = "reper +0.5R"  # audit: ok - prag de constructie, nu masuratoare\n'
                      '    return s + t\n')}
    fb = SA.probe_hardcoded_claims(bad)
    ok(len(fb) == 1 and "+0.04R" in fb[0]["evidence"] and "m.py:2" in fb[0]["files"],
       "cifra fixa in textul unei decizii e gasita (fisier si linie)")
    ok(not SA.probe_hardcoded_claims(clean), "docstring, comentariu, f-string cu format si exceptia marcata nu sunt semnalate")
    ok(len(SA.probe_hardcoded_claims({"g.py": 'r = "pe walk-forward a inrautatit rezultatul in 2 din 3 rulari"\n'})) == 1,
       "„2 din 3 rulari” scris de mana e gasit")

    naive = {"n.py": "import math\ndef ci(v, m, var, n):\n    return 1.96 * math.sqrt(var / n)\n"}
    fine = {"w.py": ("import math\n"
                     "def wilson(k, n, z=1.96):\n"
                     "    p = k / n\n"
                     "    return z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))\n"
                     "# 1.96 * math.sqrt(var / n) in comentariu\n"
                     "def nb(points, se):\n"
                     "    return points - 196 * se  # audit: ok - validat empiric\n")}
    fn = SA.probe_naive_ci(naive)
    ok(len(fn) == 1 and "n.py:3" in fn[0]["files"], "IC naiv (1.96 * sqrt(var / n)) e gasit")
    ok(not SA.probe_naive_ci(fine), "Wilson, comentariul si exceptia marcata nu sunt semnalate")


def real_code_clean():
    import self_check
    src = self_check.own_sources(ROOT)
    claims, naive = SA.probe_hardcoded_claims(src), SA.probe_naive_ci(src)
    ok(len(src) > 50, f"sursele de productie citite ({len(src)} fisiere .py, fara teste si unelte)")
    ok(not claims, "codul real nu are cifre masurate scrise de mana"
       + ("" if not claims else ": " + "; ".join(f["files"][0] for f in claims)))
    ok(not naive, "codul real nu are intervale de incredere naive"
       + ("" if not naive else ": " + "; ".join(f["files"][0] for f in naive)))


def data_trace():
    plans = [{"id": 1, "source": "live"}, {"id": 2, "source": "live"}, {"id": 9, "source": "backtest"}]
    f, st = SA.probe_data_trace({"live_ids": [1, 2, 3]}, plans, {})
    ok(any(x["id"] == "date.plan_disparut" and "#3" in x["evidence"] for x in f), "plan live disparut fara urma e gasit")
    f, _ = SA.probe_data_trace({"live_ids": [1, 2, 3]}, plans, {"removed": [{"id": 3}]})
    ok(not f, "plan live scos de audit cu motiv nu e semnalat")
    ok(st["live_ids"] == [1, 2] and st["archives"] == {"removed": 0, "rebuild_removed_live": 0},
       "starea retine planurile live si marimea arhivelor")
    f, _ = SA.probe_data_trace({"live_ids": [], "archives": {"rebuild_removed_live": 64}}, plans,
                               {"rebuild_removed_live": []})
    ok(any(x["id"] == "date.arhiva_micsorata:rebuild_removed_live" for x in f), "arhiva rebuild_removed_live 64 -> 0 e gasita")
    full = [{"id": i} for i in range(500)]
    f, _ = SA.probe_data_trace({"live_ids": [], "archives": {"removed": 500}}, plans, {"removed": full})
    ok(not f, "arhiva la plafon (500 -> 500) nu e semnalata")


def lever_ids(ctx):
    _, f = SA.levers(ctx)
    return {x["id"] for x in f}


def levers():
    er = P.EVAL_RULES
    wf_good = {"when": "t", "er": er, "gain": 0.03, "gain_ci": [0.01, 0.05], "n_issued": 100, "n_all": 200}
    wf_null = dict(wf_good, gain=0.01, gain_ci=[-0.01, 0.03])
    ok("decizie.poarta_dovedita_oprita" in lever_ids({"gate_on": False, "walkforward": wf_good, "eval_rules": er}),
       "poarta dovedita pe walk-forward, dar oprita -> constatare mare")
    ok("decizie.poarta_activa_nedovedita" in lever_ids({"gate_on": True, "walkforward": wf_null, "eval_rules": er}),
       "poarta activa fara dovada -> constatare mare")
    ok(not ({"decizie.poarta_dovedita_oprita", "decizie.poarta_activa_nedovedita"}
            & lever_ids({"gate_on": False, "walkforward": wf_null, "eval_rules": er})),
       "poarta oprita si nedovedita -> nimic de schimbat")
    ok("date.walkforward_vechi" in lever_ids({"gate_on": False, "walkforward": dict(wf_good, er=er - 1), "eval_rules": er}),
       "walk-forward cu reguli de evaluare vechi -> de refacut")
    ok("date.walkforward_lipsa" in lever_ids({"gate_on": False, "walkforward": None, "eval_rules": er}),
       "walk-forward lipsa -> de rulat")
    ok("decizie.elliott_nedovedit" in lever_ids({"elliott": {"on": True, "d": 0.1, "lo": -0.03, "hi": 0.3, "n": 3000}}),
       "filtrul Elliott activ cu IC care include zero -> nedovedit")
    ok("decizie.elliott_nedovedit" not in lever_ids({"elliott": {"on": True, "d": 0.1, "lo": 0.02, "hi": 0.3, "n": 3000}}),
       "filtrul Elliott dovedit -> nimic")
    ok("decizie.agent_activ_nedovedit" in lever_ids({"agent": {"status": "ACTIVE"}, "agent_gain": (0.02, -0.03, 0.07, 3000)}),
       "agent ACTIVE fara castig distinct -> constatare mare")
    ok("strategie.semnal_fara_avantaj" in lever_ids({"edge": {"status": "neconcludent",
                                                              "recent": {"r": -0.05, "ci_low": -0.16, "ci_high": 0.05, "n": 3700}}}),
       "semnal fara avantaj recent -> constatare mare")

    rnd = random.Random(3)
    good = []
    for _ in range(3000):
        p = rnd.random()
        good.append([p, 1.0, (1.5 if rnd.random() < p else -1.0)])
    g = SA.ranking_gain_ci(good, block_diff_ci=P.block_diff_ci)
    ok(g is not None and g[1] > 0, f"castigul real al agentului e gasit ({g[0]:+.3f}R, IC {g[1]:+.3f}..{g[2]:+.3f})")
    shuffled = [[pp[0], pp[1], r] for pp, r in zip(good, rnd.sample([pp[2] for pp in good], len(good)))]
    g = SA.ranking_gain_ci(shuffled, block_diff_ci=P.block_diff_ci)
    ok(g is not None and g[1] <= 0 <= g[2], f"cu R amestecat, IC-ul castigului include zero ({g[1]:+.3f}..{g[2]:+.3f})")


def text_state():
    off = {"gate_on": False, "agent": {"status": "SHADOW"}, "elliott": {"lo": -0.03}}
    f = SA.probe_text_state([("briefing.json", "Deciziile se bazeaza pe aceste cifre masurate.")], off)
    ok(any(x["id"] == "text.stare_contrazisa:poarta_decide" for x in f), "„se bazeaza pe aceste cifre” cu poarta oprita e gasit")
    ok(not SA.probe_text_state([("b", "Agentul e in SHADOW - invata, dar nu filtreaza.")], off),
       "„nu filtreaza” in SHADOW nu e semnalat")
    ok(SA.probe_text_state([("d", "agentul filtreaza semnalele slabe")], off), "„agentul filtreaza” in SHADOW e gasit")
    ok(SA.probe_text_state([("d", "Filtrul e justificat de masuratoare (auto-diagnostic)")], off),
       "„filtrul e justificat” cu IC care include zero e gasit")


def ledger():
    f1 = SA.finding("a", "cod", "medie", "A", "p", "e", "x")
    f2 = SA.finding("b", "date", "mare", "B", "p", "e", "x")
    led, fresh = SA.update_ledger({}, [f1, f2], 1000.0, "t1")
    ok(set(fresh) == {"a", "b"} and all(e["status"] == "deschisa" for e in led.values()), "constatari noi -> deschise")
    led, fresh = SA.update_ledger(led, [f1, f2], 2000.0, "t2")
    ok(not fresh and led["a"]["runs"] == 2 and led["a"]["first_seen"] == "t1", "a doua aparitie -> actualizata, nu noua")
    led, fresh = SA.update_ledger(led, [f2], 3000.0, "t3")
    ok(led["a"]["status"] == "rezolvata" and led["a"]["resolved"] == "t3", "absenta -> rezolvata")
    led, fresh = SA.update_ledger(led, [f1, f2], 4000.0, "t4")
    ok(fresh == ["a"] and led["a"]["status"] == "deschisa" and led["a"]["reopened"] == 1
       and led["a"]["first_seen"] == "t1", "revenita -> redeschisa (cu prima aparitie pastrata)")
    led, _ = SA.update_ledger(led, [f2], 5000.0, "t5")
    led, _ = SA.update_ledger(led, [f2], 5000.0 + SA.RESOLVED_KEEP_S + 1, "t6")
    ok("a" not in led and "b" in led, "rezolvatele mai vechi de 30 de zile dispar")
    ok(SA.ordered(led)[0]["id"] == "b" and "1 imbunatatiri deschise" in SA.summary_line(led), "ordinea si rezumatul")


def walk_forward():
    import json
    try:
        import backtest
    except ImportError as e:
        ok(False, f"backtest.py nu se poate importa ({e})")
        return
    with open(os.path.join(ROOT, "data", "plans.json")) as f:
        bt = [p for p in json.load(f).get("plans") or [] if p.get("source") == "backtest"]
    if len(bt) < 3000:
        print("  --  walk-forward: prea putine planuri de backtest in data/ - sarit")
        return
    import contextlib
    import io
    before = P.USE_DECISION_GATE, P.MITIGATIONS_SOURCE
    with contextlib.redirect_stdout(io.StringIO()):
        r = backtest.walk_forward(bt, n_windows=4)
    ok(r is not None and r["n"] < r["n_all"], f"walk-forward masoara poarta pornita ({r and r['n']} emise din "
                                              f"{r and r['n_all']}, castig {r and r['gain']:+.4f}R)")
    ok((P.USE_DECISION_GATE, P.MITIGATIONS_SOURCE) == before, "starea portii e refacuta dupa walk-forward")


def audit_cycle_shapes():
    """audit_cycle pe un director de date construit: data/decisions.json are "decisions" ca dict
    {simbol: decizie} (formatul real) - pe 16.10.2026 auditul cadea pe el, fiindca il trata ca lista."""
    import json
    import tempfile
    import self_check
    claim = "Deciziile de a deschide sau refuza planuri se bazeaza pe aceste cifre masurate."
    for shape, dec in (("dict", {"ADA/USDT": {"action": "SKIP", "mode": "X", "reason": claim}}),
                       ("lista", [{"symbol": "ADA/USDT", "action": "SKIP", "reason": claim}])):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "decisions.json"), "w") as f:
                json.dump({"scan_time": "t", "decisions": dec}, f)
            old = self_check.DATA
            self_check.DATA = d
            try:
                out, _fresh = self_check.audit_cycle({"plans": []}, {}, True, {}, {}, now=1.8e9, sources={})
                err = None
            except Exception as e:
                out, err = None, e
            finally:
                self_check.DATA = old
        ids = set((out or {}).get("ledger") or {})
        ok(err is None and any(i.startswith("text.stare_contrazisa:poarta_decide") for i in ids)
           and P.USE_DECISION_GATE is False,
           f"decizii ca {shape}: auditul ruleaza si gaseste textul care contrazice poarta "
           f"({'eroare ' + type(err).__name__ if err else 'ok'})")


def main():
    print("AUTO-AUDITUL")
    code_probes()
    real_code_clean()
    data_trace()
    levers()
    text_state()
    ledger()
    walk_forward()
    audit_cycle_shapes()
    print("\nAUTO-AUDIT: " + ("RESPINS - " + "; ".join(FAILS) if FAILS else "TRECUT"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
