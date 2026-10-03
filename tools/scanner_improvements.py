#!/usr/bin/env python3
"""tools/scanner_improvements.py - versiuni incrementale de imbunatatiri pentru hdmihai/scanner.

Starea traieste in artifactul claude.ai (publicat sub improvements/). O rulare:
  1. descarca artifactul intr-un director ROOT (Artifact read cu out_dir=ROOT)
  2. python imp.py sync    --root ROOT --repo REPO
  3. python imp.py prepare --root ROOT --repo REPO --work WORK
  4. (agentul modifica WORK)
  5. python imp.py verify  --root ROOT --repo REPO --work WORK
  6. python imp.py build   --root ROOT --repo REPO --work WORK --new new.json
  7. python imp.py publish-map --root ROOT  -> JSON pentru parametrul `files` al Artifact

Reguli:
- Versiunea curenta = TOATE fisierele modificate fata de base_commit, complete.
  v(N+1) = v(N) + imbunatatirile noi. Se aplica peste HEAD-ul repo-ului cat timp
  codul (tot ce nu e data/ sau docs/) nu s-a schimbat fata de base_commit.
- La sync, daca codul din repo s-a schimbat fata de base_commit: RESET.
  Ciclul creste, versiunea revine la 0, baza devine HEAD. Imbunatatirile aplicate
  se marcheaza "applied"; cele neaplicate ale caror fisiere n-au fost atinse se
  reporteaza automat ("carried") in v1 a ciclului nou; celelalte devin "needs_port".
"""
import argparse, datetime as dt, hashlib, json, os, shutil, subprocess, sys, zipfile

GENERATED_PREFIXES = ("data/", "docs/")          # scrise automat de workflow-uri
IGNORED_FILES = ("tools/scanner_improvements.py",)  # unealta insasi nu declanseaza reset
CHECKS = ["check_py311.py", "check_integrity.py", "self_check.py"]
CHECK_TIMEOUT = 420
IMP = "improvements"


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sh(args, cwd=None, check=True, timeout=None):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        sys.exit(f"eroare: {' '.join(args)}\n{r.stderr.strip()}")
    return r


def sha(b):
    return hashlib.sha256(b).hexdigest()


def is_code(path):
    return (not path.startswith(GENERATED_PREFIXES) and not path.startswith(".imp_") and path not in IGNORED_FILES
            and "__pycache__/" not in path and not path.endswith(".pyc"))


def served(path):
    return f"{IMP}/files/{path}.txt"


def spath(root):
    return os.path.join(root, IMP, "state.json")


