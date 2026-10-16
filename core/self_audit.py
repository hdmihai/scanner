# -*- coding: utf-8 -*-
"""core.self_audit - AUTO-AUDITUL: agentul isi cauta singur imbunatatirile, la fiecare scanare
(NUCLEU, fara I/O - fisierele si sursele le citeste self_check.py si le paseaza aici).

DE CE EXISTA
------------
Auto-diagnosticul (self_check.py) verifica INVARIANTI de calcul (tinte Elliott, geometrie,
evidente). Problemele gasite ulterior de monitorizarea zilnica erau de alt fel - nu calcule
gresite, ci lucruri pe care agentul le AFIRMA sau le FOLOSESTE fara sa le mai verifice:
  - cifre masurate o data si scrise de mana in textul deciziilor ("+0.04R fata de +0.11R"),
    ramase acolo dupa ce masuratoarea s-a schimbat;
  - intervale de incredere calculate ca si cum planurile ar fi independente (de ~2 ori prea
    inguste), care declarau "distinct de zero" efecte care erau zgomot;
  - o arhiva care isi pierdea intrarile la o reconstructie (64 de planuri live scoase pe 10.10,
    disparute fara urma pe 11.10);
  - texte care descriau starea unei parghii altfel decat era (poarta "decide", desi era oprita);
  - parghii de decizie active fara dovada, sau dovezi care nu se mai masurau deloc (walk-forward-ul
    portii compara "toate" cu "toate" de cand poarta e oprita implicit).
Fiecare are un simptom MASURABIL. Modulul asta le masoara singur, la fiecare scanare.

CE FACE
-------
1. SONDE pe propriul cod (sursele .py ale agentului, citite la rulare):
     cod.cifre_fixe   - rezultate masurate scrise ca text fix in codul de productie;
     cod.ic_naiv      - eroare standard a planurilor independente in cod care decide sau raporteaza.
2. SONDE pe date, de la o rulare la alta (starea se pastreaza in self_check.json):
     date.plan_disparut    - un plan live disparut fara nicio urma (audit, reconstructie);
     date.arhiva_micsorata - o arhiva care trebuia doar sa creasca s-a micsorat.
3. PARGHIILE DE DECIZIE, fiecare cu dovada ei masurata acum, pe blocuri de timp:
     semnal, filtrul Elliott, filtrul agentului, poarta de calibrare, regulile din cercetare,
     carantina. Constatare cand starea parghiei nu se potriveste cu dovada (activa fara dovada,
     oprita desi dovedita, dovada lipsa sau masurata cu reguli vechi).
4. TEXT vs STARE: frazele din briefing si din motivele deciziilor care descriu o parghie altfel
   decat e (text.stare_contrazisa).
5. REGISTRUL de imbunatatiri: fiecare constatare are un id stabil, prima/ultima aparitie si
   o stare (deschisa -> rezolvata cand sonda trece; redeschisa daca revine). Registrul e citit
   de dashboard, de briefing si de monitorizarea zilnica (care scrie corecturile de cod).

CE NU FACE
----------
Nu rescrie cod si nu schimba singur parghiile (cele care au deja reguli automate - filtrul Elliott,
carantina, starea agentului, regulile din cercetare - raman la regulile lor, cu histerezis).
Spune precis CE trebuie schimbat, DE CE (cifra masurata) si UNDE (fisier, linie).
"""

import io
import math
import re
import tokenize

SEVERITIES = ("mare", "medie", "mica")
RESOLVED_KEEP_S = 30 * 86400        # cat timp raman vizibile constatarile rezolvate
ARCHIVE_CAP = 500                   # arhivele de planuri scoase sunt plafonate la 500 de intrari
ALLOW_MARK = "audit: ok"            # comentariu pe linie = exceptie asumata, cu motiv

# rezultate masurate in text: "+0.04R", "-0.155R/plan", "0.11 R", "in 2 din 3 rulari"
CLAIM_RE = re.compile(r"(?<![\w{.%])[+\-−]?\d+\.\d+\s?R(?:/plan)?(?![\w])"
                      r"|\b\d+ din \d+ (?:rulari|ferestre|perioade)\b")
