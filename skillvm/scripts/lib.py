"""Shared logic for skillvm. Standard library only (Python 3.8+).

Works for any agent that loads SKILL.md folders (Claude Code, Codex, ...). The skills folder defaults to the
one this skill is installed in, so a copy in ~/.codex/skills manages Codex skills and a copy in ~/.claude/skills
manages Claude skills. Each agent home gets its own version store next to its skills folder
(~/.claude/skill-versions, ~/.codex/skill-versions, ...); SKILLVM_STORE overrides it.

Layout of the version store:
  registry.json   where each skill comes from and which commit is installed
  history.jsonl   one line per change
  snapshots/      copies of skill folders taken before every change
  cache/          blobless mirror clones of source repos
  tmp/            staging area
"""
import datetime
import difflib
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.parse

HOME = os.path.expanduser("~")
CLAUDE_HOME = os.path.abspath(os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude"))
CODEX_HOME = os.path.abspath(os.path.expanduser(os.environ.get("CODEX_HOME") or "~/.codex"))
AGENT_SKILLS = {
    "claude": os.path.join(CLAUDE_HOME, "skills"),
    "codex": os.path.join(CODEX_HOME, "skills"),
    "agents": os.path.join(HOME, ".agents", "skills"),  # shared folder some agents also read
}


def _installed_skills_dir():
    """The skills folder this copy of the skill lives in, if it lives in one."""
    skill_folder = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parent = os.path.dirname(skill_folder)
    return parent if os.path.basename(parent) == "skills" else None


DEFAULT_SKILLS = os.path.abspath(os.path.expanduser(
    os.environ.get("SKILLVM_SKILLS_DIR") or _installed_skills_dir() or AGENT_SKILLS["claude"]))
# Keys in the registry are bare folder names for the store's own skills folder, "<folder>::<name>" for others.
HOME_SKILLS = DEFAULT_SKILLS
STORE = None
PREFERRED = (".claude",)  # agent folders to prefer when a repo has one copy of a skill per agent


def store_for(skills_dir):
    """Version store for a skills folder: next to it inside an agent home, else ~/.claude/skill-versions."""
    parent = os.path.dirname(os.path.abspath(skills_dir))
    homes = {CLAUDE_HOME, CODEX_HOME, os.path.join(HOME, ".agents")}
    return os.path.join(parent if parent in homes else CLAUDE_HOME, "skill-versions")


def add_common_args(ap):
    ap.add_argument("--skills-dir", help="skills folder to manage (default: the one this skill is installed in, "
                                         "currently %s)" % DEFAULT_SKILLS.replace(HOME, "~"))
    ap.add_argument("--agent", choices=sorted(AGENT_SKILLS), help="manage this agent's skills folder: "
                    + ", ".join("%s=%s" % (k, v.replace(HOME, "~")) for k, v in sorted(AGENT_SKILLS.items())))


def setup(a):
    """Pick the skills folder and version store from parsed args. Returns the skills folder."""
    global STORE, HOME_SKILLS, PREFERRED
    if a.skills_dir and a.agent:
        die("use --skills-dir or --agent, not both")
    skills_dir = os.path.abspath(os.path.expanduser(a.skills_dir or AGENT_SKILLS.get(a.agent) or DEFAULT_SKILLS))
    tail = os.path.basename(os.path.dirname(skills_dir))
    if skills_dir.startswith(CODEX_HOME + os.sep) or tail == ".codex":
        PREFERRED = (".codex", ".agents")
    elif tail == ".agents":
        PREFERRED = (".agents", ".codex")
    if os.environ.get("SKILLVM_STORE"):
        # a custom store belongs to the skills folder it is used with, so a copied or moved store keeps working
        STORE = os.path.abspath(os.path.expanduser(os.environ["SKILLVM_STORE"]))
        HOME_SKILLS = skills_dir
    else:
        STORE = store_for(skills_dir)
        if os.path.dirname(STORE) == os.path.dirname(skills_dir):
            HOME_SKILLS = skills_dir
        else:
            HOME_SKILLS = os.path.join(os.path.dirname(STORE), "skills")
    return skills_dir
FP_EXCLUDES = ".DS_Store\n__pycache__/\n*.pyc\n.pytest_cache/\n"
KEEP_SNAPSHOTS = 10
CODE_EXT = (".py", ".sh", ".bash", ".zsh", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".ps1", ".bat", ".exe")


class GitError(RuntimeError):
    pass


class UserError(RuntimeError):
    """Something the user has to decide or fix; printed without a traceback."""


# --- basics -----------------------------------------------------------------

def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def snapshot_id():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ensure_store():
    for d in ("snapshots", "cache", "tmp"):
        os.makedirs(os.path.join(STORE, d), exist_ok=True)
    open(os.path.join(STORE, "history.jsonl"), "a").close()


# Never let git wait for a username/password (private or missing repos): fail fast instead.
GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", GIT_ASKPASS="echo")


def git(*args, inp=None, check=True, binary=False):
    r = subprocess.run(["git", *args], input=inp, capture_output=True, text=not binary, env=GIT_ENV)
    if check and r.returncode:
        err = r.stderr if not binary else r.stderr.decode("utf-8", "replace")
        raise GitError("git %s: %s" % (" ".join(args[:3]), err.strip()))
    return r.stdout


def gp(path):
    """Path as used in `<rev>:<path>` expressions; the repo root is the empty string."""
    return "" if path in (".", "", None) else path


def spec(rev, path):
    """`<rev>:<path>` for a folder, or the commit's root tree when path is the repo root."""
    return "%s:%s" % (rev, path) if gp(path) else "%s^{tree}" % rev


def short(commit):
    return commit[:7] if commit else None


# --- fingerprints and frontmatter -------------------------------------------

def fingerprint(folder):
    """Git tree id of a folder's contents: comparable with `git rev-parse <commit>:<path>`."""
    idx = os.path.join(STORE, "tmp", "fp-%d.git" % os.getpid())
    shutil.rmtree(idx, ignore_errors=True)
    git("init", "-q", "--bare", idx)
    with open(os.path.join(idx, "info", "exclude"), "w") as f:
        f.write(FP_EXCLUDES)
    git("--git-dir=" + idx, "--work-tree=" + folder, "add", "-A")
    tree = git("--git-dir=" + idx, "write-tree").strip()
    shutil.rmtree(idx, ignore_errors=True)
    return tree


def parse_frontmatter(text):
    meta = {"name": None, "description": None, "version": None, "allowed-tools": None, "hooks": False,
            "author": None, "urls": [], "source_urls": []}
    if not text.startswith("---"):
        return meta
    parts = text.split("\n---", 1)
    fm = parts[0][3:] if len(parts) == 2 else ""
    for key in ("name", "description", "allowed-tools"):
        m = re.search(r"^%s:\s*(.*)$" % re.escape(key), fm, re.M)
        if m:
            meta[key] = m.group(1).strip().strip("\"'") or None
    m = re.search(r"^\s*version:\s*[\"']?([^\"'\n]+?)[\"']?\s*$", fm, re.M)
    meta["version"] = m.group(1) if m else None
    meta["hooks"] = bool(re.search(r"^hooks:", fm, re.M))
    m = re.search(r"^\s*(?:skill-author|author):\s*[\"']?([^\"'\n]+?)[\"']?\s*$", fm, re.M)
    if m:
        meta["author"] = m.group(1)
    meta["urls"] = github_urls(fm)
    meta["source_urls"] = github_urls("\n".join(re.findall(r"^\s*(?:source|repository|repo|homepage):\s*(.*)$", fm, re.M)))
    return meta


def read_meta(folder):
    p = os.path.join(folder, "SKILL.md")
    if not os.path.exists(p):
        return parse_frontmatter("")
    with open(p, encoding="utf-8", errors="replace") as f:
        return parse_frontmatter(f.read())


def meta_at(cache, commit, path):
    text = git("-C", cache, "show", "%s:%s" % (commit, (gp(path) + "/SKILL.md").lstrip("/")), check=False)
    return parse_frontmatter(text)


def github_urls(text):
    out = []
    for o, r in re.findall(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", text):
        r = re.sub(r"\.git$", "", r).rstrip(".")
        key = "%s/%s" % (o, r)
        if key not in out:
            out.append(key)
    return out


# --- registry ---------------------------------------------------------------

def registry_path():
    return os.path.join(STORE, "registry.json")


def load_registry():
    p = registry_path()
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {"format": 1, "sources": {}, "skills": {}}


def save_registry(reg):
    p = registry_path()
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(reg, f, indent=2, sort_keys=True)
    os.replace(tmp, p)


def skill_key(skills_dir, name):
    if os.path.abspath(skills_dir) == os.path.abspath(HOME_SKILLS):
        return name
    return "%s::%s" % (os.path.abspath(skills_dir), name)


def snap_dirname(key):
    return re.sub(r"[/:\\]+", "_", key).strip("_")


def append_history(rec):
    rec = dict(at=now_iso(), **rec)
    with open(os.path.join(STORE, "history.jsonl"), "a") as f:
        f.write(json.dumps(rec) + "\n")


def read_history():
    p = os.path.join(STORE, "history.jsonl")
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return [json.loads(l) for l in f if l.strip()]


def installed_skills(skills_dir):
    """Folder names in skills_dir that look like skills (hidden entries skipped)."""
    out = []
    for name in sorted(os.listdir(skills_dir)):
        p = os.path.join(skills_dir, name)
        if name.startswith(".") or not os.path.isdir(p):
            continue
        out.append(name)
    return out


def resolve_targets(names, skills_dir):
    """Expand names and globs to installed folder names. Matches folder names, then `name:` fields."""
    have = installed_skills(skills_dir)
    if not names or names == ["all"]:
        return have, []
    out, missing = [], []
    by_field = {}
    for h in have:
        n = read_meta(os.path.join(skills_dir, h))["name"]
        if n:
            by_field.setdefault(n, h)
    for n in names:
        hits = [h for h in have if fnmatch.fnmatch(h, n)] or ([by_field[n]] if n in by_field else [])
        if hits:
            out.extend(h for h in hits if h not in out)
        else:
            close = difflib.get_close_matches(n, have, n=3)
            missing.append("%s (did you mean: %s?)" % (n, ", ".join(close)) if close else n)
    return out, missing


# --- sources ----------------------------------------------------------------

def parse_ref(s):
    """owner/repo[@ref], GitHub URLs (incl. /tree/<ref>/<path> and /blob/.../SKILL.md), or any git URL."""
    s = s.strip()
    ref = None
    m = re.match(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:@(.+))?$", s)
    if m and not s.startswith(("http", "git@")):
        o, r, ref = m.groups()
        return {"key": "%s/%s" % (o, r), "url": "https://github.com/%s/%s.git" % (o, r), "ref": ref, "path": None}
    m = re.match(r"^(?:https?://|git@)github\.com[/:]([^/]+)/([^/@#?]+?)(?:\.git)?(?:/(tree|blob)/([^/]+)(?:/(.*))?)?/?(?:@(.+))?$", s)
    if m:
        o, r, kind, ref1, path, ref2 = m.groups()
        ref = ref1 or ref2
        if path:
            path = path.rstrip("/")
            if path.endswith("SKILL.md"):
                path = os.path.dirname(path) or "."
        return {"key": "%s/%s" % (o, r), "url": "https://github.com/%s/%s.git" % (o, r), "ref": ref, "path": path}
    url, _, ref = s.partition("@") if s.startswith("http") and "@" in s.split("/")[-1] else (s, "", None)
    key = re.sub(r"^(?:https?://|git@)", "", url).replace(":", "/")
    key = re.sub(r"\.git$", "", key)
    return {"key": key, "url": url, "ref": ref or None, "path": None}


_fetched = {}


def cache_dir(key):
    return os.path.join(STORE, "cache", key.replace("/", "__"))


def fetch(key, url, no_fetch=False):
    """Blobless mirror clone in the cache; fetched at most once per process. Returns (cache, default_branch)."""
    if key in _fetched:
        return _fetched[key]
    cache = cache_dir(key)
    if os.path.exists(os.path.join(cache, "HEAD")):
        if not no_fetch:
            git("-C", cache, "fetch", "--quiet", "--prune", "origin")
    else:
        git("clone", "--quiet", "--mirror", "--filter=blob:none", url, cache)
    branch = git("-C", cache, "symbolic-ref", "--short", "HEAD").strip()
    _fetched[key] = (cache, branch)
    return _fetched[key]


def fetch_source(reg, key, no_fetch=False):
    src = reg["sources"][key]
    cache, default = fetch(key, src["url"], no_fetch)
    return cache, src.get("branch") or default


def skill_dirs(cache, rev):
    out = []
    for f in git("-C", cache, "ls-tree", "-r", "--name-only", rev).splitlines():
        if f == "SKILL.md":
            out.append(".")
        elif f.endswith("/SKILL.md"):
            out.append(os.path.dirname(f))
    return out


def path_rank(path):
    """When a repo carries one skill for several agents, prefer the managed agent's copy."""
    p = "/" + path
    for i, agent in enumerate(PREFERRED):
        if "/%s/" % agent in p:
            return (i, len(path))
    n = len(PREFERRED)
    return (n if p.startswith("/skills/") else n + 1 if not p.startswith("/.") else n + 2, len(path))


def preferred_dirs(dirs):
    """One folder per skill name, choosing the best-ranked copy."""
    best = {}
    for d in dirs:
        b = os.path.basename(d) if d != "." else "."
        if b not in best or path_rank(d) < path_rank(best[b]):
            best[b] = d
    return sorted(best.values(), key=lambda d: os.path.basename(d))


def tree_at(cache, rev, path):
    return git("-C", cache, "rev-parse", "-q", "--verify", spec(rev, path), check=False).strip() or None


def commit_date(cache, commit):
    return git("-C", cache, "log", "-1", "--format=%cs", commit).strip() if commit else None


def last_commit(cache, rev, path):
    args = ["-C", cache, "log", "-1", "--format=%H", rev]
    if gp(path):
        args += ["--", path]
    return git(*args).strip() or None


def skill_index(cache, branch):
    """{folder name: [every path that ever held <name>/SKILL.md]} in one pass over the repo's history."""
    out = git("-C", cache, "log", "--format=", "--name-only", branch, "--",
              ":(glob)**/SKILL.md", ":(glob)SKILL.md", check=False)
    index = {}
    for d in dict.fromkeys([os.path.dirname(l) for l in out.splitlines() if l.strip()] + skill_dirs(cache, branch)):
        index.setdefault(os.path.basename(d) if d not in ("", ".") else ".", []).append(d or ".")
    return index


def find_match(cache, branch, name, fp, extra_dirs=(), index=None):
    """Find the upstream commit whose tree for this skill equals `fp`.
    Looks at every folder that ever held <name>/SKILL.md (so moved skills are found), plus extra_dirs.
    Returns (commit, path_at_that_commit, current_path_or_None, known_dirs)."""
    if index is None:
        out = git("-C", cache, "log", "--format=", "--name-only", branch, "--",
                  ":(glob)**/%s/SKILL.md" % name, ":(glob)%s/SKILL.md" % name, check=False)
        found = [os.path.dirname(l) for l in out.splitlines() if l.strip()]
    else:
        found = index.get(name, [])
    dirs = list(dict.fromkeys([d for d in extra_dirs if d] + found))
    pairs = []
    for d in dirs:
        args = ["-C", cache, "log", "--format=%H", branch]
        if gp(d):
            args += ["--", d]
        pairs += [(c, d) for c in git(*args).split()]
    match = None
    if pairs:
        checks = git("-C", cache, "cat-file", "--batch-check",
                     inp="".join(spec(c, d) + "\n" for c, d in pairs)).splitlines()
        match = next((pc for pc, chk in zip(pairs, checks) if chk.split()[0] == fp), None)
    current_dirs = [d for d in dirs if tree_at(cache, branch, d)]
    current = None
    if match and match[1] in current_dirs:
        current = match[1]
    elif current_dirs:
        current = min(current_dirs, key=path_rank)
    return (match[0] if match else None), (match[1] if match else None), current, dirs


def on_branch(cache, commit, branch):
    """True if `commit` is reachable from `branch`. A force-pushed-away commit can still sit in the cache."""
    r = subprocess.run(["git", "-C", cache, "merge-base", "--is-ancestor", commit, branch],
                       capture_output=True, env=GIT_ENV)
    return r.returncode == 0


def refresh_commit(cache, branch, name, e):
    """Keep e["commit"] valid after upstream rewrote its history (force-push, rebase, squash).

    If the recorded commit is no longer on the branch, find the upstream commit whose tree equals the
    recorded fingerprint and record that instead. Changes `e` in place; the caller decides whether to save.
    Returns "ok", "rematched" (a new commit was found), or "lost" (none matches: treated as locally
    modified from then on, so it is never overwritten without asking)."""
    if not e.get("commit") or on_branch(cache, e["commit"], branch):
        return "ok"
    commit, at_path, current, _ = find_match(cache, branch, name, e["fingerprint"],
                                             extra_dirs=[e.get("installed_path"), e.get("path")])
    if commit:
        e["commit"], e["installed_path"] = commit, at_path
        if current:
            e["path"] = current
        return "rematched"
    e["commit"], e["likely"] = None, True
    return "lost"


def find_version(cache, branch, path, want):
    args = ["-C", cache, "log", "--format=%H", branch]
    if gp(path):
        args += ["--", path]
    for c in git(*args).split():
        if meta_at(cache, c, path)["version"] == want:
            return c
    return None


# --- GitHub search (unauthenticated: ~10 requests/minute) --------------------

_last_call = [0.0]


def gh_search(kind, q, per_page=3):
    """GitHub search via curl (system certificates; Python's own TLS often lacks them on macOS)."""
    wait = 6.5 - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()
    url = "https://api.github.com/search/%s?q=%s&per_page=%d%s" % (
        kind, urllib.parse.quote(q), per_page, "&sort=stars" if kind == "repositories" else "")
    r = subprocess.run(["curl", "-sS", "--max-time", "20", "-H", "Accept: application/vnd.github+json",
                        "-A", "skillvm", "-w", "\n%{http_code}", url], capture_output=True, text=True)
    body, _, code = r.stdout.rpartition("\n")
    if code in ("403", "429"):
        raise UserError("GitHub search rate limit reached; try again in a minute")
    if r.returncode or code != "200":
        return []
    try:
        return json.loads(body).get("items", [])
    except ValueError:
        return []


# --- snapshots and installing ------------------------------------------------

def snapshots_root(key):
    return os.path.join(STORE, "snapshots", snap_dirname(key))


def list_snapshots(key):
    root = snapshots_root(key)
    if not os.path.isdir(root):
        return []
    out = []
    for sid in sorted(os.listdir(root), reverse=True):
        p = os.path.join(root, sid, "meta.json")
        if os.path.exists(p):
            with open(p) as f:
                out.append(json.load(f))
    return out


def take_snapshot(key, folder, reason, entry):
    """Copy the folder into the snapshot store and verify the copy. Reuses the newest snapshot if identical."""
    fp = fingerprint(folder)
    snaps = list_snapshots(key)
    if snaps and snaps[0]["fingerprint"] == fp:
        return snaps[0]["id"]
    sid = snapshot_id()
    root = snapshots_root(key)
    n = 2
    while os.path.exists(os.path.join(root, sid)):
        sid = "%s-%d" % (snapshot_id(), n)
        n += 1
    dest = os.path.join(root, sid)
    os.makedirs(dest)
    shutil.copytree(folder, os.path.join(dest, "files"), symlinks=True)
    if fingerprint(os.path.join(dest, "files")) != fp:
        shutil.rmtree(dest, ignore_errors=True)
        raise UserError("snapshot of %s did not verify; nothing was changed" % key)
    meta = {
        "skill": key, "id": sid, "reason": reason, "version": read_meta(folder)["version"],
        "commit": entry.get("commit"), "source": entry.get("source"), "fingerprint": fp,
        "locally_modified": bool(entry.get("fingerprint") and entry.get("fingerprint") != fp),
        "created_at": now_iso(),
    }
    with open(os.path.join(dest, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    return sid


def _extract(proc_stdout, dest):
    with tarfile.open(fileobj=proc_stdout, mode="r|") as tf:
        if hasattr(tarfile, "data_filter"):
            tf.extractall(dest, filter="data")
        else:
            tf.extractall(dest)


def stage_upstream(cache, commit, path, name):
    """Extract <commit>:<path> into tmp/new-<name> and return that folder."""
    tmp = os.path.join(STORE, "tmp")
    stage = os.path.join(tmp, "stage-%s-%d" % (snap_dirname(name), os.getpid()))
    new = os.path.join(tmp, "new-%s-%d" % (snap_dirname(name), os.getpid()))
    shutil.rmtree(stage, ignore_errors=True)
    shutil.rmtree(new, ignore_errors=True)
    os.makedirs(stage)
    args = ["git", "-C", cache, "archive", "--format=tar", commit] + ([path] if gp(path) else [])
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=GIT_ENV)
    try:
        _extract(proc.stdout, stage)
    finally:
        proc.stdout.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        if proc.wait():
            shutil.rmtree(stage, ignore_errors=True)
            raise GitError("git archive %s:%s failed: %s" % (short(commit), path, err.strip()))
    if gp(path):
        os.rename(os.path.join(stage, path), new)
        shutil.rmtree(stage, ignore_errors=True)
    else:
        os.rename(stage, new)
    return new


def stage_snapshot(key, sid, name):
    new = os.path.join(STORE, "tmp", "new-%s-%d" % (snap_dirname(name), os.getpid()))
    shutil.rmtree(new, ignore_errors=True)
    shutil.copytree(os.path.join(snapshots_root(key), sid, "files"), new, symlinks=True)
    return new


def validate(folder, expected_fp):
    meta = read_meta(folder)
    if not os.path.exists(os.path.join(folder, "SKILL.md")):
        return "SKILL.md missing"
    if not meta["name"] or not meta["description"]:
        return "SKILL.md frontmatter lacks name or description"
    if expected_fp and fingerprint(folder) != expected_fp:
        return "installed files do not match the intended version"
    return None


def install_folder(staged, dest, expected_fp):
    """Swap `staged` into `dest`, validate, and roll back to the previous folder on failure.
    Returns None on success or an error message (with the previous folder restored)."""
    old = os.path.join(STORE, "tmp", "old-%s-%d" % (snap_dirname(os.path.basename(dest)), os.getpid()))
    shutil.rmtree(old, ignore_errors=True)
    had_old = os.path.lexists(dest)
    if had_old:
        os.rename(dest, old)
    os.rename(staged, dest)
    err = validate(dest, expected_fp)
    if err:
        shutil.rmtree(dest, ignore_errors=True)
        if had_old:
            os.rename(old, dest)
        return err
    shutil.rmtree(old, ignore_errors=True)
    return None


def prune_snapshots(key, entry):
    snaps = list_snapshots(key)
    keep = {s["id"] for s in snaps[:KEEP_SNAPSHOTS]}
    pre = next((s["id"] for s in snaps if s["reason"] == "pre-update"), None)
    if pre:
        keep.add(pre)
    if entry.get("pinned"):
        keep.add(entry["pinned"])
    removed = 0
    for s in snaps:
        if s["id"] not in keep:
            shutil.rmtree(os.path.join(snapshots_root(key), s["id"]), ignore_errors=True)
            removed += 1
    return removed


# --- change summaries and the standard report --------------------------------

def is_test(path):
    b = os.path.basename(path)
    return "/tests/" in "/" + path or path.startswith("tests/") or b.startswith("test_") or b.endswith(("_test.py", ".test.js", ".test.ts"))


def change_summary(cache, old, oldp, new, newp, branch=None):
    """Commits, file counts, line totals and review flags between two versions of a skill.
    `old` may be None (new install)."""
    new_spec = spec(new, newp)
    if old:
        log_args = ["-C", cache, "log", "--format=%h %s", "%s..%s" % (old, new)]
        if gp(newp):
            log_args += ["--", newp]
        commits = git(*log_args).splitlines()
        old_spec = spec(old, oldp)
        numstat = git("-C", cache, "diff", "--numstat", old_spec, new_spec).splitlines()
        raw = git("-C", cache, "diff", "--raw", old_spec, new_spec).splitlines()
        changes = []
        for l in raw:
            info, f = l.split("\t", 1)
            parts = info.split()
            changes.append((parts[4][0], parts[1], f.split("\t")[-1]))  # (status, new mode, path)
    else:
        commits = []
        numstat = []
        changes = []
        for l in git("-C", cache, "ls-tree", "-r", new_spec).splitlines():
            info, f = l.split("\t", 1)
            changes.append(("A", info.split()[0], f))
    add = sum(int(l.split()[0]) for l in numstat if l.split()[0].isdigit())
    rem = sum(int(l.split()[1]) for l in numstat if l.split()[1].isdigit())
    flags, tests = [], 0
    for st, mode, f in changes:
        code = f.startswith("scripts/") or "/scripts/" in f or f.endswith(CODE_EXT) or mode == "100755"
        if "hooks" in f.split("/") or os.path.basename(f) in ("hooks.json",):
            flags.append("hook file `%s`" % f)
        elif code and is_test(f):
            tests += st != "D"
        elif code:
            flags.append("%s `%s`" % ({"A": "adds", "D": "removes"}.get(st, "changes"), f))
    m_old = meta_at(cache, old, oldp) if old else parse_frontmatter("")
    m_new = meta_at(cache, new, newp)
    if old and m_old["allowed-tools"] != m_new["allowed-tools"]:
        flags.append("allowed-tools `%s` → `%s`" % (m_old["allowed-tools"] or "—", m_new["allowed-tools"] or "—"))
    elif not old and m_new["allowed-tools"]:
        flags.append("allowed-tools `%s`" % m_new["allowed-tools"])
    if m_new["hooks"] and not m_old["hooks"]:
        flags.append("adds hooks in SKILL.md frontmatter")
    return {"commits": commits, "files": len(numstat) if old else len(changes), "add": add, "rem": rem,
            "flags": flags, "tests": tests}


def point(version, commit, date):
    return "%s · %s · %s" % (version or "—", "`%s`" % short(commit) if commit else "—", date or "—")


def detail_lines(s, max_commits=5, max_flags=6):
    lines = []
    for c in s["commits"][:max_commits]:
        h, _, subj = c.partition(" ")
        lines.append("`%s` %s" % (h, subj))
    if len(s["commits"]) > max_commits:
        lines.append("…and %d more" % (len(s["commits"]) - max_commits))
    flags = s["flags"][:max_flags]
    extra = len(s["flags"]) - len(flags)
    if flags or s["tests"]:
        txt = ", ".join(f for f in flags)
        if extra > 0:
            txt += ", …and %d more" % extra
        if s["tests"]:
            txt += "%s(+%d test file%s)" % (" " if txt else "", s["tests"], "s" if s["tests"] != 1 else "")
        lines.append("⚠ review: " + txt)
    return lines


def headline(s, extra=None):
    bits = []
    if s is not None:
        if s["commits"]:
            bits.append("%d commit%s" % (len(s["commits"]), "s" if len(s["commits"]) != 1 else ""))
        bits.append("%d file%s (+%d −%d)" % (s["files"], "s" if s["files"] != 1 else "", s["add"], s["rem"]))
    if extra:
        bits.insert(0, extra)
    return " · ".join(bits)


def render_report(kind, mode, rows, not_changed, footer=None):
    """kind: 'changes' or 'available'. rows: dicts with name, action, frm, to, src, head, details."""
    title = "Skill changes" if kind == "changes" else "Available updates"
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    out = ["## %s · %s · %s" % (title, stamp, mode), ""]
    if rows:
        out += ["| # | Skill | Action | From | To | Source |", "|---|---|---|---|---|---|"]
        for i, r in enumerate(rows, 1):
            out.append("| %d | %s | %s | %s | %s | %s |" % (i, r["name"], r["action"], r["frm"], r["to"], r["src"]))
        out += ["", "### What changed"]
        for i, r in enumerate(rows, 1):
            out.append("%d. **%s**%s" % (i, r["name"], ": " + r["head"] if r.get("head") else ""))
            out += ["   - " + d for d in r.get("details", [])]
    else:
        out.append("No changes." if kind == "changes" else "Everything checked is up to date.")
    out += ["", "### Not changed"]
    for label, value in not_changed:
        if isinstance(value, (list, tuple)):
            value = " · ".join(value) if value else "none"
        out.append("- %s: %s" % (label, value))
    out.append("")
    if footer is None and kind == "changes" and not rows:
        footer = ""
    if footer is None:
        footer = ("Undo any row by asking to roll that skill back. Changes take effect in a new session."
                  if kind == "changes" else "Nothing applied. Ask to update specific skills, or all of them.")
    if footer:
        out.append(footer)
    return "\n".join(out).rstrip() + "\n"


def die(msg, code=1):
    print("error: " + msg, file=sys.stderr)
    sys.exit(code)
