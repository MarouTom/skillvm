#!/usr/bin/env python3
"""Compare installed skills with upstream, or show one skill's version history. Read-only.

  check.py                       check every registered skill, print the Available updates report
  check.py chart-maker 'writing-*'  check only these (names or globs)
  check.py --history SKILL [-n 20]
  check.py --diff SKILL          show local edits against the installed upstream version
  check.py --json                machine-readable result
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib  # noqa: E402


def check(a, reg, skills_dir):
    targets, missing = lib.resolve_targets(a.targets, skills_dir)
    if missing:
        lib.die("no installed skill matches: " + "; ".join(missing))
    rows, res = [], {"up_to_date": [], "locally_modified": [], "pinned": [], "unknown_source": [],
                     "source_unreachable": [], "removed_upstream": [], "not_registered": []}
    unreachable = set()
    for name in targets:
        k = lib.skill_key(skills_dir, name)
        e = reg["skills"].get(k)
        folder = os.path.join(skills_dir, name)
        if e is None:
            res["not_registered"].append(name)
            continue
        if not e.get("source"):
            res["unknown_source"].append(name)
            continue
        if e["source"] in unreachable:
            res["source_unreachable"].append(name)
            continue
        try:
            cache, branch = lib.fetch_source(reg, e["source"], a.no_fetch)
        except lib.GitError:
            unreachable.add(e["source"])
            res["source_unreachable"].append(name)
            continue
        latest_tree = lib.tree_at(cache, branch, e["path"])
        modified = e.get("commit") is None or lib.fingerprint(folder) != e["fingerprint"]
        if not latest_tree:
            res["removed_upstream"].append(name)
            continue
        if latest_tree == e["fingerprint"] and not modified:
            res["up_to_date"].append(name)
            continue
        has_update = latest_tree != e["fingerprint"]
        if modified:
            res["locally_modified"].append(name + (" (update available)" if has_update else ""))
            continue
        if e.get("pinned"):
            res["pinned"].append(name + " (update available)")
            continue
        latest = lib.last_commit(cache, branch, e["path"])
        s = lib.change_summary(cache, e["commit"], e.get("installed_path", e["path"]), latest, e["path"])
        m_new = lib.meta_at(cache, latest, e["path"])
        rows.append({
            "name": name, "action": "update available", "src": e["source"],
            "frm": lib.point(e.get("version"), e["commit"], lib.commit_date(cache, e["commit"])),
            "to": lib.point(m_new["version"], latest, lib.commit_date(cache, latest)),
            "head": lib.headline(s, "moved %s → %s" % (e["installed_path"], e["path"])
                                 if e.get("installed_path", e["path"]) != e["path"] else None),
            "details": lib.detail_lines(s), "summary": s, "latest": latest,
        })
    if a.json:
        print(json.dumps({"updates": rows, **res}, indent=1))
        return
    not_changed = [("Up to date", len(res["up_to_date"])),
                   ("Locally modified", res["locally_modified"]),
                   ("Pinned", res["pinned"]),
                   ("Unknown source", [n + " (%s)" % reg["skills"][lib.skill_key(skills_dir, n)].get("note", "no source recorded")
                                       if reg["skills"][lib.skill_key(skills_dir, n)].get("note") else n
                                       for n in res["unknown_source"]]),
                   ("Source unreachable", res["source_unreachable"])]
    if res["removed_upstream"]:
        not_changed.append(("Removed upstream", res["removed_upstream"]))
    if res["not_registered"]:
        not_changed.append(("Not registered (run bootstrap.py)", res["not_registered"]))
    print(lib.render_report("available", "check", rows, not_changed))


def history(a, reg, skills_dir):
    name = a.history
    k = lib.skill_key(skills_dir, name)
    e = reg["skills"].get(k)
    if e is None:
        lib.die("%s is not registered; run bootstrap.py first" % name)
    folder = os.path.join(skills_dir, name)
    cur_fp = lib.fingerprint(folder) if os.path.isdir(folder) else None
    print("## Version history · %s\n" % name)
    print("### Local snapshots")
    snaps = lib.list_snapshots(k)
    if snaps:
        print("| Snapshot | Version | Commit | Reason | Taken (UTC) |\n|---|---|---|---|---|")
        for s in snaps:
            mark = " ← installed" if s["fingerprint"] == cur_fp else ""
            print("| `%s`%s | %s | %s | %s | %s |" % (s["id"], mark, s.get("version") or "—",
                                                    "`%s`" % lib.short(s["commit"]) if s.get("commit") else "—",
                                                    s["reason"], s["created_at"][:16].replace("T", " ")))
    else:
        print("None yet. A snapshot is taken before every change.")
    print("\n### Upstream versions")
    if not e.get("source"):
        print("No upstream source recorded.")
        return
    cache, branch = lib.fetch_source(reg, e["source"], a.no_fetch)
    args = ["-C", cache, "log", "-n", str(a.n), "--format=%H %cs %s", branch]
    if lib.gp(e["path"]):
        args += ["--", e["path"]]
    lines = lib.git(*args).splitlines()
    print("%s · `%s` · newest first\n" % (e["source"], e["path"]))
    print("| Commit | Date | Version | Tags | Subject |\n|---|---|---|---|---|")
    for l in lines:
        c, d, subj = l.split(" ", 2)
        tags = lib.git("-C", cache, "tag", "--points-at", c).split()
        mark = " ← installed" if c == e.get("commit") and cur_fp == e.get("fingerprint") else ""
        print("| `%s`%s | %s | %s | %s | %s |" % (c[:7], mark, d, lib.meta_at(cache, c, e["path"])["version"] or "—",
                                                ", ".join(tags) or "", subj.replace("|", "\\|")))
    if e.get("commit") and e["commit"] not in " ".join(lines):
        print("\nInstalled: `%s` (older than the %d shown)" % (lib.short(e["commit"]), len(lines)))


def local_diff(a, reg, skills_dir):
    """Show local edits: the installed folder against the upstream version it was installed from."""
    import shutil
    import subprocess
    name = a.diff
    e = reg["skills"].get(lib.skill_key(skills_dir, name))
    if not e or not e.get("source"):
        lib.die("%s has no recorded source to compare with" % name)
    cache, _ = lib.fetch_source(reg, e["source"], no_fetch=True)
    if e.get("commit"):
        base, label = lib.stage_upstream(cache, e["commit"], e.get("installed_path", e["path"]), name), "upstream %s" % lib.short(e["commit"])
    else:
        snaps = lib.list_snapshots(lib.skill_key(skills_dir, name))
        if not snaps:
            lib.die("%s has no exact upstream match and no snapshot to compare with" % name)
        base, label = os.path.join(lib.snapshots_root(lib.skill_key(skills_dir, name)), snaps[-1]["id"], "files"), "snapshot %s" % snaps[-1]["id"]
    print("## Local edits · %s (compared with %s)\n" % (name, label))
    out = subprocess.run(["diff", "-ru", "-x", ".DS_Store", "-x", "__pycache__", base, os.path.join(skills_dir, name)],
                         capture_output=True, text=True).stdout
    print("```diff\n%s```" % (out.replace(base, "a").replace(os.path.join(skills_dir, name), "b") or "(no differences)\n"))
    if base.startswith(os.path.join(lib.STORE, "tmp")):
        shutil.rmtree(base, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("targets", nargs="*")
    ap.add_argument("--history", metavar="SKILL")
    ap.add_argument("--diff", metavar="SKILL", help="show local edits against the installed upstream version")
    ap.add_argument("-n", type=int, default=20, help="upstream versions to list (default 20)")
    lib.add_common_args(ap)
    ap.add_argument("--no-fetch", action="store_true", help="use the cache as is (offline)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    skills_dir = lib.setup(a)
    if not os.path.exists(lib.registry_path()):
        lib.die("no registry yet; run bootstrap.py first")
    lib.ensure_store()
    reg = lib.load_registry()
    (history if a.history else local_diff if a.diff else check)(a, reg, skills_dir)


if __name__ == "__main__":
    try:
        main()
    except (lib.UserError, lib.GitError) as e:
        lib.die(str(e))