# eroarea standard a planurilor independente: 1.96 * se, 196 * se, sqrt(var / n)
NAIVE_RE = re.compile(r"\b1\.96\s*\*\s*(?:se\b|sd\b|math\.sqrt|\(\s*sd)"
                      r"|\b196\s*\*\s*se\b"
                      r"|sqrt\(\s*var\w*\s*/\s*(?:n\b|len\()")


def finding(fid, kind, severity, title, problem, evidence, action, files=None):
    """O constatare: ce e gresit (problem), cifra masurata (evidence), ce trebuie schimbat
    (action) si unde (files: ["cale:linie", ...])."""
    return {"id": fid, "kind": kind, "severity": severity, "title": title, "problem": problem,
            "evidence": evidence, "action": action, "files": list(files or [])[:12]}


# ---------------------------------------------------------------------------
# 1. SONDE PE COD
# ---------------------------------------------------------------------------
def _string_parts(src):
    """(linie, text) pentru fiecare bucata de text literal din cod care NU e docstring sau
    comentariu. Pe Python >= 3.12 f-string-urile vin ca FSTRING_MIDDLE (doar partile literale),
    pe 3.11 ca un singur STRING - in ambele cazuri formatul {x:+.3f} nu arata ca o cifra."""
    out = []
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return out
    lines = src.splitlines()
    skip = (tokenize.NL, tokenize.COMMENT, tokenize.ENCODING)
    fmid = getattr(tokenize, "FSTRING_MIDDLE", None)
    prev = None
    sig = [t for t in toks if t.type not in skip]
    for i, t in enumerate(sig):
        if t.type == tokenize.STRING or (fmid is not None and t.type == fmid):
            nxt = sig[i + 1].type if i + 1 < len(sig) else tokenize.ENDMARKER
            is_doc = (t.type == tokenize.STRING and prev in (None, tokenize.NEWLINE, tokenize.INDENT,
                                                             tokenize.DEDENT)
                      and nxt in (tokenize.NEWLINE, tokenize.ENDMARKER))
            line = lines[t.start[0] - 1] if t.start[0] - 1 < len(lines) else ""
            if not is_doc and ALLOW_MARK not in line:
                out.append((t.start[0], t.string))
        prev = t.type
    return out


def probe_hardcoded_claims(sources):
    """Rezultate masurate scrise ca text fix in codul de productie. Un asemenea text ramane
    neschimbat cand masuratoarea se schimba si ajunge sa afirme ceva fals (C9-I1, 14.10.2026:
    "+0.04R/plan, fata de +0.11R" pe fiecare decizie ASTEAPTA_CORECTIA, desi masurat era
    -0.155R fata de -0.024R). O constatare pe fisier."""
    out = []
    for path, src in sorted(sources.items()):
        hits = []
        for ln, s in _string_parts(src):
            for m in CLAIM_RE.finditer(s):
                hits.append((ln, m.group(0).strip()))
        if hits:
            ev = "; ".join(f"linia {ln}: „{c}”" for ln, c in hits[:6])
            out.append(finding(
                f"cod.cifre_fixe:{path}", "cod", "medie",
                f"Cifre masurate scrise de mana in {path}",
                "Textul afiseaza un rezultat masurat o singura data; cand datele se schimba, textul ramane "
                "si afirma ceva fals.",
                f"{len(hits)} aparitii - {ev}",
                "Inlocuieste cifra cu masuratoarea curenta (din self_check.json, plans.json summary, "
                "walkforward.json) sau scoate afirmatia. O exceptie asumata se marcheaza pe linie cu "
                f"„# {ALLOW_MARK} <motiv>”.",
                [f"{path}:{ln}" for ln, _ in hits]))
    return out


def _code_lines(src):
    """(linie, cod fara comentariu), doar pentru liniile de cod (fara docstring-uri)."""
    out = []
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return out
    comment_col = {t.start[0]: t.start[1] for t in toks if t.type == tokenize.COMMENT}
    doc_lines = set()
    sig = [t for t in toks if t.type not in (tokenize.NL, tokenize.COMMENT)]
    prev = None
    for i, t in enumerate(sig):
        if t.type == tokenize.STRING:
            nxt = sig[i + 1].type if i + 1 < len(sig) else tokenize.ENDMARKER
            if prev in (None, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT) and nxt in (
                    tokenize.NEWLINE, tokenize.ENDMARKER):
                doc_lines.update(range(t.start[0], t.end[0] + 1))
        prev = t.type
    for n, line in enumerate(src.splitlines(), 1):
        if n in doc_lines or ALLOW_MARK in line:
            continue
        code = line[:comment_col[n]] if n in comment_col else line
        if code.strip():
            out.append((n, code))
    return out


