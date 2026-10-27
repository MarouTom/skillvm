# Versions: snapshots, history, rollback

> `scripts/apply.py` and `scripts/lib.py` implement everything here. Read this to understand or debug them, or to work by hand when the scripts can't run.

Two kinds of version history exist for a skill:

- **Local snapshots**: copies of what was installed on this machine, taken before every change. They work for any skill, including ones with no upstream.
- **Upstream versions**: commits and tags in the skill's git source that touched its folder. They let you install any version that was ever published, including ones this machine never had.

Variables:

```bash
SKILLS="$HOME/.claude/skills"           # or ~/.codex/skills, ~/.agents/skills: the folder being managed
STORE="$(dirname "$SKILLS")/skill-versions"
NAME=<skill folder name>
```

## Fingerprint

A skill folder's fingerprint is the git tree id of its contents. It's computed the same way git computes a folder's id inside a repo, so an installed copy can be compared directly with any upstream commit (`git rev-parse <commit>:<path>`).

```bash
fingerprint() {   # usage: fingerprint <folder>
  local idx="$STORE/tmp/fp-index.git"
  rm -rf "$idx" && git init -q --bare "$idx"
  printf '.DS_Store\n__pycache__/\n*.pyc\n.pytest_cache/\n' > "$idx/info/exclude"
  git --git-dir="$idx" --work-tree="$1" add -A
  git --git-dir="$idx" write-tree
  rm -rf "$idx"
}
```

- Installed fingerprint == registry `fingerprint`: the skill is unchanged since it was installed.
- Installed fingerprint != registry `fingerprint`: the skill has local edits.
- Installed fingerprint == `git rev-parse <c>:<path>`: the installed copy is exactly upstream commit `<c>`.

## Snapshot layout

```
$STORE/snapshots/<name>/<snapshot-id>/
├── files/        # exact copy of the skill folder
└── meta.json
```

`<snapshot-id>` is the UTC time, `date -u +%Y%m%dT%H%M%SZ`. If two snapshots happen in the same second, add `-2`, `-3`, and so on.

`meta.json`:

```json
{
  "skill": "chart-maker",
  "id": "20261027T091500Z",
  "reason": "pre-update",
  "version": "2.1.1",
  "commit": "3f9c2a1e5b7d8c904a6e1f2b3c4d5e6f7a8b9c0d",
  "source": "acme-labs/agent-skills",
  "fingerprint": "9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a2f1e0d",
  "locally_modified": false,
  "created_at": "2026-10-27T09:15:00Z"
}
```

`reason` is one of `pre-update`, `pre-rollback`, `pre-install`, or `manual` (the user asked for a snapshot).

## Taking a snapshot

```bash
ID=$(date -u +%Y%m%dT%H%M%SZ)
DEST="$STORE/snapshots/$NAME/$ID"
mkdir -p "$DEST"
cp -Rp "$SKILLS/$NAME" "$DEST/files"
# write meta.json (python3 or jq), with the fingerprint of "$DEST/files"
```

Check the snapshot before going on: its fingerprint must equal the installed folder's fingerprint. If it doesn't, stop and don't modify the skill.

If the installed fingerprint already equals the newest snapshot's fingerprint, don't make a duplicate. Reuse that snapshot id in the history line.

## Installing a folder

Use this for both updates and rollbacks. `$NEW` is a staged folder under `$STORE/tmp/` that holds the target version.

```bash
NEW="$STORE/tmp/new-$NAME"          # already populated
OLD="$STORE/tmp/old-$NAME"
rm -rf "$OLD"
mv "$SKILLS/$NAME" "$OLD"
mv "$NEW" "$SKILLS/$NAME"
# validate (below); on failure: rm -rf "$SKILLS/$NAME" && mv "$OLD" "$SKILLS/$NAME"
rm -rf "$OLD"                        # safe: the snapshot holds this content
```

Validation:
- `"$SKILLS/$NAME/SKILL.md"` exists
- it starts with a `---` frontmatter block that has non-empty `name:` and `description:` fields
- its fingerprint equals the intended target's (the upstream tree id, or the snapshot's fingerprint).

