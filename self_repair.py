#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
self_repair.py
===============
Auto-repararea CODULUI, pornind de la erorile gasite de self_check.py.

BUCLA
-----
  self_check.py gaseste un invariant incalcat, cu exemple din date reale
    -> self_repair.py cere unui model de limbaj un patch MINIM pentru cauza
    -> POARTA DE VALIDARE (toate conditiile, automat):
         1. patch-ul NU atinge verificarile (check_*.py, self_check.py,
            self_repair.py, .github/) - altfel cea mai "usoara" reparatie ar fi
            slabirea testului care pica, un esec clasic al sistemelor autonome
         2. patch mic (limita de linii) - reparatii tintite, nu rescrieri
         3. check_py311.py: sintaxa + nume nedefinite
         4. check_integrity.py: invariantii de arhitectura
         5. ACCEPTANTA: invariantul care a picat TRECE pe ACELEASI date reale,
            recalculate cu codul reparat
         6. REGRESIE: nicio eroare noua fata de inainte
    -> Pull Request cu diagnosticul, patch-ul si rezultatele fiecarei porti.
       Il aprobi din aplicatia GitHub; nimic nu ajunge automat in productie.

INVATAREA
---------
data/repair_log.json retine fiecare incercare: patch-urile respinse, cu motivul
exact (ce poarta a picat), intra in contextul incercarii urmatoare; reparatiile
acceptate devin exemple pentru bug-uri din aceeasi clasa. Invariantul care a
prins bug-ul ramane in self_check.py - aceeasi clasa de eroare nu mai poate
reveni neobservata.

LIMITE, DECLARATE
-----------------
Repararea automata acopera doar clasele de erori cu test de acceptanta
determinist (invariantul re-rulat pe aceleasi date). Restul raman Issue pentru
revizuire umana. Un model de limbaj poate propune un patch gresit - de aceea
exista poarta si de aceea merge-ul ramane al tau.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

DATA = "data"
LOG = os.path.join(DATA, "repair_log.json")
MODEL = os.environ.get("REPAIR_MODEL", "claude-sonnet-5")
MAX_DIFF_LINES = 120
MAX_ATTEMPTS_PER_DAY = 2
PROTECTED = {"check_integrity.py", "check_py311.py", "self_check.py", "self_repair.py"}

# Ce erori se pot repara automat: fisierele implicate si testul de acceptanta
# (functia din self_check.py re-rulata pe datele recalculate).
REGISTRY = {
    "ew_targets": {"files": ["elliott.py"], "accept": "check_targets_vs_extreme"},
    "calibration": {"files": ["plan_tracker.py"], "accept": "check_calibration_coverage"},
}

SYSTEM = (
    "Esti modulul de auto-reparare al unui scanner crypto scris in Python. Primesti un "
    "diagnostic automat - un invariant incalcat, cu exemple din date reale - si codul sursa "
    "implicat. Produci un patch MINIM, in format unified diff (ca `git diff`, cu caile a/ si "
    "b/), care repara CAUZA, nu simptomul. Reguli stricte: nu modifica check_integrity.py, "
    "check_py311.py, self_check.py, self_repair.py si nimic din .github/; nu slabi si nu "
    "ocoli niciun invariant; pastreaza stilul existent si scrie in comentariu, in romana, "
    "cauza reparata. Raspunde DOAR cu un singur bloc ```diff.")


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _run(cmd, cwd, env=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600,
                       env={**os.environ, **(env or {})})
    return r.returncode, (r.stdout + r.stderr)[-2000:]


def ask_llm(system, user):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("lipseste ANTHROPIC_API_KEY (secret in GitHub)")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps({"model": MODEL, "max_tokens": 8000, "system": system,
                         "messages": [{"role": "user", "content": user}]}).encode(),
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.loads(r.read().decode())
    return "".join(b.get("text", "") for b in data.get("content") or [])


def extract_diff(text):
    m = re.search(r"```diff\n(.*?)```", text or "", re.S)
    return m.group(1) if m else None


def diff_files(diff):
    return sorted({m for m in re.findall(r"^\+\+\+ b/(\S+)", diff, re.M)} |
                  {m for m in re.findall(r"^--- a/(\S+)", diff, re.M)})