def probe_naive_ci(sources):
    """Intervale de incredere ale unei medii de planuri calculate ca si cum planurile ar fi
    independente. Planurile din aceeasi saptamana se misca impreuna; intervalul iese de ~2 ori
    prea ingust (masurat pe backtest) si declara "distinct de zero" efecte care sunt zgomot -
    C10-I1 (intervalele de scor), filtrul Elliott (IC +0.004..+0.259 naiv, -0.034..+0.306 pe
    blocuri), walk-forward-ul portii. Metoda proiectului: core.plans.block_mean_ci /
    block_diff_ci (bootstrap) sau cluster_mean_se / cluster_diff_se (analitic)."""
    out = []
    for path, src in sorted(sources.items()):
        hits = [(n, c.strip()) for n, c in _code_lines(src) if NAIVE_RE.search(c)]
        if hits:
            out.append(finding(
                f"cod.ic_naiv:{path}", "statistica", "medie",
                f"Interval de incredere naiv in {path}",
                "Eroarea standard e calculata ca pentru planuri independente; planurile aceleiasi saptamani "
                "sunt corelate, deci intervalul e prea ingust si verdictul „semnificativ” vine prea usor.",
                "; ".join(f"linia {n}: {c[:80]}" for n, c in hits[:5]),
                "Foloseste core.plans.block_mean_ci / block_diff_ci (bootstrap pe blocuri de timp) sau "
                "cluster_mean_se / cluster_diff_se. O exceptie validata empiric se marcheaza pe linie cu "
                f"„# {ALLOW_MARK} <motiv>”.",
                [f"{path}:{n}" for n, _ in hits]))
    return out


# ---------------------------------------------------------------------------
# 2. SONDE PE DATE, DE LA O RULARE LA ALTA
# ---------------------------------------------------------------------------
def probe_data_trace(prev_state, plans, audit_log):
    """Planurile live nu au voie sa dispara fara urma: orice plan live vazut la rularea anterioara
    trebuie sa fie acum in plans.json sau intr-o arhiva cu motiv (plan_audit.json: `removed` -
    date false, `rebuild_removed_live` - reconstructie). Arhivele acestea doar cresc (plafon 500).
    Intoarce (constatari, starea noua)."""
    prev_state = prev_state or {}
    audit_log = audit_log or {}
    live_ids = sorted({p.get("id") for p in plans if p.get("source") != "backtest" and p.get("id") is not None})
    arch = {k: [r.get("id") for r in (audit_log.get(k) or []) if isinstance(r, dict)]
            for k in ("removed", "rebuild_removed_live")}
    out = []
    prev_ids = set(prev_state.get("live_ids") or [])
    traced = set(live_ids) | {i for v in arch.values() for i in v}
    gone = sorted(prev_ids - traced)
    if prev_ids and gone:
        out.append(finding(
            "date.plan_disparut", "date", "mare", "Planuri live disparute fara urma",
            "Planuri live vazute la rularea anterioara nu mai sunt nici in plans.json, nici in arhivele cu "
            "motiv din plan_audit.json - rezultatul unor tranzactii reale s-a pierdut.",
            f"{len(gone)} planuri: " + ", ".join(f"#{i}" for i in gone[:10]),
            "Gaseste pasul care le-a scos (compact_plans, data_reset, audit, backtest --merge) si fa-l sa "
            "le arhiveze cu motiv; recupereaza-le din istoricul git al data/plans.json.",
            ["data/plans.json", "data/plan_audit.json"]))
    prev_n = prev_state.get("archives") or {}
    for k, ids in arch.items():
        before = prev_n.get(k)
        if before is not None and len(ids) < before and before < ARCHIVE_CAP:
            out.append(finding(
                f"date.arhiva_micsorata:{k}", "date", "mare", f"Arhiva {k} s-a micsorat",
                f"plan_audit.json/{k} trebuie doar sa creasca (plafon {ARCHIVE_CAP}); o scriere a "
                "inlocuit-o in loc sa adauge la ea.",
                f"{before} intrari la rularea anterioara, {len(ids)} acum",
                "Scrierea trebuie sa acumuleze (unic pe id, ultimele 500) - ca data_reset.rebuild dupa C9-I2; "
                "intrarile pierdute se recupereaza din istoricul git al data/plan_audit.json.",
                ["data/plan_audit.json"]))
    state = {"live_ids": live_ids, "archives": {k: len(v) for k, v in arch.items()}}
    return out, state


