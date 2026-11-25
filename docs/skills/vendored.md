# Vendored skills (Claude-BugHunter)

`skills/vendor/claude-bughunter/` holds pinned imported copies of skills from
[elementalsouls/Claude-BugHunter](https://github.com/elementalsouls/Claude-BugHunter).

- Pinned revision: `6a530fb3149c8c34d3ba0eb31b4eb431e91da648` (upstream commit
  dated 2026-11-23; imported into Arcanum on 2026-11-25).
- Count: 83 skills, recorded in `skills/registry/registry.json`.
- Upstream licenses: **MIT for code**, **CC BY 4.0 for content/documentation**.
- Every registry entry retains `source_repository`, `source_revision`, and an
  attribution string. Upstream notices in imported files are preserved.

## Rules

- Arcanum did not author these skills. Do not remove or rewrite attribution,
  do not claim authorship, and do not present them as Arcanum-origin work.
- Arcanum is a new independent repository, **not a fork** of Claude-BugHunter.
- Treat these directories as pinned vendor copies: avoid in-place edits. To
  change behavior, add an Arcanum-authored skill in `skills/arcanum/` or
  deliberately re-pin upstream and review the diff.
- After any change to the vendor tree, regenerate and validate the registry:
  `python scripts/generate_skill_registry.py` then
  `python scripts/validate_skill_registry.py`.

See [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md) for the broader
attribution statement.
