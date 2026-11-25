# Skill registry

`skills/registry/registry.json` is the authoritative inventory of bundled
skills. As checked in for 0.1.0 it declares:

- `schema_version`: 1
- `generated_on`: 2026-11-25
- `generator`: `scripts/generate_skill_registry.py`
- `source_of_truth`: files in `skills/arcanum` and
  `skills/vendor/claude-bughunter`
- `upstream`: `elementalsouls/Claude-BugHunter` at revision
  `6a530fb3149c8c34d3ba0eb31b4eb431e91da648`, code MIT, content CC-BY-4.0
- `skill_count`: 88 (5 `arcanum`, 83 `claude-bughunter`)

Each entry records `skill_id`, `name`, `source`, `source_repository`,
`source_revision`, `content_hash`, `version`, `author`, `license`,
`attribution`, `description`, `triggers`, `supported_agents`,
`required_tools`, `required_dependencies`, `references`, and
`local_modifications`.

## Validation

```sh
python scripts/validate_skill_registry.py
```

The validator checks required fields, duplicate IDs, the presence of each
skill directory, content hashes computed from relative file paths plus file
SHA-256 values, and that no on-disk `SKILL.md` is unregistered. CI runs this
command when the validator exists in the checkout.

## Regeneration

```sh
python scripts/generate_skill_registry.py
python scripts/validate_skill_registry.py
```

Regenerate after intentionally adding, removing, or modifying a bundled skill.
Commit the regenerated registry together with the skill change. A hash mismatch
without an intentional skill change means the checkout is inconsistent —
investigate before regenerating. Do not hand-edit generated hash fields.

Note: the registry currently labels the five Arcanum-authored skills with
`license: PENDING`; do not infer a license for them from the vendored entries.
The repository-level license terms are in `licenses/`.
