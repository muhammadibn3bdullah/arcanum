# Third-party notices and attribution

Arcanum is an independent project hosted at
<https://github.com/muhammadibn3bdullah/arcanum.git>. It is **not a fork** of
any project listed below. This file records provenance for bundled material
and for external tools that Arcanum depends on.

Repository license files are retained in `licenses/`: `LICENSE` (MIT),
`LICENSE-CONTENT` (CC BY 4.0), and `NOTICE`. Nothing here changes the terms in
those files.

## Maestro (runtime dependency)

- Project: **RunMaestro/Maestro**, version **v1.1.1-RC**.
- This is the Maestro used by Arcanum. It is **not** the unrelated
  `mobile.dev/maestro` mobile UI-testing tool.
- Maestro is used as an external runtime for agent sessions, tab dispatch,
  and completion callbacks. Arcanum does not bundle Maestro's source in this
  repository; consult the upstream repository for its license and terms.

## OpenCode (specialist execution layer)

- Tool: **OpenCode**, version **v1.19.6**.
- Used as the CLI execution layer through which specialists run. Provider
  authentication is managed in the operator's OpenCode configuration.
- OpenCode is an external tool, not Arcanum-authored code; consult its
  upstream repository for license and terms.

## Claude-BugHunter (vendored skills)

- Upstream: <https://github.com/elementalsouls/Claude-BugHunter>
- Pinned revision: `6a530fb3149c8c34d3ba0eb31b4eb431e91da648`
  (upstream commit dated 2026-11-23; imported into Arcanum on 2026-11-25 as
  recorded in `skills/registry/registry.json`).
- Location: `skills/vendor/claude-bughunter/`.
- Licenses of the imported material: **MIT for code**, **CC BY 4.0 for
  content/documentation**. Upstream attribution notices are preserved in the
  imported files and in each registry entry (`source_repository`,
  `source_revision`, `attribution`).
- Arcanum did not author these skills. No authorship or endorsement by
  Arcanum is claimed. The skills are pinned copies, not a submodule or a
  continuously synchronized mirror.

## Other dependencies and technologies

These are external dependencies/tools used by Arcanum; they are not
redistributed as part of this repository's Arcanum-authored code:

| Dependency | Use | License terms |
| --- | --- | --- |
| Python 3.12+ | Runtime for the core package | See upstream Python license |
| pydantic | Structured models and validation | See upstream project |
| pytest | Test runner (development) | See upstream project |
| discord.py | Discord bridge | See upstream project |
| mcp | Tool/server integration | See upstream project |
| Bun | Dashboard runtime/dev server | See upstream project |
| React | Dashboard UI (bundled by Bun) | See upstream project |
| SQLite | Embedded runtime database | See upstream project |

## No affiliation

Arcanum is not affiliated with, endorsed by, or sponsored by
RunMaestro/mobile.dev, Anthropic, the OpenCode authors, elementalsouls, the
Claude-BugHunter authors/contributors, or any other third party named here.
Names and trademarks belong to their respective owners.