def build_prompt(check, repo, history):
    spec = REGISTRY[check["id"]]
    past = [h for h in history if h.get("check") == check["id"]][-4:]
    lessons = "\n".join(f"- incercare {h['when']}: {h['outcome']} - {h.get('reason', '')}"
                        for h in past) or "- nicio incercare anterioara"
    files = "\n\n".join(f"=== {f} ===\n{open(os.path.join(repo, f)).read()}" for f in spec["files"])
    return (f"DIAGNOSTIC ({check['id']}): {check['title']}\n{check['detail']}\n\n"
            f"EXEMPLE DIN DATE REALE:\n" + "\n".join(f"- {e}" for e in check.get("examples") or [])
            + f"\n\nINCERCARI ANTERIOARE PENTRU ACEASTA EROARE (invata din ele):\n{lessons}\n\n"
            f"Testul de acceptanta: {spec['accept']}() din self_check.py trebuie sa treaca pe "
            f"aceleasi date, recalculate cu codul reparat.\n\nCOD IMPLICAT:\n{files}")


# Recalculeaza, cu codul din `repo`, structurile Elliott si calibrarea pe datele
# reale copiate in `data_dir`, apoi ruleaza self_check acolo. Ruleaza intr-un
# proces separat, ca modulele reparate sa fie importate proaspat.
REPLAY = r'''
import json, os, sys
os.environ.setdefault("SCAN_TIMEFRAME", "4h")     # acelasi mediu ca scanarea
repo, data_dir = sys.argv[1], sys.argv[2]
sys.path.insert(0, repo); os.chdir(os.path.dirname(data_dir))
import elliott as ew, plan_tracker as pt, self_check as sc
d = json.load(open(os.path.join(data_dir, "latest_details.json")))
for sym, s in (d.get("symbols") or {}).items():
    c = s.get("candles") or []
    if len(c) < 30: continue
    H = [x[2] for x in c]; L = [x[3] for x in c]; C = [x[4] for x in c]
    r = ew.analyze(H, L, C, C[-1]); s["elliott"] = {**r, "offset": 0}
json.dump(d, open(os.path.join(data_dir, "latest_details.json"), "w"))
st = json.load(open(os.path.join(data_dir, "plans.json")))
st["calibration"] = pt.build_calibration(st)
json.dump(st, open(os.path.join(data_dir, "plans.json"), "w"))
sc.DATA = data_dir
out = sc.run()
print("RESULT=" + json.dumps({c["id"]: c["level"] for c in out["checks"]}))
'''


def replay_levels(repo, data_src):
    work = tempfile.mkdtemp()
    dd = os.path.join(work, "data")
    shutil.copytree(data_src, dd)
    script = os.path.join(work, "replay.py")
    open(script, "w").write(REPLAY)
    code, out = _run([sys.executable, script, repo, dd], cwd=work)
    m = re.search(r"RESULT=(\{.*\})", out)
    shutil.rmtree(work, ignore_errors=True)
    return json.loads(m.group(1)) if m else None


