# Arcanum provenance and supply-chain record

## Independent identity

- Product: **Arcanum**
- Repository: `https://github.com/muhammadibn3bdullah/arcanum.git`
- Status: new standalone repository; no GitHub fork relationship with Claude BugHunter.
- Previous fork (backup only): `https://github.com/muhammadibn3bdullah/Hackbot-agents`
- Upstream skill source (not a runtime dependency): `https://github.com/elementalsouls/Claude-BugHunter`

## Pinned upstream skill revision

- Repository: `elementalsouls/Claude-BugHunter`
- Commit: `6a530fb3149c8c34d3ba0eb31b4eb431e91da648`
- Date: 2026-11-23
- License: code MIT (`licenses/LICENSE`), content and documentation CC BY 4.0 (`licenses/LICENSE-CONTENT`)
- Attribution: `licenses/NOTICE`, `THIRD_PARTY_NOTICES.md`
- Local vendored tree hash: `04e777a4212750bd11b7f7ac5adf6f8c2c785030ce4634b7e904eb2a8cd8eaf0` (120 files)

Upstream skills are a capability/knowledge layer only. Arcanum remains the owner of task planning, routing, authorization, orchestration, verification, conversation rooms, Dashboard and Discord.

## Arcanum-authored skills

Introduced in the fork commit `d9031de` and preserved as Arcanum-owned:

- `api-fingerprint-caido`
- `interceptor`
- `js_analysis`
- `lazy_chunk_downloader`
- `say_hello`

Vendored tree hash: `c3c36ba959a994bfeb5db38f24e1cfbf04f3b4f61a8ae2f556b7bdb4e8911bbd` (14 files).

License for Arcanum-authored skills: **PENDING owner decision**. It is not inferred from the upstream license.

## Authoritative registry

`skills/registry/registry.json` is generated from the actual files by
`scripts/generate_skill_registry.py` and verified by
`scripts/validate_skill_registry.py`. Every entry carries source, revision,
content hash, attribution, license, and local modification status.

## Migration backups

`~/Projects/arcanum-migration/backups/`:

- `Hackbot-agents.bundle`
- `hackbots-starter.bundle`
- `arcanum.bundle`
- `sources-pre-migration.tar.gz`

The old fork will not be deleted until the new repository passes migration and installation acceptance tests and the owner explicitly confirms.
