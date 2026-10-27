#!/usr/bin/env python3
"""Find where installed skills came from and record it in the registry.

Safe to re-run: skills already confirmed are left alone, new ones are added.
Never modifies skill folders; writes only to the version store.

  bootstrap.py                         markers, frontmatter hints, known sources
  bootstrap.py owner/repo ...          also try these repos
  bootstrap.py --search                also search GitHub by author for what's left
  bootstrap.py --accept-likely a b     record unconfirmed candidates the user approved
  bootstrap.py --set-source a owner/repo   record a source for one skill (verified if possible)
"""
import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib  # noqa: E402


def read_markers(skills_dir):
    """Repos named in install-marker files (e.g. .foo-install.txt) in the skills folder."""
    repos, local = [], {}
    for f in sorted(os.listdir(skills_dir)):
        if not (f.startswith(".") and "install" in f.lower()):
            continue
        p = os.path.join(skills_dir, f)
        if not os.path.isfile(p):
            continue
        text = open(p, encoding="utf-8", errors="replace").read()
        repos += lib.github_urls(text)
        for m in re.finditer(r"source=(\S+)", text):
            path = os.path.expanduser(m.group(1))
            if os.path.isdir(path):
                url = subprocess.run(["git", "-C", path, "remote", "get-url", "origin"],
                                     capture_output=True, text=True).stdout.strip()
                for key in lib.github_urls(url):
                    repos.append(key)
                    local[key] = path
    return list(dict.fromkeys(repos)), local


def readme_hints(folder):
    out = []
    for f in ("README.md", "readme.md", "README"):
        p = os.path.join(folder, f)
        if os.path.exists(p):
            out += [u for u in lib.github_urls(open(p, encoding="utf-8", errors="replace").read())
                    if "skill" in u.lower()]
    return out


