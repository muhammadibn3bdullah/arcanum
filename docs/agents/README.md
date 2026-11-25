# Agent model

Arcanum uses five fixed agent identities. `hackbot-lead` delegates and reviews;
the other four are phase specialists that run through the OpenCode execution
layer under Maestro.

| Agent | Short description |
| --- | --- |
| `hackbot-lead` | Moderator, delegator, reviewer, final aggregator. No inline phase work. |
| `recon-agent` | Passive reconnaissance and traffic summarization. |
| `hunt-agent` | Attack-surface prioritization and hypothesis generation. |
| `validate-agent` | Evidence-based validation and confirmation of findings. |
| `report-agent` | Confirmed-only report generation. |

Operating rules:

1. Dispatch carries `(workflow_id, target_id)`, a self-contained task brief,
   and the required skill/capability. It goes through the registry-aware tab
   wrapper, not raw Maestro commands.
2. The specialist loads an installed skill before requesting a capability.
3. A capability grant (`grant_id`) is valid only in the phase that requested
   it. DENY stops the phase; REQUIRE_HUMAN_APPROVAL waits for a human.
4. Completion requires a structured JSON result and task-specific verification.
   An acknowledgment, marker, or refusal is never accepted.
5. All five agents see the same canonical room for the target; internal
   sessions are metadata only.

Details: [architecture/agents.md](../architecture/agents.md),
[architecture/shared-rooms.md](../architecture/shared-rooms.md), and
[architecture/security.md](../architecture/security.md). The executable
playbook is `workflows/maestro/playbooks/ARCANUM-01.md`.
