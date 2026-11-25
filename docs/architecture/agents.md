# Agents

Arcanum defines exactly five agent identities (`src/arcanum/core/rooms.py`):

| Agent | Role |
| --- | --- |
| `hackbot-lead` | Moderator/delegator. Reads scope, dispatches phases, reviews results, aggregates the confirmed-only report. Never performs phase work inline. |
| `recon-agent` | Passive reconnaissance. Loads a passive skill, requests `caido_read`-style capabilities, returns summaries of untrusted traffic data. |
| `hunt-agent` | Prioritizes attack-surface hypotheses (high/critical first), using only granted gateway tools. Active checks require the human approval gate. |
| `validate-agent` | Validates hypotheses with evidence, proof-gate checks, and cross-user evidence before a finding can be CONFIRMED. |
| `report-agent` | Writes the final report from confirmed finding IDs through the gateway-wrapped `report_write` tool. |

## Dispatch rules

- The lead delegates through the registry-aware wrapper
  (`scripts/arcanum-tab-dispatch`) with an explicit `workflow_id` and
  `target_id`. Raw dispatch, ad-hoc `send`, and unconstrained `--new-tab` are
  denied by agent permissions.
- Tabs are resolved by `(workflow_id, target_id, agent_id)`. A registered tab is
  reused with `--tab --queue`; a new one is created only when no valid mapping
  exists. Completion callbacks go to the one registered lead tab for that
  target.
- A phase calls an `arcanum-gateway` tool only with a `grant_id` obtained from
  `arcanum_request_capability` in the same phase. On DENY, stop. On
  REQUIRE_HUMAN_APPROVAL, report the approval ID and wait.
- Only `decision: ACCEPT` from `arcanum_verify_execution` may advance the
  workflow. REJECT returns the same task to the same specialist;
  NEEDS_VALIDATION dispatches `validate-agent`.
- Every message is self-contained: task ID, objective, scope references, and
  structured result requirements. No full conversation history is forwarded,
  and no authorization is carried in agent messages.

See [shared-rooms.md](shared-rooms.md) for the conversation model and
[security.md](security.md) for the verification rules.
