# Changelog

All notable changes to skillvm are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Claude Code plugin packaging: `.claude-plugin/plugin.json` and a `marketplace.json`, so the repo can be installed with `claude plugin install skillvm@skillvm`.

## [1.0.0] - 2026-10-27

First public release.

### Added

- Source discovery for skills that are already installed: install markers, GitHub links in skill files, and GitHub search by author. Each source is confirmed by matching the folder's git tree id to an upstream commit.
- `--check` to report outdated skills without changing anything.
- Updates for one skill, several skills, or all skills (`all` previews first and asks once).
- `--install` from `owner/repo` or a GitHub folder URL.
- `--search` to find skills on GitHub by topic, ranked by stars, with `--details` for any result.
- `--history` showing local snapshots and every upstream version, with tags and `version:` values.
- `--rollback` to a snapshot, commit, tag, `version:` value or date.
- `--pin` and `--unpin`, so "update all" leaves a skill alone.
- A standard change report on every run, with **⚠ review** flags for changed scripts, `allowed-tools` and hooks.
- Claude Code and Codex support, including `--agent` to manage the other agent's skills and `agents/openai.yaml` for Codex.
- Snapshots before every write, staged swaps checked against the expected fingerprint, and automatic restore on failure.
- Handling of upstream history rewrites (force-push, rebase, squash) by matching versions again by fingerprint.
- Blobless, parallel fetching into a private cache.

[Unreleased]: https://github.com/MarouTom/skillvm/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/MarouTom/skillvm/releases/tag/v1.0.0
