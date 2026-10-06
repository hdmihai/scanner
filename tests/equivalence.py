# -*- coding: utf-8 -*-
"""tests.equivalence - poarta de echivalenta: aceeasi scanare pe doua versiuni ale codului.

Uz (din radacina repo-ului):
  python -m tests.equivalence --baseline <arbore vechi> --candidate <arbore nou>
                              [--allow EXPECTED_CHANGES.txt] [--summary fisier.md]

Ambele versiuni primesc ACELEASI date (data/ si docs/ ale candidatului), aceleasi burse
simulate (tests/fakes/, construite din lumanarile reale din data/latest_details.json) si
acelasi timp inghetat. Ruleaza scanarea si pasii de dupa ea din scan.yml si compara tot ce
scriu in data/ si docs/. Scenarii:
  S1  productie: datele actuale (lant complet);
  S2  o bara noua: planurile deschise se evalueaza pe date noi (scanarea);
  S3  fara planuri deschise: toate semnalele trec prin evidente, decizie si plan (lant complet).

Rezultat: cod 0 daca iesirile sunt identice - sau difera doar in fisierele declarate in
--allow (o schimbare de comportament ASUMATA, cu motiv) - si daca niciun pas nu esueaza doar
pe versiunea noua. Altfel cod 1: actualizarea nu se aplica.
"""

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

try:
    from tests import dataset
except ImportError:                                   # rulat ca fisier, nu ca modul
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tests import dataset

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, "runner.py")
PIPELINE = ["ai_agent.py", "self_check.py", "briefing.py", "compact_history.py", "generate_dashboard.py",
            "compact_plans.py"]
SCENARIOS = {
    "S1": ("productie: datele actuale", False, False, True),
    "S2": ("o bara noua: planurile deschise se evalueaza pe date noi", True, False, False),
    "S3": ("fara planuri deschise: lantul complet de evidente, decizie si plan", False, True, True),
}
OPEN_STATES = ("PENDING", "OPEN", "TP1_HIT")


def copy_tree(code_src, data_src, dst):
    shutil.copytree(code_src, dst, ignore=shutil.ignore_patterns(
        ".git", "data", "docs", "__pycache__", "*.pyc", "update.zip", "updates"))
    for sub in ("data", "docs"):
        if os.path.isdir(os.path.join(data_src, sub)):
            shutil.copytree(os.path.join(data_src, sub), os.path.join(dst, sub),
                            ignore=shutil.ignore_patterns("*.tmp"))


def drop_open_plans(dst):
    p = os.path.join(dst, "data", "plans.json")
    if not os.path.exists(p):
        return 0
    with open(p) as f:
        st = json.load(f)
    n0 = len(st.get("plans", []))
    st["plans"] = [x for x in st.get("plans", []) if x.get("state") not in OPEN_STATES]
    with open(p, "w") as f:
        json.dump(st, f)
    return n0 - len(st["plans"])


def run_step(dst, frozen, ds, script, logs):
    t0 = time.monotonic()
    try:
        r = subprocess.run([sys.executable, "-B", RUNNER, dst, repr(frozen), ds, script], cwd=dst,
                           capture_output=True, text=True, timeout=1500,
                           env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        rc, out, err = r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired as e:
        rc, out, err = 124, str(e.stdout or ""), "depasit timpul limita"
    with open(os.path.join(logs, f"{os.path.basename(dst)}.{script}.log"), "w") as f:
        f.write(out + "\n--- stderr ---\n" + err)
    tail = ((err or "").strip().splitlines() or (out or "").strip().splitlines() or [""])[-1]
    return rc, tail[:200], round(time.monotonic() - t0, 1)


def hashes(root):
    """Hash-urile din data/ si docs/, fara ce e timp real (durata analizei per bursa)."""
    out = {}
    for sub in ("data", "docs"):
        for dp, _d, fs in os.walk(os.path.join(root, sub)):
            for fn in fs:
                if fn.endswith(".tmp"):
                    continue
                p = os.path.join(dp, fn)
                with open(p, "rb") as f:
                    raw = f.read()
                if fn == "exchange_scans.json":
                    try:
                        d = json.loads(raw)
                        for v in (d.get("scans") or {}).values():
                            v.pop("duration_s", None)
                        raw = json.dumps(d, sort_keys=True).encode()
                    except ValueError:
                        pass
                elif fn.endswith(".html"):
                    raw = re.sub(rb"analiza [0-9.]+s", b"analiza Xs", raw)
                out[os.path.relpath(p, root).replace(os.sep, "/")] = hashlib.sha256(raw).hexdigest()
    return out


def first_diff(a, b, path=""):
    if type(a) != type(b):
        return f"{path or '/'}: tip {type(a).__name__} -> {type(b).__name__}"
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a or k not in b:
                return f"{path}/{k}: cheie {'noua' if k not in a else 'disparuta'}"
            r = first_diff(a[k], b[k], f"{path}/{k}")
            if r:
                return r
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path or '/'}: {len(a)} -> {len(b)} elemente"
        for i, (x, y) in enumerate(zip(a, b)):
            r = first_diff(x, y, f"{path}[{i}]")
            if r:
                return r
        return None
    return None if a == b else f"{path or '/'}: {str(a)[:50]} -> {str(b)[:50]}"


