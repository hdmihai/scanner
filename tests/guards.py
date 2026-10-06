# -*- coding: utf-8 -*-
"""tests.guards - verifica gărzile: strica intentionat codul, in copii temporare, si cere ca
check_integrity sa prinda fiecare caz.

Uz (din radacina repo-ului): python -m tests.guards

DE CE: o garda care nu mai gaseste ce verifica (o functie redenumita, un fisier mutat)
trece in tacere - exact cand e nevoie de ea. Fiecare caz de mai jos e o greseala reala
sau plauzibila; daca o garda nu-l mai prinde, testul pica. Daca ancora unui caz nu mai
exista in cod (refactorizare), testul pica tot, cu cererea de a actualiza cazul.
"""

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sub(path, old, new):
    with open(path) as f:
        txt = f.read()
    if old not in txt:
        raise LookupError(f"ancora lipsa in {os.path.relpath(path, ROOT)}: {old.strip()[:50]!r}")
    with open(path, "w") as f:
        f.write(txt.replace(old, new, 1))


def _copy(rel_src, rel_dst):
    def f(d):
        shutil.copy(os.path.join(d, rel_src), os.path.join(d, rel_dst))
    return f


def _rm(rel):
    def f(d):
        os.remove(os.path.join(d, rel))
    return f


# (descriere, mutatie, fragment asteptat in mesajul garzii)
CASES = [
    ("evidentele unui semnal calculate din seria altui token",
     lambda d: _sub(f"{d}/core/analysis.py", "sig_rsi = rsi(closes_s, 14)", "sig_rsi = rsi(closes, 14)"),
     "closes_s"),
    ("functia de evidente redenumita (garda ar ramane fara obiect)",
     lambda d: (_sub(f"{d}/core/analysis.py", "def build_signal_context(", "def build_signal_ctx("),
                _sub(f"{d}/core/scan.py", "build_signal_context", "build_signal_ctx")),
     "build_signal_context"),
    ("modulul unei burse sters", _rm("adapters/exchanges/mexc.py"), "adaptoarele de bursa"),
    ("modulul unei burse cu codul alteia", _copy("adapters/exchanges/okx.py", "adapters/exchanges/kucoin.py"),
     "adaptoarele de bursa"),
    ("fisier din dashboard urcat in locul altuia", _copy("dashboard/page.py", "dashboard/build.py"),
     "fisierul gresit"),
    ("fisier din nucleu urcat in locul altuia", _copy("core/scoring.py", "core/geometry.py"), "fisierul gresit"),
    ("ccxt importat in nucleu",
     lambda d: _sub(f"{d}/core/analysis.py", "from core import elliott as ew_mod\n",
                    "import ccxt\nfrom core import elliott as ew_mod\n"), "retea"),
    ("fisier deschis direct din nucleu",
     lambda d: _sub(f"{d}/core/scan.py", "def save_json(path, data):\n",
                    "def save_json(path, data):\n    open(path, 'w').close()\n"), "deschide un fisier"),
    ("json.dump pe disc in nucleu",
     lambda d: _sub(f"{d}/core/agent.py", "def summarize(state):\n", "def summarize(state):\n    json.dump(state, None)\n"),
     "json.dump"),
    ("nucleul importa o fatada veche",
     lambda d: _sub(f"{d}/core/geometry.py", '"""\n\n', '"""\n\nimport plan_tracker  # noqa\n'),
     "regula dependentelor"),
    ("nucleul importa un adaptor",
     lambda d: _sub(f"{d}/core/learning.py", '"""\n\n', '"""\n\nfrom adapters.storage import json_store  # noqa\n'),
     "regula dependentelor"),
    ("un port importa nucleul",
     lambda d: _sub(f"{d}/ports/store.py", "from typing import Protocol\n",
                    "from typing import Protocol\n\nfrom core import plans  # noqa\n"), "regula dependentelor"),
]


def main():
    work = tempfile.mkdtemp(prefix="garzi-")
    failures = []
    for i, (desc, mutate, expect) in enumerate(CASES, 1):
        d = os.path.join(work, f"caz{i}")
        shutil.copytree(ROOT, d, ignore=shutil.ignore_patterns(".git", "data", "docs", "__pycache__", "*.pyc"))
        for sub in ("data", "docs"):                  # datele doar se citesc: legaturi, nu copii
            if os.path.isdir(os.path.join(ROOT, sub)):
                os.symlink(os.path.join(ROOT, sub), os.path.join(d, sub))
        try:
            mutate(d)
        except (LookupError, OSError) as e:
            failures.append(f"{desc}: cazul nu mai poate fi construit ({e}) - actualizeaza tests/guards.py")
            print(f"  ??  {desc}: {e}")
            continue
        r = subprocess.run([sys.executable, "-B", "check_integrity.py"], cwd=d, capture_output=True, text=True,
                           env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=300)
        msgs = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("[!]")]
        caught = r.returncode != 0 and any(expect in m for m in msgs)
        print(f"  {'OK ' if caught else 'RATAT'} {desc}" + ("" if caught else f"  (exit {r.returncode}: {msgs[:1]})"))
        if not caught:
            failures.append(desc)
        shutil.rmtree(d, ignore_errors=True)
    shutil.rmtree(work, ignore_errors=True)
    print(f"\nGARZILE: {len(CASES) - len(failures)}/{len(CASES)} cazuri prinse" + (" - RESPINS" if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
