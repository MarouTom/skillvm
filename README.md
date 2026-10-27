# git-skill-updater

**Install, update, and roll back Claude skills from GitHub, with proof of where each one came from.**

You've probably installed skills by copying folders from GitHub repos into `~/.claude/skills/`. From then on, nothing tracks them. You can't tell which version you have, whether there's a newer one, or whether you've edited it. And once you've copied a new version over, the old one is gone.

`git-skill-updater` is a Claude Code skill that takes care of this. Ask Claude *"update my skills"* and it:

- **finds where every installed skill came from**, even ones you installed months ago, and proves it with an exact file match against a GitHub commit
- **shows what's new** for each skill: commits, files, and anything that needs a security review
- **updates** all your skills, or the ones you name
- **keeps every version** it replaces, so you can roll back to any of them, or to any version ever published upstream
- **never overwrites your local edits** without asking.

It works with the skills you already have, so there's nothing to reinstall.

---

## Install

```bash
git clone --depth 1 https://github.com/MarouTom/git-skill-updater.git /tmp/git-skill-updater
```

```bash
cp -R /tmp/git-skill-updater/git-skill-updater ~/.claude/skills/
```

Start a new Claude Code session. The skill is now available as `/git-skill-updater`. From then on it can update itself along with your other skills.

**Requirements:** `git`, `curl`, and Python 3.8+ (standard library only; nothing to `pip install`).

## Use

Talk to Claude in plain language:

> *Check my skills for updates.*
> *Update everything.* · *Update chart-maker and pdf-toolkit.* · *Update all the writing-\* skills.*
> *Install the meeting-notes skill from acme-labs/agent-skills.*
> *What changed in chart-maker?* · *Show me the previous versions of pdf-toolkit.*
> *Undo the last update to sql-helper.* · *Go back to chart-maker 1.2.* · *Keep chart-maker at this version.*

Or use the slash command:

```
/git-skill-updater --check
/git-skill-updater all
/git-skill-updater chart-maker pdf-toolkit
/git-skill-updater --install owner/repo [skill ...]
/git-skill-updater --install https://github.com/owner/repo/tree/main/skills/foo
/git-skill-updater --history chart-maker
/git-skill-updater --rollback chart-maker [1.2 | <commit> | <tag> | <snapshot> | <date>]
/git-skill-updater --pin chart-maker [<version>]
/git-skill-updater --unpin chart-maker
```

Updating three or more skills always shows a preview first and asks once before applying anything.

## What you see

Every run ends with the same report, so results are easy to scan and compare. This one comes from a real update:

> ## Skill changes · 2026-10-27 10:03 · update
>
> | # | Skill | Action | From | To | Source |
> |---|---|---|---|---|---|
> | 1 | chart-maker | updated | 1.2 · `3f9c2a1` · 2026-09-14 | 1.4 · `8b7e4d0` · 2026-10-20 | acme-labs/agent-skills |
>
> ### What changed
> 1. **chart-maker**: 1 commit · 4 files (+156 −8)
>    - `8b7e4d0` Add dark-mode palettes
>    - ⚠ review: changes `scripts/render_chart.py` (+1 test file)
>    - snapshot `20261027T090345Z` holds the previous version
>
> ### Not changed
> - Up to date: 0
> - Skipped: none
> - Failed: none
>
> Undo any row with `/git-skill-updater --rollback <skill>`. Changes take effect in a new session.

From and To are `version · commit · date`. `—` means the skill doesn't have a `version:` field. `⚠ review` lines flag new or changed scripts, `allowed-tools`, and hooks, because a skill update can change what code runs on your machine.

## How it works

**1. Finding sources.** On the first run, the skill builds a registry of where each installed skill came from. It looks in three places:
- install notes left by other installers
- GitHub links in the skills' own files
- GitHub search: it groups skills by author, finds the author's account, and tries that account's skill repos.

One repo found this way often resolves dozens of skills at once. On a test machine with 222 skills and no install records for 177 of them, every skill was matched in under a minute.

