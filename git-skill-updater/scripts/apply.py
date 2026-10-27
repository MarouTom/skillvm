#!/usr/bin/env python3
"""Change installed skills: every change is snapshotted, validated, recorded, and undoable.

  apply.py update [SKILL|GLOB ...|all] [--dry-run] [--overwrite-local SKILL ...]
  apply.py rollback SKILL [TARGET]       TARGET: snapshot id, date (YYYY-MM-DD), commit, tag, or version
  apply.py install REF [SKILL ...] [--all] [--replace] [--as NAME] [--dry-run]
  apply.py pin SKILL [REF]               REF: commit or snapshot id (default: what's installed)
  apply.py unpin SKILL

Exit codes: 0 done, 1 error, 2 needs a decision from the user (the message says which).
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib  # noqa: E402

NEEDS_DECISION = 2


class Run:
    def __init__(self, a, reg, skills_dir):
        self.a, self.reg, self.skills_dir = a, reg, skills_dir
        self.rows, self.skipped, self.failed, self.uptodate, self.pruned = [], [], [], 0, 0
        self.rematched, self.lost = [], []
        self.decision = []

    def key(self, name):
        return lib.skill_key(self.skills_dir, name)

    def folder(self, name):
        return os.path.join(self.skills_dir, name)

    def row(self, name, action, frm, to, src, head=None, details=()):
        self.rows.append({"name": name, "action": action, "frm": frm, "to": to, "src": src,
                          "head": head, "details": list(details)})

    def entry_point(self, e, cache=None):
        date = lib.commit_date(cache, e.get("commit")) if cache and e.get("commit") else None
        return lib.point(e.get("version"), e.get("commit"), date)

    def swap_in(self, name, staged, expected_fp, reason, e):
        """Snapshot current folder (if any), install staged folder. Returns (snapshot_id, error)."""
        sid = None
        if os.path.isdir(self.folder(name)):
            sid = lib.take_snapshot(self.key(name), self.folder(name), reason, e)
        err = lib.install_folder(staged, self.folder(name), expected_fp)
        return sid, err

    def refresh(self, cache, branch, name, e):
        """Re-match the recorded commit if upstream rewrote history; saved unless this is a dry run."""
        state = lib.refresh_commit(cache, branch, name, e)
        if state != "ok":
            (self.rematched if state == "rematched" else self.lost).append(name)
            if not self.a.dry_run:
                lib.save_registry(self.reg)
                lib.append_history({"skill": self.key(name), "action": "rematch", "result": state,
                                    "commit": lib.short(e.get("commit"))})
        return state

    def report(self, mode):
        nc = [("Up to date", self.uptodate), ("Skipped", self.skipped), ("Failed", self.failed)]
        if self.rematched:
            nc.append(("Re-matched after upstream rewrote its history", self.rematched))
        if self.lost:
            nc.append(("Recorded version no longer upstream (treated as locally modified)", self.lost))
        if self.pruned:
            nc.append(("Old snapshots pruned", self.pruned))
        print(lib.render_report("available" if self.a.dry_run else "changes",
                                mode + (" (dry run)" if self.a.dry_run else ""), self.rows, nc,
                                footer="Dry run: nothing applied." if self.a.dry_run else None))
        for d in self.decision:
            print("\nNEEDS DECISION: " + d)


# --- update -----------------------------------------------------------------

def cmd_update(r):
    targets, missing = lib.resolve_targets(r.a.skills, r.skills_dir)
    if missing:
        lib.die("no installed skill matches: " + "; ".join(missing))
    targets.sort(key=lambda n: n == "git-skill-updater")  # update the updater last
    overwrite = set(r.a.overwrite_local or [])
    for name in targets:
        e = r.reg["skills"].get(r.key(name))
        if e is None:
            r.skipped.append("%s (not registered)" % name)
            continue
        if os.path.islink(r.folder(name)):
            r.skipped.append("%s (symlink, local development copy)" % name)
            continue
        if not e.get("source"):
            r.skipped.append("%s (unknown source)" % name)
            continue
        if e.get("pinned"):
            r.skipped.append("%s (pinned)" % name)
            continue
        try:
            cache, branch = lib.fetch_source(r.reg, e["source"])
        except lib.GitError:
            r.skipped.append("%s (source unreachable)" % name)
            continue
        r.refresh(cache, branch, name, e)
        latest_tree = lib.tree_at(cache, branch, e["path"])
        if not latest_tree:
            r.skipped.append("%s (removed upstream)" % name)
            continue
        cur_fp = lib.fingerprint(r.folder(name))
        modified = e.get("commit") is None or cur_fp != e["fingerprint"]
        if latest_tree == cur_fp:
            r.uptodate += 1
            continue
        if latest_tree == e["fingerprint"] and not modified:
            r.uptodate += 1
            continue
        if modified and name not in overwrite:
            if name in r.lost:
                r.skipped.append("%s (installed version no longer upstream)" % name)
                r.decision.append("%s: the upstream history was rewritten and the installed version is no longer "
                                  "in it. To replace it with the latest (the current files stay in a snapshot): "
                                  "apply.py update %s --overwrite-local %s" % (name, name, name))
            else:
                r.skipped.append("%s (local edits kept)" % name)
                r.decision.append("%s has local edits. Show them with: check.py --diff %s. "
                                  "To replace them (they stay in a snapshot): apply.py update %s --overwrite-local %s"
                                  % (name, name, name, name))
            continue
        latest = lib.last_commit(cache, branch, e["path"])
        oldp = e.get("installed_path", e["path"])
        s = (lib.change_summary(cache, e["commit"], oldp, latest, e["path"]) if e.get("commit")
             else lib.change_summary(cache, None, None, latest, e["path"]))
        m_new = lib.meta_at(cache, latest, e["path"])
        to = lib.point(m_new["version"], latest, lib.commit_date(cache, latest))
        frm = r.entry_point(e, cache)
        head = lib.headline(s, "replaces local edits" if modified else None)
        if r.a.dry_run:
            r.row(name, "update available", frm, to, e["source"], head, lib.detail_lines(s))
            continue
        staged = lib.stage_upstream(cache, latest, e["path"], name)
        sid, err = r.swap_in(name, staged, latest_tree, "pre-update", e)
        if err:
            r.row(name, "restored after failed update", frm, frm, e["source"], err)
            r.failed.append("%s (%s)" % (name, err))
            lib.append_history({"skill": r.key(name), "action": "restore-failed", "error": err, "snapshot": sid})
            continue
        before = {"commit": lib.short(e.get("commit")), "version": e.get("version")}
        e.update(commit=latest, path=e["path"], installed_path=e["path"], version=m_new["version"],
                 fingerprint=latest_tree, updated_at=lib.now_iso())
        e.pop("likely", None)
        lib.save_registry(r.reg)
        lib.append_history({"skill": r.key(name), "action": "update", "from": before,
                            "to": {"commit": lib.short(latest), "version": m_new["version"]}, "snapshot": sid})
        r.pruned += lib.prune_snapshots(r.key(name), e)
        r.row(name, "updated", frm, to, e["source"], head, lib.detail_lines(s) + ["snapshot `%s` holds the previous version" % sid])
    r.report("update")


# --- rollback ---------------------------------------------------------------

def cmd_rollback(r):
    name, target = r.a.skill, r.a.target
    k = r.key(name)
    e = r.reg["skills"].get(k)
    if e is None:
        lib.die("%s is not registered" % name)
    if os.path.islink(r.folder(name)):
        lib.die("%s is a symlink (local development copy); not touching it" % name)
    snaps = lib.list_snapshots(k)
    cur_fp = lib.fingerprint(r.folder(name)) if os.path.isdir(r.folder(name)) else None
    cache = branch = None
    if e.get("source"):
        try:
            cache, branch = lib.fetch_source(r.reg, e["source"])
            r.refresh(cache, branch, name, e)
        except lib.GitError:
            pass

    snap = None
    commit = None
    if target is None:
        last = next((h for h in reversed(lib.read_history()) if h.get("skill") == k and h["action"] == "update"), None)
        sid = last.get("snapshot") if last else None
        snap = next((s for s in snaps if s["id"] == sid), None) or next((s for s in snaps if s["reason"] == "pre-update"), None)
        if not snap:
            lib.die("no earlier version of %s on this machine yet; give a commit, tag or version to roll back to" % name)
    elif re.match(r"^\d{8}T\d{6}Z(-\d+)?$", target):
        snap = next((s for s in snaps if s["id"] == target), None) or lib.die("no snapshot %s for %s" % (target, name))
    elif re.match(r"^\d{4}-\d{2}-\d{2}$", target):
        day = target.replace("-", "")
        snap = next((s for s in snaps if s["id"][:8] <= day), None) or lib.die("no snapshot of %s on or before %s" % (name, target))
    else:
        if not cache:
            lib.die("%s has no reachable upstream source; only local snapshots can be restored" % name)
        commit = lib.git("-C", cache, "rev-parse", "-q", "--verify", target + "^{commit}", check=False).strip()
        if not commit:
            commit = lib.find_version(cache, branch, e["path"], target)
        if not commit:
            versions = sorted({lib.meta_at(cache, c, e["path"])["version"] or "" for c in
                               lib.git("-C", cache, "log", "-n", "40", "--format=%H", branch, "--", e["path"]).split()} - {""})
            lib.die("no commit, tag or version '%s' for %s. Versions upstream: %s" % (target, name, ", ".join(versions) or "none recorded"))

    if snap:
        to_fp, to_commit, to_version = snap["fingerprint"], snap.get("commit"), snap.get("version")
        to_path = e.get("installed_path", e.get("path"))
        label = "snapshot `%s`" % snap["id"]
    else:
        path = e.get("installed_path", e["path"])
        to_path = path if lib.tree_at(cache, commit, path) else e["path"]
        to_fp = lib.tree_at(cache, commit, to_path)
        if not to_fp:
            lib.die("%s did not exist at %s" % (name, lib.short(commit)))
        to_commit, to_version = commit, lib.meta_at(cache, commit, to_path)["version"]
        label = "upstream `%s`" % lib.short(commit)
    frm = r.entry_point(e, cache)
    to = lib.point(to_version, to_commit, lib.commit_date(cache, to_commit) if cache and to_commit else None)
    if to_fp == cur_fp:
        r.skipped.append("%s (already at %s)" % (name, label))
        r.report("rollback")
        return
    if r.a.dry_run:
        r.row(name, "rolled back", frm, to, e.get("source") or "local", "would restore " + label)
        r.report("rollback")
        return
    staged = lib.stage_snapshot(k, snap["id"], name) if snap else lib.stage_upstream(cache, commit, to_path, name)
    sid, err = r.swap_in(name, staged, to_fp, "pre-rollback", e)
    if err:
        r.failed.append("%s (%s)" % (name, err))
        r.report("rollback")
        return
    before = {"commit": lib.short(e.get("commit")), "version": e.get("version")}
    e.update(commit=to_commit, installed_path=to_path, version=to_version, fingerprint=to_fp, updated_at=lib.now_iso())
    lib.save_registry(r.reg)
    lib.append_history({"skill": k, "action": "rollback", "from": before,
                        "to": {"commit": lib.short(to_commit), "version": to_version, "from": label}, "snapshot": sid})
    r.row(name, "rolled back", frm, to, e.get("source") or "local", "restored " + label,
          ["snapshot `%s` holds the version you rolled back from" % sid])
    r.report("rollback")
    print("\nNEEDS DECISION: pin %s at this version so 'update all' doesn't undo the rollback? "
          "If yes: apply.py pin %s" % (name, name))


# --- install ----------------------------------------------------------------

def cmd_install(r):
    ref = lib.parse_ref(r.a.ref)
    cache, default = lib.fetch(ref["key"], ref["url"])
    rev = ref["ref"] or default
    commit = lib.git("-C", cache, "rev-parse", "-q", "--verify", rev + "^{commit}", check=False).strip()
    if not commit:
        lib.die("no branch, tag or commit '%s' in %s" % (rev, ref["key"]))
    is_branch = bool(lib.git("-C", cache, "rev-parse", "-q", "--verify", "refs/heads/" + rev, check=False).strip())
    dirs = lib.skill_dirs(cache, commit)
    if not dirs:
        lib.die("%s has no SKILL.md at %s" % (ref["key"], rev))
    if ref["path"]:
        chosen = [ref["path"]] if ref["path"] in dirs else lib.die("no SKILL.md under %s in %s" % (ref["path"], ref["key"]))
    elif r.a.skills:
        chosen, missing = [], []
        for want in r.a.skills:
            hit = [d for d in dirs if os.path.basename(d) == want] or \
                  [d for d in dirs if lib.meta_at(cache, commit, d)["name"] == want]
            # prefer the shortest path when a repo carries copies for several agents
            (chosen.append(min(hit, key=lib.path_rank)) if hit else missing.append(want))
        if missing:
            lib.die("not in %s: %s" % (ref["key"], ", ".join(missing)))
    elif len(lib.preferred_dirs(dirs)) == 1:
        chosen = lib.preferred_dirs(dirs)
    elif r.a.all:
        chosen = lib.preferred_dirs(dirs)
    else:
        print("## Skills in %s @ %s\n" % (ref["key"], rev))
        print("| Skill | Path | Version | Description |\n|---|---|---|---|")
        for d in lib.preferred_dirs(dirs):
            m = lib.meta_at(cache, commit, d)
            desc = (m["description"] or "").split(". ")[0][:90]
            print("| %s | `%s` | %s | %s |" % (os.path.basename(d) if d != "." else ref["key"].split("/")[-1], d,
                                            m["version"] or "—", desc.replace("|", "\\|")))
        print("\nNEEDS DECISION: which skills to install? Rerun with their names, or --all.")
        sys.exit(NEEDS_DECISION)
    if r.a.as_name and len(chosen) != 1:
        lib.die("--as works with exactly one skill")

    src = r.reg["sources"].setdefault(ref["key"], {"type": "git", "url": ref["url"]})
    if is_branch or not ref["ref"]:
        src.setdefault("branch", rev)
    clashes = []
    plan = []
    for d in chosen:
        name = r.a.as_name or (os.path.basename(d) if d != "." else ref["key"].split("/")[-1])
        e = r.reg["skills"].get(r.key(name))
        if os.path.lexists(r.folder(name)):
            if e and e.get("source") == ref["key"] and e.get("path") == d:
                r.skipped.append("%s (already installed from %s; use update)" % (name, ref["key"]))
                continue
            if os.path.islink(r.folder(name)):
                r.skipped.append("%s (existing folder is a symlink)" % name)
                continue
            if not r.a.replace:
                clashes.append(name)
                continue
        plan.append((name, d))
    for name, d in plan:
        c = lib.last_commit(cache, commit, d)  # the last commit that touched this skill, not the repo tip
        tree = lib.tree_at(cache, c, d)
        m = lib.meta_at(cache, c, d)
        s = lib.change_summary(cache, None, None, c, d)
        to = lib.point(m["version"], c, lib.commit_date(cache, c))
        replacing = os.path.lexists(r.folder(name))
        head = ("replaces existing folder · " if replacing else "new skill · ") + "%d file%s" % (s["files"], "" if s["files"] == 1 else "s")
        if r.a.dry_run:
            r.row(name, "installed", "—", to, ref["key"], head, lib.detail_lines(s))
            continue
        old = r.reg["skills"].get(r.key(name), {})
        staged = lib.stage_upstream(cache, c, d, name)
        sid, err = r.swap_in(name, staged, tree, "pre-install", old)
        if err:
            r.failed.append("%s (%s)" % (name, err))
            continue
        t = lib.now_iso()
        r.reg["skills"][r.key(name)] = {
            "source": ref["key"], "path": d, "installed_path": d, "commit": c, "version": m["version"],
            "fingerprint": tree, "installed_at": t, "updated_at": t,
            "pinned": None if (is_branch or not ref["ref"]) else c}
        lib.save_registry(r.reg)
        lib.append_history({"skill": r.key(name), "action": "install", "to": {"commit": lib.short(c), "version": m["version"]},
                            "source": ref["key"], "snapshot": sid})
        extra = ["snapshot `%s` holds the folder it replaced" % sid] if sid else []
        if not (is_branch or not ref["ref"]):
            extra.append("pinned at `%s` because a tag or commit was requested" % lib.short(c))
        r.row(name, "installed", "—", to, ref["key"], head, lib.detail_lines(s) + extra)
    if clashes:
        r.skipped += ["%s (folder exists)" % c for c in clashes]
        r.decision.append("already installed from elsewhere: %s. Rerun with --replace (old folder is snapshotted) "
                          "or --as NEWNAME (one skill)." % ", ".join(clashes))
    r.report("install")
    if clashes:
        sys.exit(NEEDS_DECISION)


# --- pin / unpin ------------------------------------------------------------

def cmd_pin(r, pin):
    name = r.a.skill
    e = r.reg["skills"].get(r.key(name))
    if not e:
        lib.die("%s is not registered" % name)
    cache = None
    if e.get("source"):
        try:
            cache, _ = lib.fetch_source(r.reg, e["source"], no_fetch=True)
        except lib.GitError:
            pass
    here = r.entry_point(e, cache)
    if pin:
        value = getattr(r.a, "ref", None) or e.get("commit")
        if not value:
            value = lib.take_snapshot(r.key(name), r.folder(name), "manual", e)
        e["pinned"] = value
        action = "pinned"
    else:
        if not e.get("pinned"):
            lib.die("%s is not pinned" % name)
        value = e["pinned"]
        e["pinned"] = None
        action = "unpinned"
    lib.save_registry(r.reg)
    lib.append_history({"skill": r.key(name), "action": action, "at_ref": value})
    r.row(name, action, here, here, e.get("source") or "local",
          "'update all' %s this skill" % ("skips" if pin else "includes"))
    r.report("pin" if pin else "unpin")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    lib.add_common_args(ap)
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("update")
    u.add_argument("skills", nargs="*")
    u.add_argument("--overwrite-local", nargs="+", metavar="SKILL")
    rb = sub.add_parser("rollback")
    rb.add_argument("skill")
    rb.add_argument("target", nargs="?")
    i = sub.add_parser("install")
    i.add_argument("ref")
    i.add_argument("skills", nargs="*")
    i.add_argument("--all", action="store_true")
    i.add_argument("--replace", action="store_true")
    i.add_argument("--as", dest="as_name")
    p = sub.add_parser("pin")
    p.add_argument("skill")
    p.add_argument("ref", nargs="?")
    up = sub.add_parser("unpin")
    up.add_argument("skill")
    for x in (u, rb, i):
        x.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    a.dry_run = getattr(a, "dry_run", False)
    skills_dir = lib.setup(a)
    lib.ensure_store()
    if a.cmd != "install" and not os.path.exists(lib.registry_path()):
        lib.die("no registry yet; run bootstrap.py first")
    reg = lib.load_registry()
    r = Run(a, reg, skills_dir)
    {"update": cmd_update, "rollback": cmd_rollback, "install": cmd_install,
     "pin": lambda r: cmd_pin(r, True), "unpin": lambda r: cmd_pin(r, False)}[a.cmd](r)
    if r.decision and a.cmd == "update":
        sys.exit(NEEDS_DECISION)


if __name__ == "__main__":
    try:
        main()
    except (lib.UserError, lib.GitError) as e:
        lib.die(str(e))