# ---------------------------------------------------------------------------
# 3. PARGHIILE DE DECIZIE
# ---------------------------------------------------------------------------
def _f(v, nd=3):
    return "n/d" if v is None else f"{v:+.{nd}f}"


def ranking_gain_ci(pairs, frac=1 / 3, chunk=100, block_diff_ci=None):
    """Cat aduce filtrul agentului (treimea de sus fata de toate), cu IC95 pe BLOCURI de predictii
    consecutive (evaluarea e prequentiala, deci in ordine cronologica: un bloc de 100 = ~o perioada).
    (castig, lo, hi, n) sau None fara date."""
    rated = [pp for pp in pairs or [] if len(pp) >= 3]
    if len(rated) < 300 or block_diff_ci is None:
        return None
    k = max(1, int(len(rated) * frac))
    cut = sorted((pp[0] for pp in rated), reverse=True)[k - 1]
    rows_all = [(i, pp[2]) for i, pp in enumerate(rated)]
    rows_top = [(i, pp[2]) for i, pp in enumerate(rated) if pp[0] >= cut]
    gain = sum(r for _, r in rows_top) / len(rows_top) - sum(r for _, r in rows_all) / len(rows_all)
    lo, hi = block_diff_ci(rows_all, rows_top, block_s=chunk)
    return gain, lo, hi, len(rated)


