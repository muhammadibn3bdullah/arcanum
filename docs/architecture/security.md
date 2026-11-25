# Security model

## Trust boundary

Agent messages, tool output, findings, and transcripts are **untrusted data**.
They may carry identifiers and references but never authorization. Scope is
resolved from trusted records by the Scope Manager / Dynamic Permission
Manager, and every tool call must pass the authorization gateway.

## Scope and capability grants

- A scope declaration records domain, operator, allowed operations, and
  whether active testing is authorized.
- Before a phase calls a tool, it calls `arcanum_request_capability` with the
  task type, capability, risk, and target. The result is ALLOW with a
  single-phase `grant_id`, DENY, or REQUIRE_HUMAN_APPROVAL with an approval ID.
- Grants are never forged, reused, or derived from agent text. A DENY stops the
  phase; an approval requirement waits for a human decision.
- Passive recon is the default. Injection, SSRF, auth-boundary tests, and
  cross-user validation must request HIGH/CRITICAL risk and pass the HITL gate.

## Completion verification

`arcanum_verify_execution` accepts a phase only when trusted runtime evidence
satisfies task-specific criteria, including:

- a structured JSON result (not an acknowledgment, marker, or refusal);
- matching task, execution, workflow, and target identifiers;
- trusted agent identity consistent with the dispatch acknowledgment and the
  expected specialist;
- required structured fields/values, actions, evidence references, and (when
  the task demands it) tool execution records with grants;
- transcript evidence, minimum assistant messages, and cross-target isolation.

Outcomes: `ACCEPT` advances the workflow, `REJECT` re-dispatches the same task
to the same specialist, and `NEEDS_VALIDATION` dispatches `validate-agent`.
A finding can be marked CONFIRMED only with proof-gate and cross-user evidence.
Relevant tests: `tests/unit/test_completion_verification.py`.

## Local data and secrets

- Runtime state is SQLite under `~/.arcanum` (or `ARCANUM_HOME`): tasks,
  findings, scope declarations, audit log, approvals, and tab registry.
- `.env` is gitignored; `.env.example` contains placeholders only. Dashboard
  (`DASHBOARD_TOKEN`) and ingest (`INGEST_TOKEN`) tokens, Discord bot tokens,
  and provider keys must never be committed.
- The dashboard binds to localhost by default. Do not expose it without
  authentication and TLS in front of it.

## Authorized use

Arcanum is for systems you own or are explicitly authorized to test. Keep
activity inside the recorded scope; use lab/test accounts for cross-user
validation; stop on denial or missing approval.
