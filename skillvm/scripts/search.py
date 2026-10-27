#!/usr/bin/env python3
"""Find skills on GitHub that match a topic, ranked by stars. Read-only for your skills.

  search.py "data management"          top 10 matching skills, ranked by GitHub stars
  search.py "pdf forms" --limit 20
  search.py "pdf forms" --json
  search.py --details 3                 everything about result 3 of the last search: links, metadata,
                                        files, and its full SKILL.md, to explain it and suggest use cases

How it works: GitHub repository search (agent-skills / claude-skills topics, plus name and description
matches, plus the most-starred skill collections), then a shallow sparse clone of each candidate that
downloads only its SKILL.md files. Skills whose name or description match the topic are ranked by
their repo's stars. Results are saved so `apply.py install --from-search 1 3` can install by number.
"""
import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib  # noqa: E402

STOP = {"a", "an", "and", "the", "for", "to", "of", "in", "on", "with", "my", "me", "i", "find", "search",
        "skill", "skills", "agent", "agents", "claude", "codex", "some", "any", "that", "which", "about", "help"}
SUFFIXES = ("ations", "ation", "ments", "ment", "ings", "ing", "ies", "es", "s", "ed")


def stem(word):
    for suf in SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 4:
            return word[:-len(suf)]
    return word


def tokens(text):
    return [stem(w) for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP and len(w) > 1]


def matched(query_terms, text):
    """The query terms found in text: same stem, or a word that starts with the term (data -> database)."""
    words = set(tokens(text))
    return {t for t in query_terms if t in words or (len(t) >= 4 and any(w.startswith(t) for w in words))}


def phrase_in(terms, text):
    """True if the query terms appear next to each other, in order (\"data management\")."""
    words = tokens(text)
    hit = lambda w, t: w == t or (len(t) >= 4 and w.startswith(t))
    n = len(terms)
    return n > 1 and any(all(hit(words[i + j], terms[j]) for j in range(n)) for i in range(len(words) - n + 1))


def candidate_repos(query, collections):
    """Repos worth opening, from a few paced GitHub searches. Returns ({full_name: info}, note).
    Repos found by searching for the query itself are marked `query_hit`."""
    searches = [("%s topic:agent-skills" % query, True), ("%s topic:claude-skills" % query, True),
                ("%s skills in:name,description" % query, True)]
    searches += [("topic:agent-skills", False), ("topic:claude-skills", False)][:collections]
    repos, note = {}, None
    try:
        for q, query_hit in searches:
            for r in lib.gh_search("repositories", q, per_page=10):
                if r["full_name"] in repos:
                    repos[r["full_name"]]["query_hit"] |= query_hit
                    continue
                if r.get("archived") or r.get("fork"):
                    continue
                repos.setdefault(r["full_name"], {"stars": r.get("stargazers_count", 0),
                                                  "branch": r.get("default_branch") or "main",
                                                  "about": (r.get("description") or "").strip(),
                                                  "license": ((r.get("license") or {}).get("spdx_id") or "").replace("NOASSERTION", "custom"),
                                                  "pushed": (r.get("pushed_at") or "")[:10],
                                                  "query_hit": query_hit})
    except lib.UserError as e:
        note = str(e)
    return repos, note


