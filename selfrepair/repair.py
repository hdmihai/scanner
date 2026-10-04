"""selfrepair.repair - propune corecturi de cod pentru erorile de logica din data/self_check.json.

Rulare: python -m selfrepair.repair [--dry-run]
Doar erorile din REGISTRY sunt reparabile, si doar in fisierele asociate lor. Garzile
(check_*.py, self_check.py), workflow-urile si datele nu pot fi modificate niciodata.
"""
import ast
import difflib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from datetime import datetime, timezone

from selfrepair.llm import complete

# eroare (id din self_check.py) -> fisierele in care poate fi corectata
REGISTRY = {
    "ew_targets": ["elliott.py"],
    "plan_forecast": ["plan_tracker.py", "elliott.py"],
    "plan_geometry": ["plan_tracker.py"],
    "evidence_panel": ["evidence.py"],
    "calibration": ["plan_tracker.py"],
}
MAX_CHANGED_LINES = 160
MAX_SOURCE_CHARS = 60000


def _summary(msg):
    print(msg)
    with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a") as f:
        f.write(msg + "\n")


def check_source(check_id):
    """Codul verificarii din self_check.py care a gasit eroarea (contextul exact)."""
    s = open("self_check.py").read()
    for n in ast.parse(s).body:
        if isinstance(n, ast.FunctionDef) and f'"{check_id}"' in (ast.get_source_segment(s, n) or ""):
            return ast.get_source_segment(s, n)
    return ""


def build_prompt(check, files):
    src = ""
    for f in files:
        src += f"\n### FISIER {f}\n```python\n{open(f).read()[:MAX_SOURCE_CHARS // len(files)]}\n```\n"
    return textwrap.dedent(f"""
    Esti inginer Python. Verificarea automata de mai jos a gasit o EROARE DE LOGICA intr-un
    scanner crypto. Corecteaza CAUZA in codul aplicatiei (nu verificarea si nu datele).

    EROAREA: {check.get('title')} - {check.get('detail')}
    EXEMPLE REALE: {json.dumps(check.get('examples') or [], ensure_ascii=False)[:3000]}

    VERIFICAREA CARE A GASIT-O (din self_check.py, NU o modifica):
    ```python
    {check_source(check['id'])}
    ```
    {src}
    Raspunde DOAR cu JSON: {{"explanation": "cauza si corectura, in romana",
    "changes": [{{"file": "<unul din: {', '.join(files)}>", "function": "<nume functie de nivel superior>",
    "code": "<functia COMPLETA corectata, incepand cu def>"}}]}}
    Schimbare minima, aceeasi semnatura, fara importuri noi. Daca nu gasesti cauza sigur,
    raspunde {{"explanation": "...", "changes": []}}.
    """).strip()


def apply_changes(root, changes, allowed):
    """Inlocuieste functiile de nivel superior in copia `root`. Intoarce (fisiere, linii schimbate)."""
    touched, changed = {}, 0
    for ch in changes:
        f, fn, code = ch.get("file"), ch.get("function"), textwrap.dedent(ch.get("code") or "").strip() + "\n"
        if f not in allowed:
            raise ValueError(f"fisier nepermis pentru aceasta eroare: {f}")
        new = ast.parse(code).body
        if len(new) != 1 or not isinstance(new[0], ast.FunctionDef) or new[0].name != fn:
            raise ValueError(f"codul pentru {fn} nu e o singura functie cu acest nume")
        p = os.path.join(root, f)
        s = open(p).read()
        node = next((n for n in ast.parse(s).body if isinstance(n, ast.FunctionDef) and n.name == fn), None)
        if node is None:
            raise ValueError(f"{f} nu are functia de nivel superior {fn}")
        lines = s.splitlines(True)
        start = (node.decorator_list[0].lineno if node.decorator_list else node.lineno) - 1
        out = "".join(lines[:start]) + code + "".join(lines[node.end_lineno:])
        changed += sum(1 for l in difflib.unified_diff(s.splitlines(), out.splitlines(), lineterm="", n=0)
                       if l[:1] in "+-" and not l.startswith(("+++", "---")))
        open(p, "w").write(out)
        touched[f] = out
    return touched, changed


