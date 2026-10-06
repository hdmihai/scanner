#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compact_git.py - compacteaza istoricul git: pastreaza istoria codului si elimina versiunile vechi ale datelor.

DE CE
-----
Fiecare scanare orara rescrie data/ si docs/. Diferentele dintre doua versiuni sunt
mici, dar GitHub pastreaza periodic COPII COMPLETE ale fisierelor mari (plans.json
~24 MB, backtest_plans.json ~23 MB): repo-ul crestea cu ~18 MB pe zi, desi codul se
schimba rar.

CE FACE
-------
1. Retine starea curenta exacta (data/ si docs/ din HEAD).
2. Rescrie istoricul fara data/ si docs/ (git filter-repo): commit-urile de cod raman,
   cu mesajele si datele lor; commit-urile care atingeau doar datele dispar.
3. Readuce starea curenta intr-un singur commit.
4. VERIFICA: arborele final trebuie sa fie identic bit cu bit cu cel de la inceput -
   altfel nu publica nimic.
5. Publica cu --force-with-lease: daca altcineva a impins intre timp, nu suprascrie.

Istoricul datelor se pierde pana la momentul compactarii (codul nu). Ruleaza din
workflow-ul compact_git.yml, cu un token care are dreptul sa rescrie si commit-urile
care ating workflow-uri (GITHUB_TOKEN nu il are).

Uz: python compact_git.py --mode masoara|compacteaza [--branch main] [--min-mb 0] [--summary FISIER]
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

PATHS = ("data", "docs")
BOT = ["-c", "user.name=crypto-ai-scanner-bot", "-c", "user.email=actions@users.noreply.github.com"]


def git(*args, check=True):
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} a esuat: {(r.stderr or r.stdout).strip()[:400]}")
    return r.stdout.strip()


def size_mb():
    kv = dict(line.split(": ", 1) for line in git("count-objects", "-v").splitlines() if ": " in line)
    return (int(kv.get("size-pack", 0)) + int(kv.get("size", 0))) / 1024


def main():
    ap = argparse.ArgumentParser(description="Compacteaza istoricul datelor din git")
    ap.add_argument("--mode", choices=["masoara", "compacteaza"], default="masoara")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--min-mb", type=float, default=0.0, help="nu compacta sub aceasta dimensiune")
    ap.add_argument("--summary", default=None)
    a = ap.parse_args()
    t0 = time.monotonic()
    if git("status", "--porcelain", "--untracked-files=no"):
        raise SystemExit("Arborele de lucru are modificari - compactarea porneste doar dintr-o copie curata.")
    if git("rev-parse", "--is-shallow-repository") == "true":
        raise SystemExit("Copie partiala (shallow): e nevoie de tot istoricul (fetch-depth: 0).")
    if git("rev-parse", "--abbrev-ref", "HEAD") != a.branch:
        raise SystemExit(f"Nu sunt pe ramura {a.branch}.")
    old, tree = git("rev-parse", "HEAD"), git("rev-parse", "HEAD^{tree}")
    n_before = int(git("rev-list", "--count", "HEAD"))
    git("gc", "-q", "--prune=now")
    before = size_mb()
    present = [p for p in PATHS if git("ls-tree", "-d", "--name-only", "HEAD", p, check=False)]
    rows = [("Dimensiune istoric", f"{before:.1f} MB"), ("Commit-uri", str(n_before))]
    if before < a.min_mb:
        rows.append(("Rezultat", f"sub pragul de {a.min_mb:.0f} MB - nimic de facut"))
        return report(a, rows, "nimic de facut", t0)
    snap = tempfile.mkdtemp(prefix="stare-curenta-")
    subprocess.run(f"git archive HEAD {' '.join(present)} | tar -x -C {snap}", shell=True, check=True)
    origin = git("remote", "get-url", "origin", check=False)
    r = subprocess.run(["git", "filter-repo", "--force", "--invert-paths"]
                       + [x for p in present for x in ("--path", p + "/")], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"git filter-repo a esuat: {(r.stderr or r.stdout).strip()[-400:]}")
    for p in present:
        shutil.copytree(os.path.join(snap, p), p, dirs_exist_ok=True)
    git("add", "-A", "--", *present)
    git(*BOT, "commit", "-q", "-m", f"stare curenta dupa compactarea istoricului git "
        f"({time.strftime('%Y-%m-%d', time.gmtime())}): codul isi pastreaza istoria, datele pornesc de aici")
    new_tree = git("rev-parse", "HEAD^{tree}")
    if new_tree != tree:
        raise SystemExit(f"ARBORELE FINAL DIFERA de cel initial ({new_tree[:12]} fata de {tree[:12]}) - "
                         f"nu public nimic.")
    n_after = int(git("rev-list", "--count", "HEAD"))
    git("reflog", "expire", "--expire=now", "--all")
    git("gc", "-q", "--prune=now")
    after = size_mb()
    rows += [("Dupa compactare", f"{after:.1f} MB ({100 * (1 - after / before):.0f}% mai mic)" if before else f"{after:.1f} MB"),
             ("Commit-uri dupa", f"{n_after} (au disparut {n_before - n_after} commit-uri care atingeau doar datele)"),
             ("Arbore final", "identic cu cel initial (verificat)")]
    if a.mode == "masoara":
        rows.append(("Rezultat", "doar masurat - nimic publicat"))
        return report(a, rows, "masurat", t0)
    if origin and not git("remote", check=False):
        git("remote", "add", "origin", origin)
    git("push", f"--force-with-lease={a.branch}:{old}", "origin", f"HEAD:{a.branch}")
    rows.append(("Rezultat", f"publicat pe {a.branch} (--force-with-lease fata de {old[:7]})"))
    return report(a, rows, "compactat si publicat", t0)


def report(a, rows, verdict, t0):
    took = round(time.monotonic() - t0)
    print(f"\nCOMPACTARE ISTORIC GIT: {verdict} ({took}s)")
    for k, v in rows:
        print(f"  {k}: {v}")
    if a.summary:
        with open(a.summary, "a") as f:
            f.write(f"### Compactarea istoricului git: {verdict} ({took}s)\n\n| | |\n|---|---|\n"
                    + "\n".join(f"| {k} | {v} |" for k, v in rows) + "\n\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