def levers(ctx):
    """Tabelul parghiilor: pentru fiecare, starea (cum decide acum) si dovada masurata acum.
    `ctx` (construit de self_check.py):
      edge            - core.plans.edge_by_period pe backtest (recent: r/ci_low/ci_high/n; status)
      elliott         - {"on", "d", "lo", "hi", "n"} din self_check.elliott_stats
      agent           - agent_model.json; agent_gain = ranking_gain_ci(...)
      gate_on         - USE_DECISION_GATE; walkforward = data/walkforward.json; eval_rules = EVAL_RULES
      research        - research.json; quarantine - mitigatii
    Intoarce (randuri, constatari)."""
    rows, out = [], []

    # SEMNALUL: radacina. Fara avantaj in semnal, nicio parghie de decizie nu poate crea unul.
    rec = (ctx.get("edge") or {}).get("recent") or {}
    st = (ctx.get("edge") or {}).get("status")
    if rec:
        rows.append({"lever": "semnal", "name": "Semnalul (EMA + RSI + pullback)", "state": "activ",
                     "evidence": f"ultimele 365 zile {_f(rec.get('r'))}R/plan (IC95 {_f(rec.get('ci_low'))}.."
                                 f"{_f(rec.get('ci_high'))}, n={rec.get('n')})",
                     "verdict": {"pozitiv": "dovedit", "negativ": "daunator"}.get(st, "nedovedit")})
        if st in ("negativ", "neconcludent"):
            out.append(finding(
                "strategie.semnal_fara_avantaj", "strategie", "mare", "Semnalul nu are avantaj masurabil recent",
                "Parghiile de decizie (filtre, poarta, agent) doar aleg dintre semnale; daca semnalul nu are "
                "avantaj, ele nu pot crea unul. Cea mai mare imbunatatire posibila e un semnal nou.",
                rows[-1]["evidence"] + f" - {st}",
                "Cauta familii de semnal noi pe tot universul din depozitul de memorie (replay rapid), cu "
                "validare pe ani nefolositi la constructie si confirmare live, inainte de orice filtru nou.",
                ["core/scan.py", "backtest.py", "memory_sync.py"]))

    # FILTRUL ELLIOTT
    ew = ctx.get("elliott") or {}
    if ew.get("lo") is not None:
        verdict = "dovedit" if ew["lo"] > 0 else ("daunator" if ew["hi"] < 0 else "nedovedit")
        rows.append({"lever": "filtru_elliott", "name": "Filtrul de conflict Elliott",
                     "state": "activ" if ew.get("on") else "oprit",
                     "evidence": f"contra Elliott vs restul: diferenta {_f(ew.get('d'))}R (IC95 {_f(ew['lo'])}.."
                                 f"{_f(ew['hi'])}, {ew.get('n')} planuri)", "verdict": verdict})
        if ew.get("on") and verdict == "nedovedit":
            out.append(finding(
                "decizie.elliott_nedovedit", "decizie", "medie", "Filtrul Elliott e activ fara dovada",
                "Filtrul refuza semnalele contra structurii Elliott, dar efectul masurat nu e distinct de zero - "
                "ramane activ doar prin histerezis.",
                rows[-1]["evidence"],
                "Nimic automat de schimbat: explorarea (15% din semnalele refuzate) strange dovada. Daca dupa "
                "inca ~100 de planuri live contra Elliott IC95 tot include zero, filtrul se poate opri.",
                ["self_check.py", "core/plans.py"]))
        if ew.get("on") and verdict == "daunator":
            out.append(finding(
                "decizie.elliott_daunator", "decizie", "mare", "Filtrul Elliott refuza semnale mai bune",
                "Planurile contra Elliott rezulta distinct MAI BINE decat restul, iar filtrul le refuza.",
                rows[-1]["evidence"], "Filtrul se opreste automat la rularea urmatoare (self_check); "
                "verifica de ce nu s-a oprit.", ["self_check.py"]))

    # FILTRUL AGENTULUI
    ag = ctx.get("agent") or {}
    g = ctx.get("agent_gain")
    if ag:
        lv = ag.get("live") or {}
        ev = (f"treimea de sus fata de toate: {_f(g[0])}R (IC95 {_f(g[1])}..{_f(g[2])}, {g[3]} predictii in "
              f"afara esantionului)" if g else "prea putine predictii cu R masurat")
        ev += (f"; AUC live {lv.get('auc')} (IC95 {lv.get('ci_low')}-{lv.get('ci_high')}, n={lv.get('n')})"
               if lv.get("auc") is not None else "")
        verdict = ("dovedit" if g and g[1] is not None and g[1] > 0 else
                   "daunator" if g and g[2] is not None and g[2] < 0 else "nedovedit")
        active = ag.get("status") == "ACTIVE"
        rows.append({"lever": "filtru_agent", "name": "Filtrul agentului (treimea de jos)",
                     "state": "activ" if active else "shadow", "evidence": ev, "verdict": verdict})
        if active and verdict != "dovedit":
            out.append(finding(
                "decizie.agent_activ_nedovedit", "decizie", "mare", "Agentul filtreaza fara dovada de castig",
                "Agentul e ACTIVE si refuza treimea de jos, dar castigul masurat al treimii de sus nu e pozitiv "
                "distinct de zero.", ev, "Agentul trebuie sa revina in SHADOW: criteriul ranking_edge din "
                "core/agent.agent_is_active trebuie sa ceara IC95 > 0, nu doar media peste prag.",
                ["core/agent.py"]))
        elif not active and g and g[0] < 0:
            out.append(finding(
                "decizie.agent_fara_castig_r", "decizie", "medie",
                "Agentul nu aduce castig in R",
                "Agentul ordoneaza castig/pierdere peste hazard (AUC), dar semnalele puse in treimea de sus nu au "
                "R mai mare decat media" + (" - nici macar distinct de zero" if verdict == "nedovedit" else "")
                + ": ar putea refuza tocmai castigurile mari. In SHADOW e inofensiv, dar motivul afisat arata doar "
                "prima conditie neindeplinita (confirmarea live), nu si aceasta.", ev,
                "Antreneaza agentul pe R (sau pe R peste 1), nu doar pe castig/pierdere, si afiseaza toate "
                "conditiile de activare, nu doar prima.", ["core/agent.py", "ai_agent.py"]))

    # POARTA DE CALIBRARE
    wf = ctx.get("walkforward") or {}
    gate_on = bool(ctx.get("gate_on"))
    if not wf:
        rows.append({"lever": "poarta", "name": "Poarta de calibrare (EV pe interval de scor)",
                     "state": "activa" if gate_on else "oprita", "evidence": "walk-forward nemasurat",
                     "verdict": "nemasurat"})
        out.append(finding(
            "date.walkforward_lipsa", "date", "medie", "Poarta de calibrare n-are dovada masurata",
            "Starea portii (USE_DECISION_GATE) se sprijina pe un walk-forward vechi, care nu mai poate fi "
            "verificat: pana la 14.10.2026 walk-forward-ul rula cu poarta oprita si compara „toate” "
            "cu „toate” (castig 0 indiferent de date).",
            "data/walkforward.json lipseste",
            "Ruleaza Actions → Backtest cu mode walk_forward (timeframe 4h, merge true); rezultatul se "
            "salveaza in data/walkforward.json si e evaluat aici la fiecare scanare.",
            ["backtest.py", "data/walkforward.json"]))
    else:
        gl, gh = (wf.get("gain_ci") or [None, None])[:2]
        verdict = ("dovedit" if gl is not None and gl > 0 else
                   "daunator" if gh is not None and gh < 0 else "nedovedit")
        stale = wf.get("er") != ctx.get("eval_rules")
        rows.append({"lever": "poarta", "name": "Poarta de calibrare (EV pe interval de scor)",
                     "state": "activa" if gate_on else "oprita",
                     "evidence": f"walk-forward {wf.get('when')}: castig {_f(wf.get('gain'), 4)}R/plan (IC95 "
                                 f"{_f(gl, 4)}..{_f(gh, 4)}), {wf.get('n_issued')} emise din {wf.get('n_all')}"
                                 + (" - REGULI DE EVALUARE VECHI" if stale else ""),
                     "verdict": "nemasurat" if stale else verdict})
        if stale:
            out.append(finding(
                "date.walkforward_vechi", "date", "medie", "Walk-forward-ul portii e evaluat cu reguli vechi",
                "Dovada portii a fost masurata cu alte reguli de evaluare decat cele curente.",
                rows[-1]["evidence"], "Ruleaza din nou Actions → Backtest, mode walk_forward.",
                ["data/walkforward.json"]))
        elif verdict == "dovedit" and not gate_on:
            out.append(finding(
                "decizie.poarta_dovedita_oprita", "decizie", "mare", "Poarta de calibrare e dovedita, dar oprita",
                "Pe ferestre nevazute, poarta adauga R distinct de zero, dar USE_DECISION_GATE e false.",
                rows[-1]["evidence"], "Activeaza poarta (variabila USE_DECISION_GATE=true in scan.yml) si "
                "urmareste efectul live.", ["core/plans.py", ".github/workflows/scan.yml"]))
        elif verdict != "dovedit" and gate_on:
            out.append(finding(
                "decizie.poarta_activa_nedovedita", "decizie", "mare", "Poarta de calibrare filtreaza fara dovada",
                "Poarta refuza semnale, dar castigul ei pe ferestre nevazute nu e distinct de zero.",
                rows[-1]["evidence"], "Opreste poarta (USE_DECISION_GATE=false).", ["core/plans.py"]))

    # REGULILE DIN CERCETARE SI CARANTINA (au deja ciclul lor de viata automat)
    rs = (ctx.get("research") or {}).get("rules") or {}
    n_act = sum(1 for r in rs.values() if r.get("state") == "activa")
    n_sh = sum(1 for r in rs.values() if r.get("state") == "shadow")
    rep = (ctx.get("research") or {}).get("report") or {}
    rows.append({"lever": "reguli_cercetare", "name": "Reguli din cercetarea autonoma",
                 "state": f"{n_act} active, {n_sh} in shadow",
                 "evidence": (f"ultima investigatie: {rep.get('n_rules')} reguli testate pe {rep.get('n_plans')} planuri, "
                              f"{len(rep.get('accepted') or [])} acceptate (eroare robusta la gruparea pe saptamani)"
                              if rep.get("n_rules") else "nicio investigatie inca"),
                 "verdict": "dovedit" if n_act else "nimic de aplicat"})
    q = ctx.get("quarantine") or {}
    rows.append({"lever": "carantina", "name": "Carantina caracteristicilor",
                 "state": ", ".join(sorted(q)) or "goala",
                 "evidence": "; ".join(f"{k}: {v.get('reason')}" for k, v in sorted(q.items())) or
                             "nicio caracteristica suspecta",
                 "verdict": "activa" if q else "nimic de aplicat"})
    return rows, out


