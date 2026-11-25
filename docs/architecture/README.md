# Architecture

Arcanum separates **conversation** (one canonical room per workflow/target),
**orchestration** (Maestro tabs and dispatch), **execution** (OpenCode-backed
specialists), and **authorization** (scope records, capability grants, and a
verification gate).

## Flow

```
Operator / harness / Discord bridge
        -> Maestro runtime (target-specific tabs)
        -> hackbot-lead (delegates only)
        -> recon / hunt / validate / report specialist
        -> OpenCode execution layer
        -> authorization gateway -> granted capability -> tool
        -> structured JSON result
        -> completion verification -> canonical room + task state
```

## Invariants

- `hackbot-lead` is a delegator. It does not execute phase tools or request
  grants.
- Every specialist phase must load an installed skill before it can request a
  capability.
- Internal Maestro/OpenCode sessions are metadata. The user-facing
  conversation is one canonical room keyed by `(workflow_id, target_id)`, with
  five agent-facing views of the same `room_id`.
- Agent output is untrusted. It can reference findings and tasks but cannot
  create grants, change scope, or authorize a call.
- Acknowledgments are not results. Completion is established from trusted
  runtime records and task-specific criteria, with cross-target isolation.

## Guides

| Guide | Contents |
| --- | --- |
| [agents.md](agents.md) | The five agents and their duties |
| [shared-rooms.md](shared-rooms.md) | Canonical rooms, views, and tab mapping |
| [security.md](security.md) | Scope, grants, verification, and secrets |

Implementation references: `src/arcanum/core/`, `src/arcanum/runtime/`,
`src/arcanum/permissions/`, `src/arcanum/tools/`, and
`workflows/maestro/playbooks/ARCANUM-01.md`.
