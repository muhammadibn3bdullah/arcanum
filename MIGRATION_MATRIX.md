# Arcanum C2.2 source-to-destination migration matrix

Prepared before any remote changes. Backups are at `~/Projects/arcanum-migration/backups/`.

| Source | Classification | Destination | Notes |
|---|---|---|---|
| `~/Projects/arcanum/arcanum/` | Arcanum-owned application | `src/arcanum/` | Package path changed; `Settings.base_dir` and runtime DB paths moved to `ARCANUM_HOME`. |
| `~/Projects/arcanum/tests/` | Arcanum tests | `tests/unit/` | `PYTHONPATH=src` added via `tests/conftest.py`; script-path tests updated. |
| `~/Projects/arcanum/scripts/` | Arcanum runtime scripts | `scripts/` | Path insertion updated to `ROOT/src`. |
| `~/Projects/hackbots-starter/harness/public/`, `server.ts`, `db.ts`, etc. | Arcanum Harness Dashboard | `apps/dashboard/` | Runtime DB default moved to `ARCANUM_HOME`. |
| `~/Projects/hackbots-starter/harness/mcp_ingest.py`, `scripts/` | Harness MCP + utilities | `integrations/harness/` | Preserved behavior. |
| `~/Projects/hackbots-starter/discord_bot/` | Discord bridge | `integrations/discord/` | Credentials never copied. |
| `~/Projects/hackbots-starter/.maestro/playbooks/2026-11-24-Arcanum-Cycle/ARCANUM-01.md` | Arcanum playbook | `workflows/maestro/playbooks/` | Legacy Pentest Cycle notes intentionally not migrated. |
| `~/Projects/Hackbot-agents/skills/{api-fingerprint-caido,interceptor,js_analysis,lazy_chunk_downloader,say_hello}` | 5 Arcanum custom skills | `skills/arcanum/` | Introduced in fork commit `d9031de`; nested references/scripts preserved. |
| `~/Projects/Hackbot-agents/skills/*` (remaining) | Vendored Claude BugHunter skills | `skills/vendor/claude-bughunter/` | Pinned upstream revision `6a530fb3149c8c34d3ba0eb31b4eb431e91da648`. |
| `~/Projects/Hackbot-agents/commands/` | Upstream slash commands | `skills/vendor/claude-bughunter/commands/` | Knowledge layer only; not an Arcanum orchestrator. |
| `~/Projects/Hackbot-agents/LICENSE`, `LICENSE-CONTENT`, `NOTICE` | Third-party licenses | `licenses/` | Preserved verbatim. |
| `~/.config/opencode/opencode.json` (agent prompts) | Arcanum agent definitions | `config/agents/*.md` + generated config template | Extracted from `wire_maestro_agents.py`; secrets not copied. |
| Runtime `*.db`, `*.sqlite`, `.env`, logs, tokens | User state / secrets | **Not migrated** | Created by installer under `~/.arcanum`. |

## Verification basis

- Arcanum verified through commits `b1e79a3` (C2.1.4), `20aa9ba` (C2.1.4.1).
- Harness/Discord verified through `9742ab2`, `99f77e1`.
- Upstream fork `muhammadibn3bdullah/Hackbot-agents` backup: `Hackbot-agents.bundle`.
- Local Arcanum backup: `arcanum.bundle`.
- Local Harness/Discord backup: `hackbots-starter.bundle`.
- Raw source archive: `sources-pre-migration.tar.gz`.

No old repository is a runtime dependency of the new project.
