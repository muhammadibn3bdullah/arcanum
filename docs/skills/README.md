# Skills

Arcanum bundles skills in two separate trees, with provenance recorded in a
single registry:

| Tree | Contents |
| --- | --- |
| `skills/arcanum/` | Five Arcanum-authored skills |
| `skills/vendor/claude-bughunter/` | 83 pinned skills imported from `elementalsouls/Claude-BugHunter` |
| `skills/registry/registry.json` | Inventory: 88 skills with source, revision, license, and content hash |

A specialist must load a real, installed skill before it can request a
capability. Skill loading is a deterministic precondition in the agent code,
not merely a prompt suggestion.

## Guides

| Guide | Contents |
| --- | --- |
| [registry.md](registry.md) | Registry schema, hashes, validation, regeneration |
| [custom.md](custom.md) | The five Arcanum-authored skills |
| [vendored.md](vendored.md) | Upstream pin, licenses, and attribution rules |

Do not edit vendored skills in place. Preserve upstream attribution; put new
work in an Arcanum-authored skill or re-pin upstream deliberately.