If any check fails, the new folder is removed and the previous one is moved back. The run reports `restored after failed update`.

If the frontmatter `name:` differs from the folder name, keep the folder name and mention the difference to the user.

## Staging an upstream version

```bash
STAGE="$STORE/tmp/stage-$NAME"
rm -rf "$STAGE" "$STORE/tmp/new-$NAME" && mkdir -p "$STAGE"
git -C "$CACHE" archive "$COMMIT" "$SKILL_PATH" | tar -x -C "$STAGE"
mv "$STAGE/$SKILL_PATH" "$STORE/tmp/new-$NAME"
rm -rf "$STAGE"
```

Upstream archives are untrusted. The scripts extract them with Python's `tarfile` "data" filter (Python 3.12+), which rejects absolute paths, `..` and links that point outside the folder.

When `$SKILL_PATH` is `.` (the whole repo is one skill), use `git -C "$CACHE" archive "$COMMIT" | tar -x -C "$STORE/tmp/new-$NAME"` instead.

`$COMMIT` can be a commit hash, a tag, or a branch. To install a version given by its `version:` field, search for it:

```bash
for c in $(git -C "$CACHE" log --format=%H "$BRANCH" -- "$SKILL_PATH"); do
  git -C "$CACHE" show "$c:$SKILL_PATH/SKILL.md" 2>/dev/null | sed -n '1,/^---$/{/version:/p;}' | grep -q "\"\{0,1\}$WANT\"\{0,1\}$" && { echo "$c"; break; }
done
```

That finds the newest commit that has that version. If none does, list the versions that exist.

## Staging a local snapshot

```bash
rm -rf "$STORE/tmp/new-$NAME"
cp -Rp "$STORE/snapshots/$NAME/$ID/files" "$STORE/tmp/new-$NAME"
```

## Listing versions

**Local snapshots:**

```bash
ls -1r "$STORE/snapshots/$NAME" 2>/dev/null
```

Read each `meta.json` and show `id | version | commit (short) | reason | locally modified`. Mark the snapshot whose fingerprint matches the installed folder as `← installed`, if any does.

**Upstream versions:**

```bash
git -C "$CACHE" log -n 20 --format='%h %cs %s' "$BRANCH" -- "$SKILL_PATH"
git -C "$CACHE" tag --contains "$(git -C "$CACHE" log --reverse --format=%H -- "$SKILL_PATH" | head -1)" 2>/dev/null
```

For each commit, the version field can be read with `git -C "$CACHE" show <c>:"$SKILL_PATH"/SKILL.md | head -20`. Only do that for the commits you're showing. Mark the registry `commit` as `← installed`.

**What changed between two versions:**

```bash
git -C "$CACHE" log --oneline A..B -- "$SKILL_PATH"           # commit subjects
git -C "$CACHE" diff --stat A B -- "$SKILL_PATH"              # files touched
git -C "$CACHE" diff A B -- "$SKILL_PATH/SKILL.md"            # the instructions themselves
diff -ru "$STORE/snapshots/$NAME/$ID/files" "$SKILLS/$NAME"   # snapshot vs installed
```

## History log

Add one JSON line to `$STORE/history.jsonl` for every change:

```json
{"at":"2026-10-27T09:15:04Z","skill":"chart-maker","action":"update","from":{"commit":"3f9c2a1","version":"2.1.1"},"to":{"commit":"9f8e7d6","version":"2.2.0"},"snapshot":"20261027T091500Z"}
```

`action` is one of `update`, `rollback`, `install`, `pin`, `unpin`, or `restore-failed`. "Undo the last update to X" means: find the newest `update` line for X, then roll back to its `snapshot`.

## Retention

Keep the 10 newest snapshots per skill. Never prune:
- a snapshot named by the skill's `pinned` field
- the newest `pre-update` snapshot.

Prune only at the end of a successful run, and tell the user how many snapshots were removed. If the user says "keep everything", skip pruning for that run.