**2. Proof by fingerprint.** A skill folder's fingerprint is its git tree hash, the same id git gives that folder inside a repo. If an installed folder's fingerprint equals the tree at some upstream commit, that's exactly the version installed. That's a proof, not a guess by name. A skill the repo has but that doesn't match any commit means you edited it, or it came from a fork, and you're asked before anything is recorded.

**3. Snapshots before every change.** Before an update, install, or rollback writes anything, the current folder is copied to `~/.claude/skill-versions/snapshots/<skill>/<timestamp>/`, and the copy is verified. Then the new version is staged, swapped in, and validated. If validation fails, the old folder is put straight back.

**4. History both ways.** You can roll back to any snapshot this machine has had, or to any commit, tag, or `version:` ever published upstream.

**5. Cheap fetching.** Repos are cloned as blobless mirrors into a private cache: commits and folder structure only, with file contents fetched only when needed. A 170-skill repo is about 2 MB and takes a second. Your own git clones are never touched.

## Safety

- Local edits are detected by fingerprint and never overwritten without your answer. When you do overwrite, the edits stay in a snapshot.
- New skills and updates that add or change scripts, `allowed-tools`, or hooks are flagged for review.
- Upstream archives are extracted with Python's `tarfile` "data" filter, which rejects paths and links that escape the skill folder.
- Skills that are symlinks (your development copies) are never modified.
- All state lives in one folder, `~/.claude/skill-versions/`. Delete it to start over; your skills aren't affected.

## Use the scripts directly

The skill is a thin layer over three scripts, which you can also run yourself:

```bash
S=~/.claude/skills/git-skill-updater/scripts
python3 $S/bootstrap.py --search                  # build the registry (safe to re-run)
python3 $S/check.py                               # what's outdated?
python3 $S/check.py --history chart-maker         # local snapshots + upstream versions
python3 $S/check.py --diff chart-maker            # your local edits
python3 $S/apply.py update all --dry-run          # preview
python3 $S/apply.py update all
python3 $S/apply.py install owner/repo skill-a skill-b
python3 $S/apply.py rollback chart-maker 1.2
python3 $S/apply.py pin chart-maker
```

Each script takes `--help`. Exit code `2` means a decision is needed; the output says which.

- **Project skills.** Pass `--skills-dir <repo>/.claude/skills`. With `apply.py`, it goes before the subcommand.
- **Other agents.** The scripts don't depend on Claude Code. Point `--skills-dir` at any folder of `SKILL.md` skills.
- **Separate state.** Set `GSU_STORE=/some/folder` to keep the registry and snapshots somewhere else, for example for testing.

## Repository layout

```
git-skill-updater/
├── SKILL.md               what Claude follows: which script to run, and how to handle decisions
├── scripts/
│   ├── lib.py             fingerprints, registry, fetching, snapshots, the report
│   ├── bootstrap.py       find and confirm where installed skills came from
│   ├── check.py           check for updates, version history, local diffs (read-only)
│   └── apply.py           update, rollback, install, pin, unpin
└── references/
    ├── sources.md         registry format and source resolution, step by step
    └── versions.md        fingerprints, snapshots, staging, history, retention
```

## Limitations

- **Git sources only.** Skills distributed as zip files or single files can't be tracked. Non-GitHub git hosts work for install and update, but GitHub search only finds GitHub repos.
- **Plugin-managed skills.** Skills installed through Claude Code plugins belong to the plugin manager; update them with `/plugin`.
- **Rate limits.** Unauthenticated GitHub search allows about 10 requests a minute. The skill paces itself, and stops cleanly if the limit is reached.
- **Snapshots are local.** They live on this machine, and the 10 newest per skill are kept.

## Contributing

Issues and pull requests are welcome. To test changes without touching your real skills, point both the skills folder and the state folder at a scratch copy:

```bash
GSU_STORE=/tmp/gsu-test python3 git-skill-updater/scripts/bootstrap.py --skills-dir /tmp/test-skills --search
```
