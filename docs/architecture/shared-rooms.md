# Shared rooms and sessions

## Canonical room

Arcanum owns one canonical domain conversation per authorized target within a
workflow:

```
(workflow_id, target_id) -> room_id
```

`room_id` is derived deterministically from the workflow ID and the canonical
domain (`src/arcanum/runtime/rooms.py`). Creating a room requires an existing
authorized scope record; an unknown target is rejected. Room creation and the
creation of agent views happen in one immediate SQLite transaction.

## Five views, one history

All five agents (`hackbot-lead`, `recon-agent`, `hunt-agent`, `validate-agent`,
`report-agent`) get a view of the **same** `room_id`. Messages are published
once to that room and read back from the same history. Sender identity is
preserved and is not merged into another agent's view. `hackbot-lead` is the
moderator.

## Internal sessions are metadata

Maestro keeps execution sessions/tabs, and OpenCode performs specialist work,
but these are **not** additional user-facing conversations. A tab ID proves
dispatch initiation only. Tab identity, transcript cursor, execution ID, and
callback IDs are recorded as correlation metadata on canonical messages and in
the tab registry.

## Tab registry

The tab registry maps `(workflow_id, target_id, agent_id)` to a tab ID,
including for the lead agent. Key properties:

- Registered tabs are validated against trusted Maestro session metadata
  before reuse; stale mappings are replaced.
- Caller-supplied tabs that do not match the registry are refused.
- Completion callbacks must arrive on the target's registered lead tab with a
  payload carrying the same workflow/target/task/execution IDs. Cross-target
  callbacks are rejected.
- Follow-ups go to the same registered tab, never a newly created one.

## Isolation

Cross-target isolation is enforced at multiple layers: room identity, tab
registry keys, callback correlation, and the completion verification gate.
Stale structured results from an earlier task in a persistent tab are rejected
by transcript cursor and task/execution ID matching.

Relevant tests: `tests/unit/test_domain_rooms.py`,
`tests/unit/test_tab_registry.py`, and `tests/unit/test_tab_routing.py`.
