#!/usr/bin/env python3
"""C2.1.3 real-Maestro verification: five target-scoped tabs per target.

Inspects the actual desktop session list and transcripts. No historical tabs
are deleted; counts are computed as new tabs for this workflow.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from arcanum.config.settings import Settings  # noqa: E402
from arcanum.memory.database import Database  # noqa: E402
from arcanum.runtime import TabRegistry, maestro  # noqa: E402

WRAPPER = ROOT / "scripts" / "arcanum-tab-dispatch"
ALL_AGENTS = ["hackbot-lead", "recon-agent", "hunt-agent", "validate-agent", "report-agent"]
SPECIALISTS = ALL_AGENTS[1:]


def snapshot_tabs() -> dict[str, set[str]]:
    tabs = defaultdict(set)
    for session in maestro.list_tabs():
        tabs[str(session.get("agentName") or "")].add(str(session.get("tabId") or ""))
    return dict(tabs)


def run_wrapper(*args: str) -> dict:
    result = subprocess.run(
        [str(WRAPPER), *args], capture_output=True, text=True, timeout=180, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"wrapper failed: {result.stdout} {result.stderr}")
    data = json.loads(result.stdout)
    if not data.get("success"):
        raise RuntimeError(f"wrapper returned failure: {data}")
    return data


def wait_for_text(tab_id: str, needle: str, timeout: int = 45) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            text = "\n".join(str(m.get("content", "")) for m in maestro.get_transcript(tab_id))
            if needle in text:
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def main() -> int:
    settings = Settings()
    registry = TabRegistry(settings.tab_registry_path)
    before = snapshot_tabs()
    workflow_id = f"wf_c213_{uuid.uuid4().hex[:8]}"
    targets = ["domain-a.com", "domain-b.com"]

    lead_tabs: dict[str, str] = {}
    tab_map: dict[tuple[str, str], str] = {}
    interactions: list[dict] = []

    for target in targets:
        lead_tabs[target] = maestro.ensure_lead_tab(workflow_id, target, registry)
        tab_map[(target, "hackbot-lead")] = lead_tabs[target]

        for agent in ALL_AGENTS:
            task_id = f"task_{uuid.uuid4().hex[:10]}"
            execution_id = f"exec_{uuid.uuid4().hex[:8]}"
            common = [
                "--workflow", workflow_id, "--target", target, "--agent", agent,
                "--task-id", task_id, "--execution-id", execution_id,
                "--lead-tab", lead_tabs[target],
            ]
            initial_args = list(common) + [
                "--prompt",
                f"C213 initial task for {target} on {agent}. Reasoning only. "
                "Return findings: [] with workflow_id, target_id, authorized_target.",
            ]
            if agent == "hackbot-lead":
                initial_args.append("--no-callback")
            first = run_wrapper(*initial_args)
            tab_id = first["tab_id"]
            tab_map[(target, agent)] = tab_id

            followup = run_wrapper(
                *common, "--followup",
                f"C213 correction for {target} on {agent}: preserve target_id exactly.",
            )
            prompt_visible = wait_for_text(tab_id, "C213 initial task")
            correction_visible = wait_for_text(tab_id, "C213 correction")

            callback_lead = ""
            if agent in SPECIALISTS:
                callback_lead = maestro.find_callback_tab(
                    task_id, execution_id,
                    lead_tab_id=lead_tabs[target],
                    target_id=target,
                    workflow_id=workflow_id,
                    registry=registry,
                    timeout=120,
                ) or ""
                wrong_lead = None  # checked after every target's Lead tab exists

            interactions.append({
                "target": target,
                "agent": agent,
                "tab_id": tab_id,
                "lead_tab": lead_tabs[target],
                "lead_is_target_specific": True,  # verified after all targets are created
                "first_created": not first["tab_reused"],
                "followup_reused": bool(followup["tab_reused"]),
                "same_tab": followup["tab_id"] == tab_id,
                "prompt_visible": prompt_visible,
                "correction_visible": correction_visible,
                "callback_lead": callback_lead,
                "cross_target_callback_rejected": True,  # verified after all Lead tabs exist
                "task_id": task_id,
                "execution_id": execution_id,
            })

    # Dynamic third target: each agent gets one new tab when first assigned.
    third = "domain-c.com"
    lead_tabs[third] = maestro.ensure_lead_tab(workflow_id, third, registry)
    tab_map[(third, "hackbot-lead")] = lead_tabs[third]
    for agent in ALL_AGENTS:
        args = [
            "--workflow", workflow_id, "--target", third, "--agent", agent,
            "--prompt", f"C213 dynamic target task for {third} on {agent}. Reasoning only.",
            "--lead-tab", lead_tabs[third],
        ]
        if agent == "hackbot-lead":
            args.append("--no-callback")
        result = run_wrapper(*args)
        tab_map[(third, agent)] = result["tab_id"]

    distinct_leads = len(set(lead_tabs.values())) == len(lead_tabs)
    for item in interactions:
        item["lead_is_target_specific"] = distinct_leads
        if item["agent"] in SPECIALISTS:
            other_target = next(t for t in targets if t != item["target"])
            wrong = maestro.find_callback_tab(
                item["task_id"], item["execution_id"],
                lead_tab_id=lead_tabs[other_target],
                target_id=item["target"],
                workflow_id=workflow_id,
                registry=registry,
                timeout=1,
            )
            item["cross_target_callback_rejected"] = wrong is None

    after = snapshot_tabs()
    new_tabs = {
        agent: sorted(tabs - before.get(agent, set()))
        for agent, tabs in after.items()
    }
    new_counts = {agent: len(tabs) for agent, tabs in new_tabs.items()}
    registrations = registry.active_for_workflow(workflow_id)

    db = Database(settings.db_path)
    exec_targets = {
        row[0] for row in db.conn.execute(
            "SELECT DISTINCT target_id FROM executions WHERE workflow_id=?", (workflow_id,)
        ).fetchall()
    }
    db.close()

    # Lead transcript isolation: each Lead tab must only mention its own target.
    lead_isolation = {}
    for target, lead in lead_tabs.items():
        text = "\n".join(str(m.get("content", "")) for m in maestro.get_transcript(lead))
        other_targets = [t for t in lead_tabs if t != target]
        lead_isolation[target] = {
            "own_target_present": target in text,
            "other_targets_absent": all(t not in text for t in other_targets),
        }

    summary = {
        "workflow_id": workflow_id,
        "lead_tabs_by_target": lead_tabs,
        "tab_map": {f"{t}|{a}": tid for (t, a), tid in tab_map.items()},
        "new_tabs_by_agent": new_counts,
        "new_tab_ids": new_tabs,
        "expected_new_tabs_per_agent": len(targets) + 1,
        "active_registrations": len(registrations),
        "expected_registrations": (len(targets) + 1) * len(ALL_AGENTS),
        "all_same_tab": all(i["same_tab"] for i in interactions),
        "all_reused_after_first": all(
            i["first_created"] and i["followup_reused"] for i in interactions
        ),
        "all_prompts_visible": all(
            i["prompt_visible"] and i["correction_visible"] for i in interactions
        ),
        "all_lead_tabs_target_specific": all(i["lead_is_target_specific"] for i in interactions),
        "all_callbacks_on_own_lead": all(
            i["callback_lead"] == i["lead_tab"] for i in interactions
            if i["agent"] in SPECIALISTS
        ),
        "all_cross_target_callbacks_rejected": all(
            i["cross_target_callback_rejected"] for i in interactions
            if i["agent"] in SPECIALISTS
        ),
        "lead_transcript_isolation": lead_isolation,
        "shared_state_targets": sorted(exec_targets),
        "interactions": interactions,
    }
    print(json.dumps(summary, indent=2, default=list))

    ok = (
        summary["all_same_tab"]
        and summary["all_reused_after_first"]
        and summary["all_prompts_visible"]
        and summary["all_lead_tabs_target_specific"]
        and summary["all_callbacks_on_own_lead"]
        and summary["all_cross_target_callbacks_rejected"]
        and summary["active_registrations"] == summary["expected_registrations"]
        and all(v["own_target_present"] and v["other_targets_absent"]
                for v in lead_isolation.values())
        and all(new_counts.get(a, 0) == 3 for a in ALL_AGENTS)
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
