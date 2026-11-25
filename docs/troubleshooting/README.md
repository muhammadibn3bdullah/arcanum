# Troubleshooting

This page lists likely causes and checks, not a record of resolved incidents.

| Symptom | Likely cause | What to check |
| --- | --- | --- |
| `Maestro CLI not found` | Binary path mismatch | Set `MAESTRO_CLI` to an absolute path; confirm version v1.1.1-RC from RunMaestro/Maestro (not mobile.dev). |
| `OpenCode CLI not found` | Binary path mismatch | Set `OPENCODE_CLI` (default `~/.opencode/bin/opencode`); confirm v1.19.6 and operator auth. |
| `ModuleNotFoundError` when running scripts | Missing install / wrong interpreter | Activate the venv, install `pydantic pytest discord.py mcp`, and run tests with `PYTHONPATH=src`. |
| `pytest: command not found` | Test dependency absent | `python -m pip install pytest` and use `python -m pytest`. |
| Skill not found for a phase | Skill not installed in the configured root | Re-run the installer; check `skills/arcanum/` and `skills/vendor/claude-bughunter/`; see [skills/registry.md](../skills/registry.md). |
| `registry OK` fails with content hash mismatch | Skill files changed without regeneration | Review the diff, then `python scripts/generate_skill_registry.py` and re-validate. |
| `unregistered skills` error | New skill directory has no registry entry | Regenerate the registry after intentionally adding a skill. |
| `Target ... has no authorized scope declaration` | No trusted scope record | Register the domain through the Scope Manager for the authorized engagement; do not guess targets. |
| Verification returns REJECT for an "ack only" result | Agent returned `ok`/`done`/marker, not execution evidence | Require structured JSON with task/execution IDs, actions, and evidence refs; see [security.md](../architecture/security.md). |
| Verification rejects a result from another target | Cross-target isolation | Ensure workflow/target/task/execution IDs match the current dispatch; results from persistent tabs must be correlated by cursor. |
| `Refusing unregistered tab` / stale tab | Tab no longer matches the registry or Maestro metadata | Use the registry-aware dispatch wrapper; let the registry mark the stale mapping and create one replacement. |
| Capability DENIED | Scope, task type, or risk does not permit the call | Stop the phase; do not switch tools. Recheck the scope record and request the correct risk level. |
| REQUIRE_HUMAN_APPROVAL never clears | No operator approval decision | Run the bridge's pending/approve flow; the runtime re-runs the original phase only after approval. |
| SQLite `database is locked` | Another process holds the DB | Stop other Arcanum processes; confirm `ARCANUM_HOME` points at the intended directory; back up before deleting any DB. |
| Dashboard 401 on an API call | `DASHBOARD_TOKEN` required | Health and ingest are exempt; open the dashboard once with `?token=<value>` so the UI stores it, and check the token in `.env`. |
| Ingest rejected | Missing/wrong `INGEST_TOKEN` | Send `Authorization: Bearer <INGEST_TOKEN>`. |
| Discord bot ignores messages | Token, Message Content intent, or allowlist | Check `DISCORD_BOT_TOKEN`, the privileged intent, and `ALLOWED_USER_IDS` / `ALLOWED_CHANNEL_IDS`. |
| PowerShell blocks the installer | Execution policy | Use `powershell -ExecutionPolicy Bypass -File .\installers\install.ps1` after reviewing the script. |
| Bash helper scripts fail on Windows | No Bash shell | Use Git Bash, or the corresponding PowerShell script when one is provided. |

For upgrade and data safety steps, see
[installation/upgrade-uninstall.md](../installation/upgrade-uninstall.md).