# ---------------------------------------------------------------------------
# 4. TEXT vs STARE
# ---------------------------------------------------------------------------
STATE_CLAIMS = [
    # (id, tipar in text, predicat pe stare -> textul e adevarat, ce afirma)
    ("poarta_decide", re.compile(r"se bazeaza pe aceste cifre", re.I), lambda s: bool(s.get("gate_on")),
     "deciziile se bazeaza pe intervalele de scor (poarta pornita)"),
    ("elliott_justificat", re.compile(r"filtrul e justificat de masuratoare", re.I),
     lambda s: (s.get("elliott") or {}).get("lo") is not None and s["elliott"]["lo"] > 0,
     "filtrul Elliott e justificat de masuratoare"),
    ("agent_filtreaza", re.compile(r"(?<!nu )\bagentul (?:filtreaza|decide)\b", re.I),
     lambda s: (s.get("agent") or {}).get("status") == "ACTIVE", "agentul filtreaza/decide"),
]


def probe_text_state(texts, state):
    """Frazele care descriu starea unei parghii trebuie sa fie adevarate ACUM. `texts` =
    [(sursa, text)] din briefing si motivele deciziilor."""
    bad = {}
    for src, txt in texts:
        for cid, rx, ok, what in STATE_CLAIMS:
            if rx.search(txt or "") and not ok(state):
                bad.setdefault(cid, (what, []))[1].append(src)
    out = []
    for cid, (what, srcs) in sorted(bad.items()):
        out.append(finding(
            f"text.stare_contrazisa:{cid}", "text", "medie", "Text care contrazice starea agentului",
            f"Un text afirma ca {what}, dar starea masurata acum spune altceva.",
            f"in {len(srcs)} locuri: " + ", ".join(sorted(set(srcs))[:5]),
            "Textul trebuie generat din starea parghiei (ca briefing._gate_line), nu scris fix.",
            sorted(set(srcs))[:5]))
    return out


