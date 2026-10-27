# Resolving skill sources

> `scripts/bootstrap.py` and `scripts/lib.py` implement everything here. Read this to understand or debug them, or to work by hand when the scripts can't run.

A skill folder doesn't say where it came from, so the updater keeps that in a registry. This file covers the registry format, how to fill it in on first run, and how to fetch each kind of source.

Variables used below:

```bash
SKILLS="$HOME/.claude/skills"           # or ~/.codex/skills, ~/.agents/skills: the folder being managed
STORE="$(dirname "$SKILLS")/skill-versions"
```

## Registry format

`$STORE/registry.json`:

```json
{
  "format": 1,
  "sources": {
    "acme-labs/agent-skills": {
      "type": "git",
      "url": "https://github.com/acme-labs/agent-skills.git",
      "branch": "main",
      "local_clone": "/Users/me/code/agent-skills"
    },
    "my-drafts": {
      "type": "local",
      "path": "/Users/me/skill-drafts"
    }
  },
  "skills": {
    "chart-maker": {
      "source": "acme-labs/agent-skills",
      "path": "skills/chart-maker",
      "commit": "3f9c2a1e5b7d8c904a6e1f2b3c4d5e6f7a8b9c0d",
      "version": "2.1.1",
      "fingerprint": "9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a2f1e0d",
      "installed_at": "2026-10-19T07:15:57Z",
      "updated_at": "2026-10-19T07:15:57Z",
      "pinned": null
    },
    "my-notes": { "source": null }
  }
}
```

- `source` keys are `owner/repo` for GitHub, or any short id the user chooses for other sources.
- `path` is the skill folder's path inside the source.
- `commit` is the upstream commit the installed copy matches, or `null` when it's not known.
- `version` is copied from `metadata.version` (or a top-level `version:`) in `SKILL.md`, when there is one. It's for display only; commits are what count.
- `fingerprint` is the git tree id of the installed folder at install time (see versions.md, "Fingerprint"). If the current fingerprint differs, the skill has been edited locally.
- `pinned` is `null`, a commit, or a snapshot id.
- `"source": null` means unknown. Report the skill but don't touch it.
- `installed_path` is the skill's path at the installed commit. It differs from `path` only when the skill has since moved inside the repo.
- `likely: true` marks a source the user accepted without an exact match.
- Personal skills are keyed by folder name. Skills in any other skills folder are keyed as `<absolute skills folder>::<folder name>`, so they don't collide with personal skills of the same name.

Edit the registry with a small `python3 -c` or `jq` call so the JSON stays valid. Always write to a temp file and then `mv` it into place.

## Source types

| type | Where it comes from | Update | Older versions |
|---|---|---|---|
| `git` | A git remote (GitHub, GitLab, etc.) | yes | yes (commits and tags) |
| `local` | A folder on disk, such as the user's own drafts | yes (copy) | local snapshots only |
| `plugin` | A skill bundled in an agent plugin (Claude Code, Codex) | report only | report only |
| `null` | Unknown | no | local snapshots only |

### `git` sources

Use a private cache clone. Never fetch into, pull into, or check out in the user's own clone (`local_clone`), because that could disturb their working tree.

```bash
CACHE="$STORE/cache/${OWNER}__${REPO}"
if [ -f "$CACHE/HEAD" ]; then
  git -C "$CACHE" fetch --quiet --prune origin
else
  git clone --quiet --bare --filter=blob:none "$URL" "$CACHE"
  git -C "$CACHE" config --replace-all remote.origin.fetch '+refs/heads/*:refs/heads/*'
  git -C "$CACHE" config --add remote.origin.fetch '+refs/tags/*:refs/tags/*'
fi
BRANCH=${BRANCH:-$(git -C "$CACHE" symbolic-ref --short HEAD)}
LATEST=$(git -C "$CACHE" rev-parse "$BRANCH")
```

Latest commit that touched the skill:

```bash
git -C "$CACHE" log -1 --format=%H "$BRANCH" -- "$SKILL_PATH"
```

Commits between installed and latest, for this skill only:

```bash
git -C "$CACHE" log --oneline "$INSTALLED..$BRANCH" -- "$SKILL_PATH"
```

`--bare` means there's no work tree, and the two refspecs make `fetch` keep every branch and tag current. (Not `--mirror`: on GitHub that also fetches one ref per pull request, which can be hundreds. Caches made by older versions as mirrors are converted on their next fetch.) `--filter=blob:none` downloads only the commit and folder structure. Fingerprint matching needs only the structure, so even a repo with 150+ skills clones in a second or two, at a few MB.