def explain(base_dir, cand_dir, rel):
    pa, pb = os.path.join(base_dir, rel), os.path.join(cand_dir, rel)
    if not os.path.exists(pa) or not os.path.exists(pb):
        return "fisier nou" if not os.path.exists(pa) else "fisier disparut"
    if rel.endswith(".json"):
        try:
            with open(pa) as fa, open(pb) as fb:
                return first_diff(json.load(fa), json.load(fb)) or "difera doar formatarea"
        except ValueError:
            return "JSON invalid"
    return "continut diferit"


def load_allow(path):
    if not path or not os.path.exists(path):
        return []
    with open(path) as f:
        return [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--candidate", default=".")
    ap.add_argument("--data", default=None, help="de unde vin data/ si docs/ (implicit: candidatul)")
    ap.add_argument("--scenarios", default="S1,S2,S3")
    ap.add_argument("--allow", default=None, help="fisier cu tiparele fisierelor care au voie sa difere")
    ap.add_argument("--summary", default=None, help="raportul in Markdown (ex. $GITHUB_STEP_SUMMARY)")
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--keep", action="store_true", help="pastreaza copiile scenariilor (pentru depanare)")
    a = ap.parse_args()
    base, cand = os.path.abspath(a.baseline), os.path.abspath(a.candidate)
    data_src = os.path.abspath(a.data or a.candidate)
    allow = load_allow(a.allow)
    work = a.workdir or tempfile.mkdtemp(prefix="echivalenta-")
    logs = os.path.join(work, "jurnale")
    os.makedirs(logs, exist_ok=True)
    t_all = time.monotonic()
    ds1 = os.path.join(work, "lumanari.json")
    meta = dataset.build(data_src, ds1)
    print(f"Set de date: {meta['symbols']} simboluri, bara {meta['base_tf']}, din {data_src}/data/latest_details.json")
    lines, failed = [], False
    for sc in [s.strip() for s in a.scenarios.split(",") if s.strip()]:
        desc, extra_bar, no_open, full = SCENARIOS[sc]
        ds, frozen = ds1, dataset.frozen_time(meta)
        if extra_bar:
            ds = os.path.join(work, "lumanari_bara_noua.json")
            dataset.add_bar(ds1, ds)
            frozen += meta["bar_ms"] / 1000
        steps = ["crypto_ai_scanner.py"] + (PIPELINE if full else [])
        dirs, fails = {}, {}
        for side, tree in (("vechi", base), ("nou", cand)):
            dst = os.path.join(work, f"{sc}-{side}")
            shutil.rmtree(dst, ignore_errors=True)
            copy_tree(tree, data_src, dst)
            if no_open:
                drop_open_plans(dst)
            dirs[side], fails[side] = dst, {}
            for st in steps:
                if not os.path.exists(os.path.join(dst, st)):
                    continue
                rc, tail, secs = run_step(dst, frozen, ds, st, logs)
                if rc != 0:
                    fails[side][st] = tail
                    if st == "crypto_ai_scanner.py":
                        break                          # fara scanare, pasii urmatori n-au sens
        ha, hb = hashes(dirs["vechi"]), hashes(dirs["nou"])
        diffs = sorted(k for k in set(ha) | set(hb) if ha.get(k) != hb.get(k))
        unexpected = [d for d in diffs if not any(fnmatch.fnmatch(d, p) for p in allow)]
        regress = {k: v for k, v in fails["nou"].items() if k not in fails["vechi"]}
        # scanarea trebuie sa mearga pe versiunea noua: altfel poarta n-a verificat nimic
        # (doua rulari esuate lasa datele neschimbate - "identic" ar fi o minciuna)
        if "crypto_ai_scanner.py" in fails["nou"]:
            regress["crypto_ai_scanner.py"] = fails["nou"]["crypto_ai_scanner.py"]
        ok = not unexpected and not regress
        failed |= not ok
        verdict = ("IDENTIC" if not diffs else "DIFERENTE ASUMATE") if ok else "DIFERIT"
        print(f"\n[{sc}] {desc}: {verdict} - {len(ha)} fisiere comparate, {len(diffs)} diferite")
        lines.append(f"| {sc} | {desc} | **{verdict}** | {len(ha)} | {len(diffs)} |")
        for d in diffs[:15]:
            tag = "asumat" if d not in unexpected else "NEASTEPTAT"
            print(f"    {tag:10s} {d}: {explain(dirs['vechi'], dirs['nou'], d)}")
        for st, tail in regress.items():
            print(f"    ESUEAZA DOAR PE VERSIUNEA NOUA: {st}: {tail}")
        for st, tail in fails["vechi"].items():
            if st in fails["nou"] and st != "crypto_ai_scanner.py":
                print(f"    (esueaza pe ambele versiuni, deci nu din actualizare: {st}: {tail})")
        if not a.keep:                                # copiile au ~200 MB: raman doar jurnalele
            for dst in dirs.values():
                shutil.rmtree(dst, ignore_errors=True)
    took = round(time.monotonic() - t_all)
    print(f"\nPOARTA DE ECHIVALENTA: {'RESPINS' if failed else 'TRECUT'} ({took}s; jurnale in {logs})")
    if a.summary:
        with open(a.summary, "a") as f:
            f.write("### Poarta de echivalenta: " + ("RESPINS" if failed else "trecut") + f" ({took}s)\n\n"
                    "| Scenariu | Ce verifica | Rezultat | Fisiere | Diferite |\n|---|---|---|---|---|\n"
                    + "\n".join(lines) + "\n\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