def load(root):
    p = spath(root)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save(root, st):
    st["updated_at"] = now()
    os.makedirs(os.path.join(root, IMP), exist_ok=True)
    with open(spath(root), "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def head(repo):
    return sh(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()


def show(repo, commit, path):
    r = sh(["git", "show", f"{commit}:{path}"], cwd=repo, check=False)
    if r.returncode != 0:
        return None
    return subprocess.run(["git", "show", f"{commit}:{path}"], cwd=repo, capture_output=True).stdout


def ensure_commit(repo, commit):
    if sh(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=repo, check=False).returncode != 0:
        sh(["git", "fetch", "--unshallow"], cwd=repo, check=False, timeout=300)
        sh(["git", "fetch", "origin", commit], cwd=repo, check=False, timeout=300)
    if sh(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=repo, check=False).returncode != 0:
        sys.exit(f"eroare: commit-ul de baza {commit} nu exista in clona (clonati fara --depth)")


def read_file(p):
    with open(p, "rb") as f:
        return f.read()


def write_file(p, b):
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "wb") as f:
        f.write(b)


def removed_path(root):
    return os.path.join(root, IMP, ".removed.json")


def add_removed(root, paths):
    p = removed_path(root)
    cur = set(json.load(open(p)) if os.path.exists(p) else [])
    cur.update(paths)
    with open(p, "w") as f:
        json.dump(sorted(cur), f)


def label(st):
    return f"v{st['version']}" if st["version"] else "v0"


# ------------------------------------------------------------------ commands
def cmd_init(a):
    st = load(a.root)
    if st:
        print(json.dumps({"exists": True, "cycle": st["cycle"], "version": st["version"]}))
        return
    h = head(a.repo)
    st = {"schema": 1, "repo": "hdmihai/scanner", "cycle": 1, "version": 0,
          "base_commit": h, "base_date": now(), "files": [], "improvements": [],
          "history": [{"at": now(), "event": "init", "cycle": 1, "version": 0, "base_commit": h,
                       "summary": "Pornire proces de imbunatatiri incrementale."}],
          "last_sync": None, "last_verify": None, "has_patch": False}
    save(a.root, st)
    print(json.dumps({"created": True, "base_commit": h}))


def cmd_sync(a):
    st = load(a.root)
    if not st:
        return cmd_init(a)
    repo, base, h = a.repo, st["base_commit"], head(a.repo)
    ensure_commit(repo, base)
    changed = [p for p in sh(["git", "diff", "--name-only", base, h], cwd=repo).stdout.split() if is_code(p)]
    # starea fiecarui fisier propus fata de HEAD
    fstate = {}
    for f in st["files"]:
        prop = read_file(os.path.join(a.root, f["served"])) if os.path.exists(os.path.join(a.root, f["served"])) else None
        hb, bb = show(repo, h, f["path"]), show(repo, base, f["path"])
        if prop is not None and hb == prop:
            fstate[f["path"]] = "applied"
        elif hb == bb:
            fstate[f["path"]] = "untouched"
        else:
            fstate[f["path"]] = "diverged"
    report = {"at": now(), "head": h, "base_commit": base, "code_changed": changed,
              "files": fstate, "action": "continue"}
    if changed:
        report["action"] = "reset"
        old_cycle, old_version = st["cycle"], st["version"]
        prev_dir = os.path.join(a.root, IMP, ".prev")
        shutil.rmtree(prev_dir, ignore_errors=True)
        for f in st["files"]:
            src = os.path.join(a.root, f["served"])
            if os.path.exists(src):
                write_file(os.path.join(prev_dir, f["path"]), read_file(src))
        applied, carried, port = [], [], []
        for imp in st["improvements"]:
            if imp["status"] not in ("proposed", "carried", "needs_port"):
                continue
            states = [fstate.get(p, "untouched") for p in imp["files"]]
            if imp["status"] == "proposed" and states and all(s == "applied" for s in states):
                imp["status"], imp["status_note"] = "applied", f"aplicat in {h[:7]}"
                applied.append(imp["id"])
            elif imp["status"] in ("proposed", "carried") and all(s == "untouched" for s in states):
                imp["status"], imp["status_note"] = "carried", f"reportat automat din C{old_cycle}"
                carried.append(imp["id"])
            else:
                imp["status"] = "needs_port"
                imp["status_note"] = "fisierele s-au schimbat in repo; trebuie reimplementat pe noua baza"
                port.append(imp["id"])
        add_removed(a.root, [f["served"] for f in st["files"]])
        for f in st["files"]:
            try:
                os.remove(os.path.join(a.root, f["served"]))
            except FileNotFoundError:
                pass
        st["carry_files"] = sorted({p for i in st["improvements"] if i["status"] == "carried" for p in i["files"]})
        st.update(cycle=old_cycle + 1, version=0, base_commit=h, base_date=now(), files=[], has_patch=False)
        st["history"].append({"at": now(), "event": "reset", "cycle": st["cycle"], "version": 0, "base_commit": h,
                              "summary": f"Cod nou in repo ({len(changed)} fisiere). C{old_cycle} v{old_version} inchis. "
                                         f"Aplicate: {len(applied)}, reportate: {len(carried)}, de reimplementat: {len(port)}.",
                              "changed": changed, "applied": applied, "carried": carried, "needs_port": port})
        report.update(applied=applied, carried=carried, needs_port=port, new_cycle=st["cycle"])
    st["last_sync"] = report
    save(a.root, st)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_prepare(a):
    st = load(a.root)
    shutil.rmtree(a.work, ignore_errors=True)
    sh(["git", "clone", "-q", a.repo, a.work])
    sh(["git", "checkout", "-q", head(a.repo)], cwd=a.work)
    n = 0
    for f in st["files"]:
        b = read_file(os.path.join(a.root, f["served"]))
        if sha(b) != f["sha256"]:
            sys.exit(f"eroare: {f['path']} din artifact nu corespunde manifestului")
        write_file(os.path.join(a.work, f["path"]), b)
        n += 1
    carried = 0
    for p in st.get("carry_files", []) if st["version"] == 0 else []:
        src = os.path.join(a.root, IMP, ".prev", p)
        if os.path.exists(src):
            write_file(os.path.join(a.work, p), read_file(src))
            carried += 1
    print(json.dumps({"work": a.work, "overlaid": n, "carried_overlaid": carried,
                      "version": st["version"], "cycle": st["cycle"]}))


def diff_files(work):
    sh(["git", "add", "-A", "-N", "."], cwd=work)
    out = sh(["git", "diff", "--name-only", "HEAD"], cwd=work).stdout.split()
    return sorted(p for p in out if is_code(p))


def diff_patch(work, files):
    if not files:
        return ""
    return sh(["git", "diff", "HEAD", "--"] + files, cwd=work).stdout


def run_checks(d):
    res = {}
    pyc = sh([sys.executable, "-m", "compileall", "-q", "-x", r"(^|/)\.", "."], cwd=d, check=False, timeout=CHECK_TIMEOUT)
    res["py_compile"] = {"ok": pyc.returncode == 0, "out": (pyc.stdout + pyc.stderr)[-1500:]}
    for c in CHECKS:
        if not os.path.exists(os.path.join(d, c)):
            res[c] = {"ok": None, "out": "lipseste"}
            continue
        try:
            r = sh([sys.executable, c], cwd=d, check=False, timeout=CHECK_TIMEOUT)
            res[c] = {"ok": r.returncode == 0, "out": (r.stdout + r.stderr)[-1500:]}
        except subprocess.TimeoutExpired:
            res[c] = {"ok": False, "out": "timeout"}
    return res


def cmd_verify(a):
    files = diff_files(a.work)
    patch = diff_patch(a.work, files)
    # verificarile ruleaza pe copii, ca sa nu polueze WORK (pycache, fisiere scrise de check-uri)
    base_dir, prop_dir = a.work + ".baseline", a.work + ".proposed"
    for d in (base_dir, prop_dir):
        shutil.rmtree(d, ignore_errors=True)
    sh(["git", "clone", "-q", a.repo, base_dir])
    sh(["git", "checkout", "-q", head(a.repo)], cwd=base_dir)
    shutil.copytree(a.work, prop_dir, symlinks=True)
    baseline, proposed = run_checks(base_dir), run_checks(prop_dir)
    shutil.rmtree(prop_dir, ignore_errors=True)
    regress = [k for k in proposed if proposed[k]["ok"] is False and baseline.get(k, {}).get("ok") is True]
    result = {"at": now(), "files": files, "diff_sha": sha(patch.encode()), "baseline": baseline,
              "proposed": proposed, "regressions": regress, "ok": not regress}
    with open(os.path.join(a.work, ".imp_verify.json"), "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    shutil.rmtree(base_dir, ignore_errors=True)
    print(json.dumps({k: result[k] for k in ("files", "regressions", "ok")}, ensure_ascii=False, indent=2))
    print(json.dumps({k: {"baseline": baseline[k]["ok"], "proposed": proposed[k]["ok"]} for k in proposed}, indent=2))
    sys.exit(0 if result["ok"] else 1)


def cmd_build(a):
    st = load(a.root)
    files = diff_files(a.work)
    patch = diff_patch(a.work, files)
    vpath = os.path.join(a.work, ".imp_verify.json")
    if not os.path.exists(vpath):
        sys.exit("eroare: rulati intai `verify`")
    ver = json.load(open(vpath))
    if ver["diff_sha"] != sha(patch.encode()) or not ver["ok"]:
        sys.exit("eroare: verificarea nu corespunde diff-ului curent sau a esuat; rulati din nou `verify`")
    new = json.load(open(a.new)) if a.new else []
    prev = {f["path"]: f["sha256"] for f in st["files"]}
    cur = {p: sha(read_file(os.path.join(a.work, p))) for p in files if os.path.exists(os.path.join(a.work, p))}
    carried = [i for i in st["improvements"] if i["status"] == "carried"]
    if cur == prev and not new:
        print(json.dumps({"new_version": False, "reason": "nicio schimbare fata de versiunea curenta"}))
        return
    if not new and not carried:
        sys.exit("eroare: fisierele s-au schimbat dar --new nu descrie nicio imbunatatire")
    deleted = [p for p in files if not os.path.exists(os.path.join(a.work, p))]
    if deleted:
        sys.exit(f"eroare: stergerile de fisiere nu sunt suportate: {deleted}")
    st["version"] += 1
    v = st["version"]
    n0 = sum(1 for i in st["improvements"] if i["cycle"] == st["cycle"])
    added = []
    for k, item in enumerate(new, 1):
        miss = [p for p in item.get("files", []) if p not in cur]
        if miss:
            sys.exit(f"eroare: imbunatatirea '{item.get('title')}' mentioneaza fisiere nemodificate: {miss}")
        iid = f"C{st['cycle']}-I{n0 + k}"
        for old in item.get("supersedes", []):
            for i in st["improvements"]:
                if i["id"] == old:
                    i["status"], i["status_note"] = "superseded", f"reimplementat ca {iid}"
        st["improvements"].append({
            "id": iid, "title": item["title"], "problem": item.get("problem", ""),
            "evidence": item.get("evidence", ""), "fix": item.get("fix", ""),
            "verification": item.get("verification", ""), "files": item["files"],
            "severity": item.get("severity", "medie"), "cycle": st["cycle"], "version_added": v,
            "status": "proposed", "status_note": "", "created_at": now(),
            "supersedes": item.get("supersedes", [])})
        added.append(iid)
    for i in carried:
        if all(p in cur for p in i["files"]):
            i.update(status="proposed", cycle=st["cycle"], version_added=v,
                     status_note=(i.get("status_note") or "") + f"; inclus in C{st['cycle']} v{v}")
            added.append(i["id"])
        else:
            i.update(status="needs_port", status_note="reportarea automata nu a mai inclus fisierele")
    st.pop("carry_files", None)
    # rescrie fisierele servite
    old_served = {f["served"] for f in st["files"]}
    st["files"] = []
    for p in files:
        b = read_file(os.path.join(a.work, p))
        write_file(os.path.join(a.root, served(p)), b)
        st["files"].append({"path": p, "served": served(p), "sha256": sha(b),
                            "lines": b.count(b"\n"), "new_file": show(a.repo, st["base_commit"], p) is None})
    gone = old_served - {f["served"] for f in st["files"]}
    for s in gone:
        try:
            os.remove(os.path.join(a.root, s))
        except FileNotFoundError:
            pass
    add_removed(a.root, gone)
    write_file(os.path.join(a.root, IMP, "current.patch.txt"), patch.encode())
    st["has_patch"] = True
    st["last_verify"] = {k: ver[k] for k in ("at", "regressions", "ok")} | {
        "checks": {k: {"baseline": ver["baseline"][k]["ok"], "proposed": ver["proposed"][k]["ok"]} for k in ver["proposed"]}}
    st["history"].append({"at": now(), "event": "version", "cycle": st["cycle"], "version": v,
                          "base_commit": st["base_commit"], "improvements": added,
                          "summary": a.summary or f"{len(added)} imbunatatiri noi"})
    save(a.root, st)
    z = make_zip(a.root, st, a.zip_dir)
    print(json.dumps({"new_version": True, "cycle": st["cycle"], "version": v, "added": added,
                      "files": files, "zip": z}, ensure_ascii=False, indent=2))


def apply_md(st):
    return (f"# Scanner improvements - ciclul {st['cycle']}, v{st['version']}\n\n"
            f"Baza: commit `{st['base_commit'][:7]}` din hdmihai/scanner.\n\n"
            "## Aplicare\n\n"
            "Varianta A: copiaza fisierele din aceasta arhiva peste radacina repo-ului (aceleasi cai), apoi commit + push.\n\n"
            "Varianta B: din radacina repo-ului, `git apply changes.patch`, apoi commit + push.\n\n"
            "Fisierele sunt complete si includ TOATE imbunatatirile din v1..v"
            f"{st['version']} ale acestui ciclu. Se aplica peste orice HEAD care nu a modificat codul fata de baza "
            "(commit-urile automate din data/ si docs/ nu conteaza).\n")


def improvements_md(st):
    out = [f"# Imbunatatiri incluse (ciclul {st['cycle']}, v{st['version']})\n"]
    for i in st["improvements"]:
        if i["cycle"] == st["cycle"] and i["status"] == "proposed":
            out.append(f"## {i['id']} (v{i['version_added']}) - {i['title']}\n\n"
                       f"- Fisiere: {', '.join(i['files'])}\n- Problema: {i['problem']}\n- Dovada: {i['evidence']}\n"
                       f"- Fix: {i['fix']}\n- Verificare: {i['verification']}\n")
    return "\n".join(out)


def make_zip(root, st, zip_dir):
    os.makedirs(zip_dir, exist_ok=True)
    name = f"scanner-improvements-C{st['cycle']}-v{st['version']}.zip"
    zp = os.path.join(zip_dir, name)
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in st["files"]:
            z.write(os.path.join(root, f["served"]), f["path"])
        z.write(os.path.join(root, IMP, "current.patch.txt"), "changes.patch")
        z.writestr("APPLY.md", apply_md(st))
        z.writestr("IMPROVEMENTS.md", improvements_md(st))
    return zp


def cmd_publish_map(a):
    st = load(a.root)
    root = os.path.abspath(a.root)
    m = {f"{IMP}/state.json": {"from": os.path.join(root, IMP, "state.json"), "contentType": "application/json"}}
    for f in st["files"]:
        m[f["served"]] = {"from": os.path.join(root, f["served"]), "contentType": "text/plain"}
    if st.get("has_patch") and st["files"]:
        m[f"{IMP}/current.patch.txt"] = {"from": os.path.join(root, IMP, "current.patch.txt"), "contentType": "text/plain"}
    tool = os.path.join(root, IMP, "tool", "imp.py.txt")
    if os.path.exists(tool):
        m[f"{IMP}/tool/imp.py.txt"] = {"from": tool, "contentType": "text/plain"}
    log = os.path.join(root, "scanner-monitor-log.md")
    if os.path.exists(log):
        m["scanner-monitor-log.md"] = log
    rp = removed_path(root)
    if os.path.exists(rp):
        for s in json.load(open(rp)):
            if s not in m:
                m[s] = None
    if not st["files"]:
        m.setdefault(f"{IMP}/current.patch.txt", None)
    print(json.dumps(m, ensure_ascii=False, indent=2))


def cmd_mark(a):
    st = load(a.root)
    for i in st["improvements"]:
        if i["id"] == a.id:
            i["status"], i["status_note"] = a.status, a.note or ""
            save(a.root, st)
            print(json.dumps(i, ensure_ascii=False, indent=2))
            return
    sys.exit(f"eroare: nu exista {a.id}")


def cmd_status(a):
    st = load(a.root)
    if not st:
        print("fara stare")
        return
    print(json.dumps({"cycle": st["cycle"], "version": st["version"], "base_commit": st["base_commit"],
                      "files": [f["path"] for f in st["files"]],
                      "improvements": [{k: i[k] for k in ("id", "title", "status", "version_added", "cycle")}
                                       for i in st["improvements"]],
                      "carry_files": st.get("carry_files", [])}, ensure_ascii=False, indent=2))


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("init", "sync", "prepare", "verify", "build", "publish-map", "mark", "status"):
        s = sub.add_parser(name)
        s.add_argument("--root", required=True)
        if name not in ("publish-map", "mark", "status"):
            s.add_argument("--repo", required=True)
        if name in ("prepare", "verify", "build"):
            s.add_argument("--work", required=True)
        if name == "build":
            s.add_argument("--new", help="JSON cu lista imbunatatirilor noi")
            s.add_argument("--summary", default="")
            s.add_argument("--zip-dir", default="/mnt/user-data/outputs")
        if name == "mark":
            s.add_argument("id")
            s.add_argument("status", choices=["proposed", "applied", "dropped", "superseded", "needs_port"])
            s.add_argument("--note", default="")
    a = p.parse_args()
    {"init": cmd_init, "sync": cmd_sync, "prepare": cmd_prepare, "verify": cmd_verify, "build": cmd_build,
     "publish-map": cmd_publish_map, "mark": cmd_mark, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    main()