File contents are fetched when needed, and always in batches. Git fetches a missing blob in its own network round trip, and `git archive` fetches every blob in the repo, so the scripts first list the blobs they need from the trees (`git ls-tree`, or `git cat-file --batch` on tree objects), then fetch the missing ones in one request:

```bash
git -C "$CACHE" -c fetch.negotiationAlgorithm=noop fetch -q origin --no-tags --no-write-fetch-head \
    --recurse-submodules=no --filter=blob:none <blob-id> ...
```

and read them with `git cat-file --batch`. With git 2.44+, `GIT_NO_LAZY_FETCH=1 git cat-file --batch-check` tells which blobs are missing without fetching them.

**Upstream history rewrites.** If `$INSTALLED` is no longer reachable from `$BRANCH` (after a force-push, rebase, or squash upstream), re-match it: find the upstream commit whose tree equals the recorded fingerprint (see "Finding the installed commit when it's unknown") and record that commit instead. Check for reachability with `git merge-base --is-ancestor "$INSTALLED" "$BRANCH"`, not existence, because a force-pushed-away commit can stay in the cache for weeks. If no commit matches, the installed version is gone from upstream: record `commit: null` and treat the skill as locally modified, so it's only replaced when the user agrees. `check.py` reports re-matches without saving them; the next `apply.py` run saves them and logs a `rematch` line in the history.

Fetch each repo once per run, even when many skills share it.

### Parsing a GitHub reference

| Input | owner/repo | ref | path |
|---|---|---|---|
| `owner/repo` | owner/repo | default branch | (search the repo) |
| `owner/repo@v2.0` | owner/repo | `v2.0` | (search the repo) |
| `https://github.com/owner/repo` or `.git` | owner/repo | default branch | (search the repo) |
| `https://github.com/owner/repo/tree/main/skills/foo` | owner/repo | `main` | `skills/foo` |
| `https://github.com/owner/repo/blob/main/skills/foo/SKILL.md` | owner/repo | `main` | `skills/foo` |

Clone URL: `https://github.com/<owner>/<repo>.git`. If the user gives a non-GitHub git URL (GitLab, Bitbucket, a self-hosted server), use it as is, with the host and path as the source key.

A `ref` the user names explicitly is recorded as the entry's `branch` when it's a branch. When it's a tag or commit, it's recorded as `pinned`, after telling the user that's what happens.

### `local` sources

The source folder *is* the latest version. Compare its fingerprint with the installed one. There's no upstream history, so history mode shows local snapshots only. If the folder is a git work tree, read its history with `git -C "$path" log -- "$SKILL_PATH"` and treat it like a `git` source, without modifying that work tree.

### `plugin` sources

Skills inside an agent's plugin folders (such as `~/.claude/plugins/`) and Codex's built-in `~/.codex/skills/.system/` belong to that agent. Don't write to those folders. Report the skill, and tell the user to update it through that agent's own plugin or skill manager.

## Finding the skill's path inside a repo

1. If the registry already has `path`, use it, but confirm it still exists at `$BRANCH` (`git -C "$CACHE" rev-parse --verify -q "$BRANCH:$SKILL_PATH"`). Skills sometimes move.
2. Otherwise, list folders that contain a `SKILL.md`:
   ```bash
   git -C "$CACHE" ls-tree -r --name-only "$BRANCH" | grep '/SKILL.md$\|^SKILL.md$' | sed 's#/\{0,1\}SKILL.md$##'
   ```
   Prefer a folder whose last path segment equals the installed folder name. When a repo keeps copies of a skill for several agents (`.agents/skills/x`, `.claude/skills/x`, `skills/x`, …), prefer the copy for the agent being managed (`.claude/` for Claude Code; `.codex/`, then `.agents/` for Codex), then `skills/`, then any other non-hidden folder, then other hidden ones. If none matches, read each candidate's `name:` field (`git -C "$CACHE" show "$BRANCH:$p/SKILL.md" | head -20`) and match on that.
3. If there are several matches or none, ask the user. Don't guess.
4. If the skill used to exist but is gone at `$BRANCH`, report it as `removed upstream` and leave the installed copy alone.

## Finding the installed commit when it's unknown

Compare the installed folder's fingerprint with the tree id of the skill's path at each upstream commit that touched it, newest first:

```bash
FP=<fingerprint of installed folder>   # versions.md, "Fingerprint"
for c in $(git -C "$CACHE" log --format=%H "$BRANCH" -- "$SKILL_PATH"); do
  [ "$(git -C "$CACHE" rev-parse -q "$c:$SKILL_PATH" 2>/dev/null)" = "$FP" ] && { echo "$c"; break; }
done
```

- If there's a match, that commit is what's installed. Record it with this fingerprint.
- If nothing matches, the installed copy either has local edits or came from somewhere else. Record `commit: null`, treat the skill as locally modified, and let the update flow's local-edits guard handle it.

