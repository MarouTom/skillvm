<div align="center">

# 🔄 skillvm

**The version manager for agent skills.**<br>
Install, update, and roll back skills from GitHub, with proof of where each one came from.

[![Claude Code](https://img.shields.io/badge/Claude_Code-skill-D97757?style=flat-square)](#-install)
[![Codex](https://img.shields.io/badge/Codex-skill-10A37F?style=flat-square)](#-install)
[![Python](https://img.shields.io/badge/python-3.8%2B-3776AB?style=flat-square&logo=python&logoColor=white)](#-install)
[![Dependencies](https://img.shields.io/badge/pip_dependencies-none-2EA44F?style=flat-square)](#-install)
[![Version](https://img.shields.io/badge/version-1.1.0-0969DA?style=flat-square)](skillvm/SKILL.md)

[Install](#-install) · [Use](#-use) · [Find skills](#example-find-new-skills-learn-about-one-install-it) · [Versions &amp; restore](#example-list-every-version-of-a-skill-then-restore-one) · [Example report](#-what-you-see) · [How it works](#-how-it-works) · [Safety](#-safety) · [Scripts](#-use-the-scripts-directly)

</div>

---

You've probably installed skills by copying folders from GitHub repos into `~/.claude/skills/` or `~/.codex/skills/`. From then on, nothing tracks them. You can't tell which version you have, whether there's a newer one, or whether you've edited it. And once you've copied a new version over, the old one is gone.

**skillvm** is a skill for **Claude Code** and **Codex** that takes care of this. Think `nvm` or `pyenv`, but for agent skills. Ask your agent *"update my skills"*, and it handles the rest. It works with the skills you already have, so there's nothing to reinstall.

<table>
<tr>
<td width="50%" valign="top">

### 🔍 Finds every source
Works out which GitHub repo each installed skill came from, even skills installed months ago, and **proves it** with an exact match against an upstream commit.

</td>
<td width="50%" valign="top">

### 📋 Shows what's new
One consistent report per run: versions, commits, line counts, and **⚠ review flags** for changed scripts, tool permissions, and hooks.

</td>
</tr>
<tr>
<td valign="top">

### ⏪ Undo anything
Every version it replaces is snapshotted. Roll back to any snapshot, or to **any commit, tag, or version** ever published upstream.

</td>
<td valign="top">

### 🛡️ Respects your edits
Local changes are detected by fingerprint and **never overwritten** without your answer. When you do overwrite, they stay in a snapshot.

</td>
</tr>
<tr>
<td valign="top">

### 🔎 Discovers new skills
*"Find skills for data management"* searches GitHub, reads each candidate's `SKILL.md`, and shows the **top 10 by stars**, with links, licenses, and last updates. Pick numbers to install.

</td>
<td valign="top">

### 🌍 Speaks your language
Ask in any language. Results, descriptions, and explanations come back in yours, while names, links, and commands stay exact.

</td>
</tr>
</table>

---

## 📦 Install

```bash
git clone --depth 1 https://github.com/MarouTom/skillvm.git /tmp/skillvm
```

Then copy the skill into the skills folder of each agent you use.

| Agent | Command | Invoke with |
|---|---|---|
| **Claude Code** | `cp -R /tmp/skillvm/skillvm ~/.claude/skills/` | `/skillvm` |
| **Codex** | `cp -R /tmp/skillvm/skillvm ~/.codex/skills/` | `$skillvm` |

If you've set `CODEX_HOME`, copy into `$CODEX_HOME/skills` instead. Start a new session afterwards. Each copy manages the skills of the agent it's installed in, and can update itself along with them.

> **Requirements:** `git`, `curl`, and Python 3.8+. Standard library only, so there's nothing to `pip install`.

## 💬 Use

Just ask, in plain language:

> 💭 *Check my skills for updates.*
> 💭 *Update everything.* · *Update chart-maker and pdf-toolkit.* · *Update all the writing-\* skills.*
> 💭 *Find skills for spreadsheet cleanup.* · *Tell me more about number 2.* · *Install 1 and 3.*
> 💭 *Install the meeting-notes skill from acme-labs/agent-skills.*
> 💭 *What changed in chart-maker?* · *Show me the previous versions of pdf-toolkit.*
> 💭 *Undo the last update to sql-helper.* · *Go back to chart-maker 1.2.* · *Keep chart-maker at this version.*

Or pass arguments: `/skillvm …` in Claude Code, `$skillvm …` in Codex.

```
--search "spreadsheet cleanup"            find skills on GitHub, top 10 by stars
--check                                   what's outdated? changes nothing
all                                       update everything (previews first, asks once)
chart-maker pdf-toolkit                   update specific skills
--install acme-labs/agent-skills [skill …]
--install https://github.com/jdoe/writing-skills/tree/main/skills/blog-post
--history chart-maker                     local snapshots + every upstream version
--rollback chart-maker [1.2 | <commit> | <tag> | <snapshot> | <date>]
--pin chart-maker [<version>]             "update all" leaves it alone
--unpin chart-maker
```

### Example: list every version of a skill, then restore one

> 💭 *Show me all the versions of chart-maker.*

The skill lists two kinds of versions: **local snapshots** (what this machine has had) and **upstream versions** (everything ever published):

> **Version history · chart-maker**
>
> **Local snapshots**
>
> | Snapshot | Version | Commit | Reason | Taken (UTC) |
> |---|---|---|---|---|
> | `20261027T090345Z` | 1.2 | `3f9c2a1` | pre-update | 2026-10-27 09:03 |
>
> **Upstream versions** · acme-labs/agent-skills · `skills/chart-maker` · newest first
>
> | Commit | Date | Version | Tags | Subject |
> |---|---|---|---|---|
> | `8b7e4d0` ← installed | 2026-10-20 | 1.4 | v1.4.0 | Add dark-mode palettes |
> | `61aa3f2` | 2026-10-08 | 1.3 | | Fix legend overlap on small charts |
> | `2d9e0c4` | 2026-10-01 | 1.3 | v1.3.0 | Support SVG export |
> | `3f9c2a1` | 2026-09-14 | 1.2 | v1.2.0 | Add heatmap charts |
> | `7e5a1c8` | 2026-08-02 | 1.1 | v1.1.0 | Add axis label rotation |
> | `5c1b7f9` | 2026-07-11 | 1.0 | v1.0.0 | First release |

Pick any row and ask for it. All of these work:

| You say | Restores |
|---|---|
| *Go back to chart-maker 1.1* | the newest commit with `version: 1.1` |
| *Restore chart-maker to v1.3.0* · *…to `2d9e0c4`* | that tag or commit |
| *Restore the chart-maker snapshot `20261027T090345Z`* | that local snapshot |
| *Restore chart-maker as it was on 2026-10-27* | the newest snapshot from on or before that day |
| *Undo the last update to chart-maker* | the snapshot taken just before it |

> 💭 *Go back to chart-maker 1.1.*

> **Skill changes · 2026-10-27 10:15 · rollback**
>
> | # | Skill | Action | From | To | Source |
> |---|---|---|---|---|---|
> | 1 | chart-maker | rolled back | 1.4 · `8b7e4d0` · 2026-10-20 | 1.1 · `7e5a1c8` · 2026-08-02 | acme-labs/agent-skills |
>
> **What changed**
> 1. **chart-maker**: restored upstream `7e5a1c8`
>    - snapshot `20261027T101502Z` holds the version you rolled back from
>
> **Not changed**
> - Up to date: 0
> - Skipped: none
> - Failed: none
>
> Undo any row by asking to roll that skill back. Changes take effect in a new session.
>
> *Pin chart-maker at 1.1, so "update all" doesn't bring 1.4 back?*

Say yes and the skill stays at 1.1 until you unpin it. Asking for a version that doesn't exist (*"go back to chart-maker 0.9"*) gets you the list of versions that do.

### Example: find new skills, learn about one, install it

> 💭 *Find skills for spreadsheet cleanup.*

The skill searches GitHub (repos tagged `agent-skills` or `claude-skills`, plus the biggest skill collections), opens each candidate, and keeps the skills whose name or description match. The top 10 are ranked by stars:

> **Skill search · "spreadsheet cleanup" · top 10 by GitHub stars**
>
> | # | Skill | What it does | Repo | ★ | Updated | Notes |
> |---|---|---|---|---|---|---|
> | 1 | **csv-wrangler** | Clean, deduplicate, and reshape CSV and Excel files. | acme-labs/agent-skills | 12,480 | 2026-10-14 | v2.1 · 3 scripts |
> | 2 | **sheet-auditor** | Find broken formulas, mixed types, and outliers in spreadsheets. | jdoe/data-skills | 2,315 | 2026-09-30 | v1.0 |
> | 3 | **tidy-tables** | Turn messy tables into tidy, analysis-ready data. | dataworks-lab/tidy-skills | 640 | 2026-08-12 | 1 script |
> | … | | | | | | |
>
> **About each result**
> 1. **csv-wrangler**: github.com/acme-labs/agent-skills/tree/main/skills/csv-wrangler
>    - Repo: acme-labs/agent-skills · ★ 12,480 · MIT · last updated 2026-10-14 · Skills for everyday data work.
> 2. **sheet-auditor**: …

> 💭 *Tell me more about number 2.*

> **sheet-auditor** checks a spreadsheet before you trust it: it finds formulas that return errors, columns that mix numbers and text, and values far outside the usual range, then explains each problem with its cell reference.
>
> **You could ask it:**
> - *"Audit budget-2026.xlsx before I send it to finance."*
> - *"Why does the total in column F not match the sum of the rows?"*
> - *"List the outliers in the sales sheet and suggest which ones are typos."*
>
> **What it installs:** 4 files, no scripts, no special tool permissions.
> **Source:** jdoe/data-skills · ★ 2,315 · MIT · last updated 2026-09-30

> 💭 *Install 1 and 3.*

Both are installed with the usual change report, snapshot, and ⚠ review flags. Results you already have are marked `installed`.

**In your language:** ask *"Trouve des skills pour nettoyer des tableurs"* and the agent searches with English keywords (most skills are described in English), then answers in French. It translates the descriptions and use cases, and keeps skill names, links, and commands exactly as they are.

## 🧾 What you see

Every run ends with the same report, so results are easy to scan and compare:

> **Skill changes · 2026-10-27 10:03 · update**
>
> | # | Skill | Action | From | To | Source |
> |---|---|---|---|---|---|
> | 1 | chart-maker | updated | 1.2 · `3f9c2a1` · 2026-09-14 | 1.4 · `8b7e4d0` · 2026-10-20 | acme-labs/agent-skills |
> | 2 | pdf-toolkit | updated | — · `c41d9e7` · 2026-08-30 | — · `5a2f0b3` · 2026-10-11 | acme-labs/agent-skills |
> | 3 | blog-post | installed | — | 2.0 · `e77c215` · 2026-10-02 | jdoe/writing-skills |
>
> **What changed**
> 1. **chart-maker**: 3 commits · 5 files (+212 −48)
>    - `8b7e4d0` Add dark-mode palettes
>    - `61aa3f2` Fix legend overlap on small charts
>    - `2d9e0c4` Support SVG export
>    - ⚠ review: changes `scripts/render_chart.py` (+1 test file)
>    - snapshot `20261027T090345Z` holds the previous version
> 2. **pdf-toolkit**: 1 commit · 2 files (+31 −9)
>    - `5a2f0b3` Handle encrypted PDFs gracefully
>    - snapshot `20261027T090346Z` holds the previous version
> 3. **blog-post**: new skill · 6 files
>    - ⚠ review: allowed-tools `Read Write`
>
> **Not changed**
> - Up to date: 41
> - Skipped: sql-helper (pinned) · meeting-notes (local edits kept)
> - Failed: none
>
> Undo any row by asking to roll that skill back. Changes take effect in a new session.

From and To are `version · commit · date`. `—` means the skill doesn't have a `version:` field. `⚠ review` lines flag new or changed scripts, `allowed-tools`, and hooks, because a skill update can change what code runs on your machine.

## 🤝 Claude Code and Codex

Each installed copy manages the skills of the agent it's installed in, and keeps its own state next to them:

| Installed in | Manages | State |
|---|---|---|
| `~/.claude/skills/` | Claude Code skills | `~/.claude/skill-versions/` |
| `~/.codex/skills/` (or `$CODEX_HOME/skills`) | Codex skills | `~/.codex/skill-versions/` |
| `~/.agents/skills/` | the shared agents folder | `~/.agents/skill-versions/` |

- **Manage the other agent's skills from either one.** Add `--agent codex` or `--agent claude` (*"check my Codex skills for updates"*).
- **Agents share what they've learned.** If your Claude copy already knows where a skill comes from, the Codex copy tries that repo first, and the other way round, so a source found once is reused.
- **Agent-specific copies.** When a repo keeps one copy of a skill per agent (`.claude/skills/x`, `.codex/skills/x`, `.agents/skills/x`), each agent gets its own.
- **Built-in skills.** Codex's own `.system` skills are never touched.
- **Codex sandbox.** Fetching from GitHub and writing to `~/.codex/skills` happen outside the workspace sandbox, so Codex asks you to approve those commands. Checks can also run offline from the cache with `--no-fetch`.

The skill's frontmatter uses only fields both agents accept, and it passes Codex's skill validator. `agents/openai.yaml` gives Codex a display name and a default prompt.

## ⚙️ How it works

```
 installed skill ──fingerprint──▶ git tree id ──match?──▶ upstream commit ✔
        │                                                      │
        ▼                                                      ▼
   snapshot ◀── before every change          check ──▶ update / rollback / pin
```

1. **🔍 Finding sources.** On the first run, the skill builds a registry of where each installed skill came from. It looks in three places:
   - install notes left by other installers
   - GitHub links in the skills' own files
   - GitHub search: it groups skills by author, finds the author's account, and tries that account's skill repos.

   One repo found this way often resolves dozens of skills at once. On a test machine with 222 skills, 177 of them with no install records, every skill was matched in under a minute.
2. **🧬 Proof by fingerprint.** A skill folder's fingerprint is its git tree hash, the same id git gives that folder inside a repo. If an installed folder's fingerprint equals the tree at some upstream commit, that's exactly the version installed. That's a proof, not a guess by name. If the repo has the skill but no commit matches, you edited it or it came from a fork, and you're asked before anything is recorded.
3. **📸 Snapshots before every change.** Before an update, install, or rollback writes anything, the current folder is copied to `skill-versions/snapshots/<skill>/<timestamp>/`, and the copy is verified. Then the new version is staged, swapped in, and validated. If validation fails, the old folder is put straight back.
4. **🕰️ History both ways.** You can roll back to any snapshot this machine has had, or to any commit, tag, or `version:` ever published upstream.
5. **⚡ Cheap fetching.** Repos are cloned blobless into a private cache: branches, tags, commits and folder structure only. File contents are fetched in one batch, and only for the skill being installed. A 170-skill repo is about 2 MB and takes a second. Checks, fingerprints and fetches run in parallel, so checking 200+ skills takes a few seconds. Your own git clones are never touched.

## 🛡️ Safety

- ✅ **Local edits** are detected by fingerprint and never overwritten without your answer. When you do overwrite, the edits stay in a snapshot.
- ✅ **New skills and updates** that add or change scripts, `allowed-tools`, or hooks are flagged for review.
- ✅ **Upstream files** are written only inside the skill folder: absolute paths, `..`, and links that escape the folder stop the install.
- ✅ **Symlinked skills** (your development copies) are never modified.
- ✅ **Upstream history rewrites** (force-push, rebase, squash) are handled. The installed version is re-matched by fingerprint, and if it no longer exists upstream, the skill is treated as locally modified and replaced only when you agree.
- ✅ **No password prompts.** Git never waits for a password: a private or missing repo fails at once and is reported as unreachable.
- ✅ **One folder of state** per agent (`~/.claude/skill-versions/`, `~/.codex/skill-versions/`). Delete it to start over; your skills aren't affected.

## 🧰 Use the scripts directly

The skill is a thin layer over four scripts, which you can also run yourself.

<details>
<summary><b>Show commands</b></summary>

```bash
S=~/.claude/skills/skillvm/scripts      # or ~/.codex/skills/skillvm/scripts
python3 $S/bootstrap.py --search                  # build the registry (safe to re-run)
python3 $S/search.py "spreadsheet cleanup"        # find skills on GitHub, top 10 by stars
python3 $S/search.py --details 2                  # everything about result 2
python3 $S/apply.py install --from-search 1 3     # install results by number
python3 $S/check.py                               # what's outdated?
python3 $S/check.py --history chart-maker         # local snapshots + upstream versions
python3 $S/check.py --diff chart-maker            # your local edits
python3 $S/apply.py update all --dry-run          # preview
python3 $S/apply.py update all
python3 $S/apply.py install acme-labs/agent-skills meeting-notes
python3 $S/apply.py rollback chart-maker 1.2
python3 $S/apply.py pin chart-maker
```

Each script takes `--help`. Exit code `2` means a decision is needed; the output says which.

- **Another agent's skills.** Pass `--agent claude|codex|agents`.
- **Project skills, or any other folder.** Pass `--skills-dir <repo>/.claude/skills` (or `.codex/skills`). The scripts work with any folder of `SKILL.md` skills.
- With `apply.py`, these options go before the subcommand: `apply.py --agent codex update all`.
- **Separate state.** Set `SKILLVM_STORE=/some/folder` to keep the registry and snapshots somewhere else, for example for testing.

</details>

<details>
<summary><b>Repository layout</b></summary>

```
skillvm/
├── SKILL.md               what the agent follows: which script to run, and how to handle decisions
├── agents/
│   └── openai.yaml        Codex display metadata
├── scripts/
│   ├── lib.py             fingerprints, registry, fetching, snapshots, the report
│   ├── bootstrap.py       find and confirm where installed skills came from
│   ├── check.py           check for updates, version history, local diffs (read-only)
│   ├── search.py          find skills on GitHub by topic, ranked by stars (read-only)
│   └── apply.py           update, rollback, install, pin, unpin
└── references/
    ├── sources.md         registry format and source resolution, step by step
    └── versions.md        fingerprints, snapshots, staging, history, retention
```

</details>

## 🚧 Limitations

- **Git sources only.** Skills distributed as zip files or single files can't be tracked. Non-GitHub git hosts work for install and update, but GitHub search only finds GitHub repos.
- **Plugin-managed and built-in skills.** Skills that come with an agent's plugins, and Codex's `.system` skills, belong to that agent; update them through its own tools.
- **Rate limits.** Unauthenticated GitHub search allows about 10 requests a minute (one search uses 5). The skill runs at full speed while the limit allows, waits when it runs out, and stops cleanly if the wait would be long. Set `GH_TOKEN` or `GITHUB_TOKEN` to raise the limit to 30 a minute. Repeat searches reuse what earlier ones read from repos that haven't changed since.
- **Snapshots are local.** They live on this machine, and the 10 newest per skill are kept.

## 🙌 Contributing

Issues and pull requests are welcome. To test changes without touching your real skills, point both the skills folder and the state folder at a scratch copy:

```bash
SKILLVM_STORE=/tmp/skillvm-test python3 skillvm/scripts/bootstrap.py --skills-dir /tmp/test-skills --search
```