def gate(root, changed):
    """Poarta: patch mic + garzile proiectului in copia modificata. (ok, motive)"""
    why = []
    if changed > MAX_CHANGED_LINES:
        why.append(f"patch prea mare: {changed} linii (maxim {MAX_CHANGED_LINES})")
    env = {**os.environ, "SCAN_TIMEFRAME": os.environ.get("SCAN_TIMEFRAME", "4h"), "PYTHONDONTWRITEBYTECODE": "1"}
    for g in ("check_py311.py", "check_integrity.py"):
        r = subprocess.run([sys.executable, "-B", g], cwd=root, capture_output=True, text=True, env=env, timeout=600)
        if r.returncode != 0:
            why.append(f"{g} a respins corectura: " + " | ".join(l for l in r.stdout.splitlines() if "[!]" in l)[:600])
    return not why, why


def open_pr(check, touched, explanation, source):
    branch = f"selfrepair/{check['id']}-{datetime.now(timezone.utc):%Y%m%d-%H%M}"
    run = lambda *c: subprocess.run(c, check=True, capture_output=True, text=True)
    open_heads = json.loads(run("gh", "pr", "list", "--state", "open", "--json", "headRefName").stdout or "[]")
    if any(p["headRefName"].startswith(f"selfrepair/{check['id']}-") for p in open_heads):
        return _summary(f"Exista deja un Pull Request deschis pentru {check['id']} - nu deschid altul.")
    run("git", "checkout", "-b", branch)
    for f, s in touched.items():
        open(f, "w").write(s)
    run("git", "add", *touched)
    run("git", "commit", "-m", f"selfrepair: {check['title']}")
    run("git", "push", "-u", "origin", branch)
    body = (f"**Eroare gasita de self_check.py:** {check['title']}\n\n{check.get('detail')}\n\n"
            f"**Corectura propusa ({source}):** {explanation}\n\n"
            f"**Poarta trecuta:** fisiere permise ({', '.join(touched)}), sintaxa 3.11, check_integrity, patch mic.\n\n"
            "**Verificarea finala:** dupa merge, scanarea urmatoare ruleaza self_check pe date reale; "
            "daca eroarea persista, apare din nou pe dashboard (Starea sistemului).")
    url = run("gh", "pr", "create", "--title", f"selfrepair: {check['title']}", "--body", body,
              "--head", branch).stdout.strip()
    _summary(f"Pull Request deschis: {url}")


def main(dry_run=False):
    sc = json.load(open("data/self_check.json")) if os.path.exists("data/self_check.json") else {}
    errors = [c for c in sc.get("checks") or [] if c.get("level") == "ERROR" and c.get("id") in REGISTRY]
    if not errors:
        return _summary(f"Nicio eroare de logica reparabila (auto-diagnostic: {sc.get('status', 'lipsa')}) - nimic de facut.")
    check = errors[0]
    files = REGISTRY[check["id"]]
    _summary(f"Eroare de reparat: {check['id']} - {check['title']}")
    try:
        text, source = complete(build_prompt(check, files))
        ans = json.loads(text.strip().strip("`").removeprefix("json").strip())
    except Exception as e:
        return _summary(f"Modelul nu a dat un raspuns utilizabil: {e}")
    if not ans.get("changes"):
        return _summary(f"Modelul ({source}) nu a gasit o cauza sigura: {ans.get('explanation', '')[:300]}")
    tmp = tempfile.mkdtemp()
    try:
        shutil.copytree(".", tmp, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git", "docs", "__pycache__"))
        touched, changed = apply_changes(tmp, ans["changes"], files)
        ok, why = gate(tmp, changed)
    except Exception as e:
        ok, why, touched = False, [str(e)], {}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not ok:
        return _summary(f"Corectura RESPINSA de poarta ({source}): " + "; ".join(why))
    if dry_run:
        return _summary(f"[dry-run] Corectura acceptata de poarta ({source}, {changed} linii) - PR-ul ar fi deschis.")
    open_pr(check, touched, ans.get("explanation", ""), source)


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