# ---------------------------------------------------------------------------
# 5. REGISTRUL
# ---------------------------------------------------------------------------
def update_ledger(prev, findings, now, when):
    """Registrul de imbunatatiri: {id: intrare}. Intrare noua -> "deschisa"; prezenta din nou ->
    actualizata (ultima aparitie, numar de rulari); absenta -> "rezolvata"; rezolvata care revine
    -> "deschisa" din nou (redeschisa de N ori). Cele rezolvate dispar dupa 30 de zile.
    Intoarce (registrul, id-urile noi sau redeschise)."""
    led = {k: dict(v) for k, v in (prev or {}).items() if isinstance(v, dict)}
    fresh = []
    seen = set()
    for f in findings:
        seen.add(f["id"])
        e = led.get(f["id"])
        if e is None or e.get("status") == "rezolvata":
            fresh.append(f["id"])
            base = {"first_seen": when, "first_ts": now, "runs": 0, "reopened": 0, "issue": False}
            if e is not None:
                base.update({"first_seen": e.get("first_seen"), "first_ts": e.get("first_ts"),
                             "reopened": e.get("reopened", 0) + 1, "issue": e.get("issue", False)})
            e = base
        e.update(f)
        e.update({"status": "deschisa", "last_seen": when, "last_ts": now, "runs": e.get("runs", 0) + 1})
        e.pop("resolved", None)
        led[f["id"]] = e
    for k, e in list(led.items()):
        if k in seen:
            continue
        if e.get("status") == "deschisa":
            e.update({"status": "rezolvata", "resolved": when, "resolved_ts": now})
        if e.get("status") == "rezolvata" and now - (e.get("resolved_ts") or now) > RESOLVED_KEEP_S:
            del led[k]
    return led, fresh


def ordered(ledger):
    """Lista pentru afisare: deschise intai (mare -> mica, apoi cele mai vechi), apoi rezolvate."""
    rank = {s: i for i, s in enumerate(SEVERITIES)}
    return sorted(ledger.values(), key=lambda e: (e.get("status") != "deschisa", rank.get(e.get("severity"), 9),
                                                  e.get("first_ts") or 0, e.get("id")))


def summary_line(ledger):
    """O fraza pentru briefing: cate imbunatatiri deschise si cea mai importanta."""
    op = [e for e in ordered(ledger) if e.get("status") == "deschisa"]
    if not op:
        return "Auto-auditul nu are nicio imbunatatire deschisa."
    by = {s: sum(1 for e in op if e.get("severity") == s) for s in SEVERITIES}
    return (f"Auto-auditul are {len(op)} imbunatatiri deschise ({by['mare']} mari, {by['medie']} medii, "
            f"{by['mica']} mici); cea mai importanta: {op[0]['title']}.")