## Bootstrapping the registry

Run this once, when `registry.json` doesn't exist.

1. **Create the store:** `mkdir -p "$STORE"/{snapshots,cache,tmp}` and `touch "$STORE/history.jsonl"`.
2. **Read install markers.** Look for hidden files in `$SKILLS` that look like install records (`ls -a "$SKILLS" | grep '^\..*install'`). Read each one in full. They're free-form, so pull out:
   - the source: a repo URL, or a local path (if it's a git clone, get its URL with `git -C <path> remote get-url origin` and record the path as `local_clone`)
   - the commit, often abbreviated (expand it with `git -C "$CACHE" rev-parse <short>` after fetching)
   - the list of skill names it installed.

   Two formats you're likely to see:
   ```
   # Managed by agent-skills scripts/install.sh
   # source=/Users/me/code/agent-skills
   # commit=3f9c2a1e...
   chart-maker
   pdf-toolkit
   ```
   ```
   writing-skills: installed 2026-10-23 from https://github.com/jdoe/writing-skills @ 9c1e4b2
   blog-post newsletter ...
   ```
3. **Check for git work trees.** Any skill folder that's a git work tree itself (`git -C "$SKILLS/<name>" rev-parse --show-toplevel` equals that folder) is a `git` source with `path: "."`.
4. **Check frontmatter hints.** A repo URL in `metadata.source`, `metadata.repository`, `homepage`, or anywhere in the frontmatter or a README in the skill folder is a candidate source.
5. **Confirm candidates by fingerprint.** For each candidate repo, fetch it, find the skill's path, and look for a commit whose tree matches the installed fingerprint (sections above).
   - **Match**: the source is *confirmed*. Record the source, path, commit, version, and fingerprint without asking.
   - **The repo has the skill, but no commit matches**: the source is *likely*. The user probably edited the skill, or it came from a fork. Keep it as a candidate to confirm with the user in step 8.

   Once a repo is fetched, try **every** still-unresolved skill against it, not only the one that led to it. Skill collections are usually installed together, so one repo often resolves dozens of skills.
6. **Search GitHub for the rest.** Group the unresolved skills by `metadata.skill-author` or `metadata.author` (or "no author"). For each group, try these in order, and stop as soon as one gives a fingerprint match (step 5):
   ```bash
   q() { python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1]))' "$1"; }
   API=https://api.github.com/search
   # a. Find the author's GitHub account (users and orgs), e.g. "Acme Labs Inc." -> acme-labs
   curl -s "$API/users?q=$(q "$AUTHOR")&per_page=3" | python3 -c 'import json,sys;[print(u["login"],u["type"]) for u in json.load(sys.stdin).get("items",[])]'
   # b. That account's skill repos
   curl -s "$API/repositories?q=$(q "skills user:$LOGIN")&sort=stars&per_page=3" | python3 -c 'import json,sys;[print(r["full_name"],r["stargazers_count"]) for r in json.load(sys.stdin).get("items",[])]'
   # c. Free-text search on the author name or the group's most distinctive skill names, plus "skills"
   curl -s "$API/repositories?q=$(q "$TERMS")&sort=stars&per_page=5" | python3 -c 'import json,sys;[print(r["full_name"],r["stargazers_count"],(r["description"] or "")[:100]) for r in json.load(sys.stdin).get("items",[])]'
   ```
   Run the top 3 results of each search through step 5. Results from (c) are often forks or repackagings. When a result's description links to another GitHub repo ("packaged from https://github.com/..."), add that repo as a candidate too, since it's usually the original.

   Use `curl`, not Python's `urllib`: on macOS, python.org builds of Python often have no CA certificates, so HTTPS requests from Python fail with `CERTIFICATE_VERIFY_FAILED`.

   A search result counts as *confirmed* only after a fingerprint match, never on the name or star count alone. Unauthenticated search allows about 10 requests a minute, so search per group, not per skill. If `gh` is installed and signed in, `gh search code "name: <skill>" --filename SKILL.md` is a more precise alternative.
7. **Everything else** gets `"source": null`.
8. **Show the user a summary table**: each source with its number of skills, split into confirmed / likely / locally modified, then the unknown skills grouped by author. Ask them to confirm the *likely* sources and to give a repo URL for any unknown group they recognise. Add what they give, then repeat step 5 for those skills.

Skills the user has written themselves usually have no upstream. Leave them as `null` unless the user names a `local` folder they develop them in.

## Adding or changing a source later

If the user says "X comes from <url>" or "track X from <folder>", parse the reference, add the source and the skill entry, find the path and the installed commit, and show the result. Installing new skills from a URL is SKILL.md's install mode.