def try_repo(reg, key, url, unresolved, info, skills_dir, local_clone=None, report=None):
    """Match every unresolved skill against one repo. Returns the number confirmed."""
    try:
        cache, branch = lib.fetch(key, url)
    except lib.GitError as e:
        report.setdefault("unreachable", []).append("%s (%s)" % (key, str(e).splitlines()[-1][:80]))
        return 0
    hits = 0
    index = lib.skill_index(cache, branch)
    for name in list(unresolved):
        if name not in index:
            continue
        k = lib.skill_key(skills_dir, name)
        e = reg["skills"][k]
        commit, at_path, current, dirs = lib.find_match(cache, branch, name, info[name]["fingerprint"], index=index)
        if commit:
            src = reg["sources"].setdefault(key, {"type": "git", "url": url, "branch": branch})
            if local_clone:
                src["local_clone"] = local_clone
            t = lib.now_iso()
            e.clear()
            e.update(source=key, path=current or at_path, installed_path=at_path, commit=commit,
                     version=info[name]["version"], fingerprint=info[name]["fingerprint"],
                     installed_at=t, updated_at=t, pinned=None)
            if not current:
                e["removed_upstream"] = True
            unresolved.remove(name)
            hits += 1
        elif dirs and not e.get("candidate"):
            e["candidate"] = {"source": key, "url": url, "path": current or dirs[0]}
    return hits


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repos", nargs="*", help="extra candidate repos (owner/repo or URL)")
    ap.add_argument("--skills-dir", default=lib.DEFAULT_SKILLS)
    ap.add_argument("--search", action="store_true", help="search GitHub by author for unresolved skills")
    ap.add_argument("--max-groups", type=int, default=8, help="author groups to search (default 8)")
    ap.add_argument("--accept-likely", nargs="+", metavar="SKILL", help="record these likely candidates")
    ap.add_argument("--set-source", nargs=2, metavar=("SKILL", "REPO"), help="set one skill's source")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    a = ap.parse_args()

    skills_dir = os.path.abspath(os.path.expanduser(a.skills_dir))
    if not os.path.isdir(skills_dir):
        lib.die("no skills folder at %s" % skills_dir)
    lib.ensure_store()
    reg = lib.load_registry()
    report = {}

    if a.accept_likely:
        done = []
        for name in a.accept_likely:
            k = lib.skill_key(skills_dir, name)
            e = reg["skills"].get(k, {})
            c = e.get("candidate")
            if not c:
                print("skip %s: no likely candidate recorded" % name, file=sys.stderr)
                continue
            reg["sources"].setdefault(c["source"], {"type": "git", "url": c["url"]})
            folder = os.path.join(skills_dir, name)
            e.update(source=c["source"], path=c["path"], installed_path=c["path"], commit=None,
                     version=lib.read_meta(folder)["version"], fingerprint=lib.fingerprint(folder),
                     installed_at=lib.now_iso(), updated_at=lib.now_iso(), pinned=None, likely=True)
            e.pop("candidate", None)
            done.append(name)
        lib.save_registry(reg)
        print("recorded: %s" % (", ".join(done) or "none"))
        return

    # 1. inventory of installed skills
    info, unresolved = {}, []
    for name in lib.installed_skills(skills_dir):
        folder = os.path.join(skills_dir, name)
        k = lib.skill_key(skills_dir, name)
        if os.path.islink(folder):
            reg["skills"].setdefault(k, {"source": None, "note": "symlink to %s (local development copy)" % os.path.realpath(folder)})
            continue
        e = reg["skills"].setdefault(k, {"source": None})
        if e.get("source"):
            continue
        m = lib.read_meta(folder)
        # links that look like a skill's own repo; a link to the library a skill documents is not a source
        hints = m["source_urls"] + [u for u in m["urls"] if "skill" in u.lower()] + readme_hints(folder)
        info[name] = {"fingerprint": lib.fingerprint(folder), "version": m["version"], "author": m["author"],
                      "hints": list(dict.fromkeys(hints))}
        unresolved.append(name)
    gone = [k for k in reg["skills"] if "::" not in k and not os.path.lexists(os.path.join(skills_dir, k))
            and lib.skill_key(skills_dir, k) == k]
    for k in gone:
        reg["skills"].pop(k)

    if a.set_source:
        name, ref = a.set_source
        if name not in info:
            lib.die("%s is not an unresolved installed skill" % name)
        unresolved = [name]
        r = lib.parse_ref(ref)
        if not try_repo(reg, r["key"], r["url"], unresolved, info, skills_dir, report=report):
            e = reg["skills"][lib.skill_key(skills_dir, name)]
            print("no exact match in %s%s" % (r["key"], "; likely candidate recorded, confirm with --accept-likely " + name
                                                if e.get("candidate") else "; the repo has no skill folder named " + name))
        lib.save_registry(reg)
        return

    # 2. candidates in order of trust: markers, user-named, already-known sources, frontmatter/README hints
    marker_repos, local = read_markers(skills_dir)
    queue = []
    for key in marker_repos + [lib.parse_ref(r)["key"] for r in a.repos] + list(reg["sources"]):
        if key not in queue:
            queue.append(key)
    urls = {lib.parse_ref(r)["key"]: lib.parse_ref(r)["url"] for r in a.repos}
    tried = set()

    def run(key):
        if key in tried or not unresolved:
            return
        tried.add(key)
        url = urls.get(key) or reg["sources"].get(key, {}).get("url") or "https://github.com/%s.git" % key
        n = try_repo(reg, key, url, unresolved, info, skills_dir, local.get(key), report)
        report.setdefault("tried", []).append("%s: %d confirmed" % (key, n))

    for key in queue:
        run(key)
    for name in list(unresolved):
        for key in info[name]["hints"][:3]:
            run(key)

    # 3. GitHub search, one group per author
    if a.search and unresolved:
        groups = {}
        for name in unresolved:
            groups.setdefault(info[name]["author"] or "", []).append(name)
        ordered = sorted(((au, ns) for au, ns in groups.items() if au), key=lambda g: -len(g[1]))[:a.max_groups]
        try:
            for author, names in ordered:
                if not any(n in unresolved for n in names):
                    continue
                logins = re.findall(r"@([A-Za-z0-9-]+)|\(([A-Za-z0-9-]+)\)", author)
                logins = [x or y for x, y in logins]
                clean = re.sub(r"\(.*?\)|,?\s*(Inc|LLC|Ltd|GmbH)\.?", "", author).strip(" ,/")
                if not logins and clean:
                    logins = [u["login"] for u in lib.gh_search("users", clean, 2)]
                cands = []
                for login in logins[:2]:
                    cands += [r["full_name"] for r in lib.gh_search("repositories", "skills user:%s" % login)]
                if not any(n in unresolved for n in names):
                    break
                for r in lib.gh_search("repositories", "%s skills" % (clean or author)):
                    cands.append(r["full_name"])
                    cands += lib.github_urls(r.get("description") or "")
                for key in list(dict.fromkeys(cands))[:5]:
                    run(key)
        except lib.UserError as e:
            report["search_stopped"] = str(e)

    lib.save_registry(reg)

    # 4. summary
    by_src, likely, unknown = {}, [], {}
    for k, e in reg["skills"].items():
        if e.get("source"):
            by_src[e["source"]] = by_src.get(e["source"], 0) + 1
        elif e.get("candidate"):
            likely.append((k, e["candidate"]["source"], e["candidate"]["path"]))
        elif not e.get("note"):
            unknown.setdefault((info.get(k.split("::")[-1]) or {}).get("author") or "no author", []).append(k)
    notes = [(k, e["note"]) for k, e in reg["skills"].items() if e.get("note")]
    summary = {"sources": by_src, "likely": likely, "unknown": unknown, "notes": notes, **report}
    if a.json:
        print(json.dumps(summary, indent=1))
        return
    print("## Skill sources\n")
    print("| Source | Skills |\n|---|---|")
    for s, n in sorted(by_src.items(), key=lambda x: -x[1]):
        print("| %s | %d |" % (s, n))
    print("\nConfirmed by exact match: %d · Likely: %d · Unknown: %d" % (
        sum(by_src.values()), len(likely), sum(len(v) for v in unknown.values())))
    if likely:
        print("\n### Likely (repo has the skill, but no commit matches exactly: edited locally or from a fork)")
        for k, s, p in likely:
            print("- %s ← %s (%s)" % (k.split("::")[-1], s, p))
    if unknown:
        print("\n### Unknown source, by author")
        for au, ns in sorted(unknown.items(), key=lambda x: -len(x[1])):
            print("- %s: %s" % (au, ", ".join(sorted(n.split("::")[-1] for n in ns))))
    for k, n in notes:
        print("- %s: %s" % (k.split("::")[-1], n))
    for x in report.get("unreachable", []):
        print("- unreachable: %s" % x)
    if report.get("search_stopped"):
        print("- search stopped: %s" % report["search_stopped"])


if __name__ == "__main__":
    try:
        main()
    except (lib.UserError, lib.GitError) as e:
        lib.die(str(e))
