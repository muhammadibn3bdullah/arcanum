# Changelog

All notable changes to Arcanum are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[SemVer](https://semver.org/).

## [Unreleased]

Nothing recorded yet.

## [0.1.0] - 2026-11-25

Initial standalone release of the independent Arcanum repository.

### Added

- Independent repository at <https://github.com/muhammadibn3bdullah/arcanum.git>.
  Arcanum is **not a fork** of Claude-BugHunter.
- Five-agent model: `hackbot-lead`, `recon-agent`, `hunt-agent`,
  `validate-agent`, `report-agent`. The lead delegates only; specialists
  perform phase work through OpenCode.
- Maestro-first orchestration with registry-aware, target-specific tab
  dispatch, completion callbacks to a single registered lead tab, and a
  `(workflow_id, target_id, agent_id)` tab registry.
- Canonical shared domain rooms: one room per `(workflow_id, target_id)`, five
  agent-facing views of the same `room_id`, and internal execution sessions
  recorded as metadata only.
- Completion verification gate: acknowledgments, markers, and refusals are
  rejected; acceptance requires structured JSON results, task-specific
  criteria, trusted agent identity, and cross-target/workflow isolation.
- Authorization gateway with per-phase capability grants (`grant_id`), scope
  re-verification, and a human-in-the-loop gate for active testing.
- Skill bundle: five Arcanum-authored skills and 83 pinned upstream skills from
  `elementalsouls/Claude-BugHunter` at commit
  `6a530fb3149c8c34d3ba0eb31b4eb431e91da648` (upstream commit dated
  2026-11-23), with provenance and content hashes in
  `skills/registry/registry.json`.
- Installer entry points `installers/install.sh` (macOS/Linux) and
  `installers/install.ps1` (Windows) using a shared Python engine; local
  runtime state under `~/.arcanum`.
- Dashboard/harness (`apps/dashboard`, `integrations/harness`), Discord bridge
  (`integrations/discord`), migration helper (`scripts/migrate_database.py`),
  and registry tooling (`scripts/generate_skill_registry.py`,
  `scripts/validate_skill_registry.py`).
- CI workflow with an OS matrix (`ubuntu-latest`, `macos-latest`,
  `windows-latest`) for Python tests, registry validation, secret scanning,
  installer syntax checks, and targeted verification tests. CI does not
  publish releases.
- Documentation: README, third-party notices, environment example, and guides
  under `docs/installation/`, `docs/architecture/`, `docs/skills/`,
  `docs/agents/`, and `docs/troubleshooting/`.

### Migration note

- This is a new independent project, not an upgrade of Claude-BugHunter or a
  hackbots starter repository. Do not assume configuration or agent wiring
  carries over: review `.env.example`, re-run the installer, and re-wire
  Maestro agents with the repo's wiring script if you are moving an existing
  local setup.
- Runtime databases are local state under `~/.arcanum`. Back them up before
  upgrading, then run `python scripts/migrate_database.py`, which
  initializes/adds missing schema without overwriting existing data.
- Vendored skills are pinned imported copies, not a live upstream checkout.
  Preserve their attribution and do not edit them in place; changes belong in
  a new upstream pin or an Arcanum-authored skill.
- No cross-platform fresh-clone installation results are asserted for this
  release. The automated checks are source-level and unit-test checks only.

[Unreleased]: https://github.com/muhammadibn3bdullah/arcanum/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/muhammadibn3bdullah/arcanum/releases/tag/v0.1.0
