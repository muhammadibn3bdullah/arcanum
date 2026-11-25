# Upgrade and uninstall

## Upgrade

1. Back up local state first — by default `~/.arcanum/` contains
   `arcanum.db`, `arcanum-approvals.sqlite`, and `arcanum-tabs.sqlite`.
2. Update the checkout: `git pull` (or replace the checkout for a published
   release).
3. Re-run the platform installer (`installers/install.sh` or
   `installers/install.ps1`) so agent/skill wiring reflects the new tree. Use
   the installer's own help output for flags.
4. Initialize or upgrade local schema:
   `python scripts/migrate_database.py`. It adds missing tables without
   overwriting existing data and prints the paths it used.
5. Check the bundled skill inventory:
   `python scripts/validate_skill_registry.py`. If it reports a hash mismatch,
   see [docs/skills/registry.md](../skills/registry.md) before regenerating.
6. Review `.env` against `.env.example` for new optional settings. Keep real
   credentials out of version control.
7. Restart the dashboard/Discord bridge and any service helpers you use.

Arcanum is an independent repository, not a fork. Do not assume configuration
from Claude-BugHunter or another starter is compatible; re-check agent wiring
and scope records after switching.

## Uninstall

1. Stop running agents, the Discord bridge, the dashboard, and any service
   helpers you started.
2. Use the uninstall option provided by the platform installer in this
   checkout (see its help output) to remove the agent/skill wiring it created.
3. Revoke the Discord bot token in the Discord developer portal and remove
   any webhook URLs you configured.
4. Remove local credentials (`.env`) from the checkout.
5. Only after a backup, remove the runtime directory (`~/.arcanum` by
   default) if you no longer need task, approval, or tab-registry history.
6. Remove the cloned repository directory itself.

Uninstalling Arcanum does not remove independently installed tools such as
Maestro, OpenCode, Bun, Burp Suite, or Caido.