def read_skills(full_name, info, workdir):
    """Shallow sparse clone with only SKILL.md files; return the skills it contains."""
    dest = os.path.join(workdir, full_name.replace("/", "__"))
    url = "https://github.com/%s.git" % full_name
    try:
        for args in (["clone", "-q", "--depth", "1", "--filter=blob:none", "--no-checkout", url, dest],
                     ["-C", dest, "sparse-checkout", "set", "--no-cone", "SKILL.md"],
                     ["-C", dest, "checkout", "-q"]):
            subprocess.run(["git", *args], capture_output=True, env=lib.GIT_ENV, timeout=90, check=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []
    files = subprocess.run(["git", "-C", dest, "ls-tree", "-r", "--name-only", "HEAD"],
                           capture_output=True, text=True, env=lib.GIT_ENV).stdout.splitlines()
    head = subprocess.run(["git", "-C", dest, "log", "-1", "--format=%h %cs"],
                          capture_output=True, text=True, env=lib.GIT_ENV).stdout.split()
    dirs = [os.path.dirname(f) or "." for f in files if f == "SKILL.md" or f.endswith("/SKILL.md")]
    out = []
    for d in lib.preferred_dirs(dirs):
        meta = lib.read_meta(os.path.join(dest, d) if d != "." else dest)
        if not meta["name"] or not meta["description"]:
            continue
        prefix = "" if d == "." else d + "/"
        code = [f for f in files if f.startswith(prefix) and (f[len(prefix):].startswith("scripts/") or f.endswith(lib.CODE_EXT))]
        n_files = len([f for f in files if f.startswith(prefix)]) if prefix else len(files)
        out.append({"repo": full_name, "stars": info["stars"], "branch": info["branch"], "path": d,
                    "query_hit": info["query_hit"],
                    "url": skill_url(full_name, info["branch"], d), "repo_about": info["about"],
                    "license": info["license"], "updated": head[1] if len(head) > 1 else info["pushed"],
                    "commit": head[0] if head else None, "files": n_files,
                    "folder": os.path.basename(d) if d != "." else full_name.split("/")[1],
                    "name": meta["name"], "description": meta["description"], "version": meta["version"],
                    "allowed_tools": meta["allowed-tools"], "scripts": len(code)})
    shutil.rmtree(dest, ignore_errors=True)
    return out


def skill_url(repo, branch, path):
    return "https://github.com/%s" % repo + ("" if path == "." else "/tree/%s/%s" % (branch, path))


def first_sentence(text, n=110):
    s = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def load_last_search():
    p = os.path.join(lib.STORE, "last-search.json")
    if not os.path.exists(p):
        lib.die("no search yet: run search.py \"<topic>\" first")
    with open(p) as f:
        return json.load(f)


def details(n):
    """Everything about one result of the last search, including its full SKILL.md."""
    last = load_last_search()
    s = next((r for r in last["results"] if r["n"] == n), None) or lib.die("no result #%d in the last search" % n)
    dest = os.path.join(lib.STORE, "tmp", "details-%d" % os.getpid())
    shutil.rmtree(dest, ignore_errors=True)
    try:
        for args in (["clone", "-q", "--depth", "1", "--filter=blob:none", "--no-checkout",
                      "https://github.com/%s.git" % s["repo"], dest],
                     ["-C", dest, "sparse-checkout", "set", "--no-cone", "/%s/" % s["path"] if s["path"] != "." else "/*"],
                     ["-C", dest, "checkout", "-q"]):
            subprocess.run(["git", *args], capture_output=True, env=lib.GIT_ENV, timeout=120, check=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        lib.die("could not fetch %s from GitHub" % s["repo"])
    folder = dest if s["path"] == "." else os.path.join(dest, s["path"])
    files = sorted(os.path.relpath(os.path.join(r, f), folder) for r, ds, fs in os.walk(folder)
                   for f in fs if ".git" not in r.split(os.sep))
    with open(os.path.join(folder, "SKILL.md"), encoding="utf-8", errors="replace") as f:
        body = f.read()
    shutil.rmtree(dest, ignore_errors=True)
    print("## %s · result %d of \"%s\"\n" % (s["folder"], n, last["query"]))
    print("| | |\n|---|---|")
    for k, v in (("Skill", s["name"]), ("Link", s["url"]), ("Repo", "https://github.com/%s" % s["repo"]),
                 ("Repo description", s["repo_about"] or "—"), ("Stars", "{:,}".format(s["stars"])),
                 ("License", s["license"] or "no license"), ("Last updated", "%s (`%s`)" % (s["updated"], s["commit"])),
                 ("Version", s["version"] or "—"), ("allowed-tools", s["allowed_tools"] or "—"),
                 ("Files", "%d (%d scripts)" % (len(files), s["scripts"])), ("Already installed", s.get("installed") or "no")):
        print("| %s | %s |" % (k, str(v).replace("|", "\\|").replace("\n", " ")))
    print("\n### Files\n" + "\n".join("- `%s`" % f for f in files[:40]) + ("\n- …and %d more" % (len(files) - 40) if len(files) > 40 else ""))
    lines = body.splitlines()
    print("\n### SKILL.md%s\n" % (" (first 150 of %d lines)" % len(lines) if len(lines) > 150 else ""))
    print("````markdown\n%s\n````" % "\n".join(lines[:150]))
    print("\nInstall it with: apply.py install --from-search %d" % n)


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--details":
        lib.setup(argparse.Namespace(skills_dir=None, agent=None))
        return details(int(sys.argv[2]))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="+", help="what the skills should be about")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--collections", type=int, default=2, choices=(0, 1, 2),
                    help="also look inside the most-starred skill collections (default 2 searches)")
    ap.add_argument("--json", action="store_true")
    lib.add_common_args(ap)
    a = ap.parse_args()
    skills_dir = lib.setup(a)
    lib.ensure_store()
    query = " ".join(a.query)
    terms = list(dict.fromkeys(tokens(query)))
    if not terms:
        lib.die("give a topic to search for, e.g. \"data management\"")

    repos, note = candidate_repos(query, a.collections)
    workdir = os.path.join(lib.STORE, "tmp", "search-%d" % os.getpid())
    os.makedirs(workdir, exist_ok=True)
    found = []
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            for skills in pool.map(lambda kv: read_skills(kv[0], kv[1], workdir), repos.items()):
                found += skills
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    # relevance first, then stars: a skill must clearly be about the topic before its stars count
    best = {}
    for s in found:
        desc, label = s["description"], s["name"] + " " + s["folder"].replace("-", " ")
        hits = matched(terms, label) | matched(terms, desc)
        # every word of the query must appear, except in repos that are about the topic as a whole
        if len(hits) < len(terms) and not (s["query_hit"] and hits):
            continue
        s["score"] = (3 * len(matched(terms, label)) + 2 * len(matched(terms, first_sentence(desc, 300)))
                      + len(hits) + (5 if phrase_in(terms, label + " " + desc) else 0) + (2 if s["query_hit"] else 0))
        key = (s["name"], desc[:200])  # the same skill copied into several repos: keep the most-starred
        if key not in best or s["stars"] > best[key]["stars"]:
            best[key] = s
    ranked = sorted(best.values(), key=lambda s: (-s["score"], -s["stars"]))
    per_repo, picked = {}, []
    for s in ranked:
        if len(picked) >= a.limit:
            break
        if per_repo.get(s["repo"], 0) >= 3:  # keep one big collection from filling the list
            continue
        per_repo[s["repo"]] = per_repo.get(s["repo"], 0) + 1
        picked.append(s)
    picked.sort(key=lambda s: (-s["stars"], -s["score"]))
    more = {r: sum(1 for s in ranked if s["repo"] == r) - n for r, n in per_repo.items()}

    reg = lib.load_registry()
    for i, s in enumerate(picked, 1):
        s["n"] = i
        e = reg["skills"].get(lib.skill_key(skills_dir, s["folder"]))
        if e and e.get("source") == s["repo"]:
            s["installed"] = "installed"
        elif os.path.lexists(os.path.join(skills_dir, s["folder"])):
            s["installed"] = "name taken"
    with open(os.path.join(lib.STORE, "last-search.json"), "w") as f:
        json.dump({"query": query, "skills_dir": skills_dir, "results": picked}, f, indent=1)

    if a.json:
        print(json.dumps({"query": query, "results": picked, "repos_checked": len(repos), "note": note}, indent=1))
        return
    print("## Skill search · \"%s\" · top %d by GitHub stars\n" % (query, len(picked)))
    if not picked:
        print("No matching skills found in %d repos. Try other words, or a broader topic." % len(repos))
    else:
        print("| # | Skill | What it does | Repo | ★ | Updated | Notes |\n|---|---|---|---|---|---|---|")
        for s in picked:
            notes = [x for x in ("v" + s["version"] if s["version"] else None,
                                 "%d script%s" % (s["scripts"], "s" if s["scripts"] != 1 else "") if s["scripts"] else None,
                                 "⚠ allowed-tools: %s" % s["allowed_tools"] if s["allowed_tools"] else None,
                                 s.get("installed")) if x]
            print("| %d | [**%s**](%s) | %s | %s | %s | %s | %s |" % (
                s["n"], s["folder"], s["url"], first_sentence(s["description"]).replace("|", "\\|"), s["repo"],
                "{:,}".format(s["stars"]), s["updated"] or "—", " · ".join(notes) or ""))
        print("\n### About each result")
        for s in picked:
            about = first_sentence(s["repo_about"], 160) if s["repo_about"] else "no description"
            print("%d. **%s**: %s" % (s["n"], s["folder"], s["url"]))
            print("   - Repo: [%s](https://github.com/%s) · ★ %s · %s · last updated %s · %s"
                  % (s["repo"], s["repo"], "{:,}".format(s["stars"]), s["license"] or "no license",
                     s["updated"] or "—", about.replace("\n", " ")))
        extra = ["%s (+%d)" % (r, m) for r, m in more.items() if m > 0]
        print("\nStars belong to the repo, not the individual skill. Checked %d repos." % len(repos)
              + (" More matches in: %s." % ", ".join(extra) if extra else ""))
        print("\nMore about one result (files, full instructions, use cases): search.py --details N")
        print("\nNEEDS DECISION: which skills to install? Install by number with: apply.py install --from-search N [N ...]")
    if note:
        print("\nNote: %s; results may be incomplete." % note)


if __name__ == "__main__":
    try:
        main()
    except (lib.UserError, lib.GitError) as e:
        lib.die(str(e))