def gate(check, diff, repo, data_src, baseline):
    """Toate conditiile, in ordine. Returneaza (ok, rezultate pe porti)."""
    res = []
    touched = diff_files(diff)
    bad = [f for f in touched if f in PROTECTED or f.startswith(".github/")]
    res.append(("fisiere protejate neatinse", not bad, ", ".join(bad) or "ok"))
    if bad:
        return False, res
    # doar fisierele implicate de diagnostic: un patch care creeaza, muta sau
    # modifica alte fisiere iese din perimetrul reparatiei (prins la test: un
    # antet de diff gresit "muta" elliott.py intr-o cale noua)
    allowed = set(REGISTRY[check["id"]]["files"])
    extra = [f for f in touched if f not in allowed]
    res.append(("doar fisierele implicate", not extra, ", ".join(extra) or ", ".join(sorted(allowed))))
    if extra:
        return False, res
    n = sum(1 for l in diff.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---")))
    res.append((f"patch mic (<= {MAX_DIFF_LINES} linii)", n <= MAX_DIFF_LINES, f"{n} linii"))
    if n > MAX_DIFF_LINES:
        return False, res
    wt = tempfile.mkdtemp()
    shutil.copytree(repo, os.path.join(wt, "r"), ignore=shutil.ignore_patterns("data", "docs", "__pycache__"))
    r = os.path.join(wt, "r")
    pf = os.path.join(wt, "p.diff")
    open(pf, "w").write(diff)
    code, out = _run(["git", "apply", "--whitespace=nowarn", pf], cwd=r)
    res.append(("patch se aplica", code == 0, out.strip()[-200:] or "ok"))
    if code != 0:
        return False, res
    for name, cmd in (("sintaxa si nume nedefinite", ["check_py311.py"]),
                      ("invariantii de arhitectura", ["check_integrity.py"])):
        code, out = _run([sys.executable, "-B"] + cmd, cwd=r, env={"PYTHONDONTWRITEBYTECODE": "1"})
        res.append((name, code == 0, out.strip().splitlines()[-1][:200] if out.strip() else ""))
        if code != 0:
            return False, res
    after = replay_levels(r, data_src)
    ok_acc = bool(after) and after.get(check["id"]) != "ERROR"
    res.append((f"acceptanta: {check['id']} trece pe datele reale", ok_acc,
                (f"{(baseline or {}).get(check['id'])} -> {after.get(check['id'])}" if after
                 else "codul reparat crapa la recalcularea pe datele reale")))
    if not ok_acc:
        return False, res
    new_err = [k for k, v in after.items() if v == "ERROR" and (baseline or {}).get(k) != "ERROR"]
    res.append(("fara erori noi (regresie)", not new_err, ", ".join(new_err) or "ok"))
    shutil.rmtree(wt, ignore_errors=True)
    return (not new_err), res


def open_pr(check, diff, results):
    tok, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not tok or not repo:
        return None
    branch = f"auto-repair/{check['id']}-{time.strftime('%Y%m%d%H%M', time.gmtime())}"
    pf = tempfile.mktemp(suffix=".diff")
    open(pf, "w").write(diff)
    for cmd in (["git", "checkout", "-b", branch], ["git", "apply", pf],
                ["git", "commit", "-am", f"auto-repair: {check['title']}"],
                ["git", "push", "origin", branch]):
        code, out = _run(cmd, cwd=".")
        if code != 0:
            print(f"[!] {' '.join(cmd)}: {out}")
            _run(["git", "checkout", "-"], cwd=".")
            return None
    _run(["git", "checkout", "-"], cwd=".")
    body = (f"## {check['title']}\n\n{check['detail']}\n\n**Exemple din date reale:**\n"
            + "\n".join(f"- {e}" for e in check.get("examples") or [])
            + "\n\n**Poarta de validare (toate trecute):**\n"
            + "\n".join(f"- {'✅' if ok else '❌'} {n} - {d}" for n, ok, d in results)
            + f"\n\nModel: `{MODEL}`. _Deschis automat de self_repair.py. Revizuieste si fa merge "
              "din aplicatia GitHub; nimic nu ajunge in productie fara aprobarea ta._")
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/pulls",
        data=json.dumps({"title": f"[auto-repair] {check['title']}", "head": branch,
                         "base": os.environ.get("GITHUB_REF_NAME", "main"), "body": body}).encode(),
        headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode()).get("html_url")


def main(llm=ask_llm, pr=open_pr, repo=".", data_src=DATA):
    diag = _load(os.path.join(data_src, "self_check.json"), {})
    history = _load(LOG, [])
    today = time.strftime("%Y-%m-%d", time.gmtime())
    todo = [c for c in diag.get("checks") or [] if c.get("level") == "ERROR" and c["id"] in REGISTRY
            and sum(1 for h in history if h["check"] == c["id"] and h["when"][:10] == today)
            < MAX_ATTEMPTS_PER_DAY]
    if not todo:
        print("Auto-reparare: nicio eroare reparabila automat in acest moment.")
        return None
    check = todo[0]
    # BAZA DE COMPARATIE: codul NEMODIFICAT, recalculat in exact aceleasi conditii
    # ca varianta reparata. Comparatia cu self_check.json amesteca medii diferite
    # (prins la test: o "regresie" falsa de calibrare, din alt timeframe).
    baseline = replay_levels(repo, data_src) or {c["id"]: c["level"] for c in diag.get("checks") or []}
    entry = {"when": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "check": check["id"],
             "model": MODEL}
    try:
        diff = extract_diff(llm(SYSTEM, build_prompt(check, repo, history)))
    except Exception as e:
        entry.update(outcome="eroare_model", reason=str(e)[:200])
        diff = None
    if diff:
        ok, results = gate(check, diff, repo, data_src, baseline)
        failed = next((f"{n}: {d}" for n, good, d in results if not good), "")
        if ok:
            url = pr(check, diff, results)
            entry.update(outcome="pr_deschis" if url else "validat_fara_pr", pr=url,
                         reason="toate portile trecute")
        else:
            entry.update(outcome="respins", reason=failed)
        entry["gates"] = [[n, ok_, d] for n, ok_, d in results]
    elif "outcome" not in entry:
        entry.update(outcome="fara_patch", reason="modelul nu a returnat un diff")
    history.append(entry)
    os.makedirs(os.path.dirname(LOG) or ".", exist_ok=True)
    with open(LOG, "w") as f:
        json.dump(history[-200:], f, indent=1)
    print(f"Auto-reparare {check['id']}: {entry['outcome']} - {entry.get('reason', '')}"
          + (f" | PR: {entry.get('pr')}" if entry.get("pr") else ""))
    return entry


if __name__ == "__main__":
    try:
        main()
    except Exception as e:           # nu opreste niciodata workflow-ul
        print(f"[!] auto-repararea a esuat: {e}")
