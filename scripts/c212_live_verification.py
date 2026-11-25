#!/usr/bin/env python3
"""C2.1.2 real-Maestro verification of agent-facing tab reuse.

Uses the same wrapper the Head Agent is allowed to run, inspects the actual
desktop session list before/after, and checks real transcripts and callbacks.
No duplicate tab is deleted; counts are compared against the live session list.
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
from arcanum.runtime import SPECIALISTS, TabRegistry, maestro  # noqa: E402

WRAPPER = ROOT / "scripts" / "arcanum-tab-dispatch"


def snapshot_tabs() -> dict[str, set[str]]:
    tabs = defaultdict(set)
    for session in maestro.list_tabs():
        tabs[str(session.get("agentName") or "")].add(str(session.get("tabId") or ""))
    return dict(tabs)


def wait_for_text(tab_id: str, needle: str, timeout: int = 45) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            text = "\n".join(
                str(m.get("content", "")) for m in maestro.get_transcript(tab_id)
            )
            if needle in text:
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


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


def callback_in_lead(
    lead_tab: str, target: str, task_id: str, execution_id: str,
    timeout: int = 180,
) -> bool:
    """Callbacks fire when the specialist turn completes, not at dispatch."""
    deadline = time.monotonic() + timeout
    while True:
        for item in maestro.list_lead_queue():
            if str(item.get("tabId") or "") != lead_tab:
                continue
            text = str(item.get("text") or "")
            if all(x in text for x in ("ARCANUM_COMPLETION", target, task_id, execution_id)):
                return True
        try:
            text = "\n".join(
                str(m.get("content", "")) for m in maestro.get_transcript(lead_tab)
            )
            if all(x in text for x in ("ARCANUM_COMPLETION", target, task_id, execution_id)):
                return True
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(3)


def verify_existing(path: str) -> int:
    """Re-check callbacks from a completed run without creating more tabs."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    lead = str(data["lead_tab_id"])
    results = []
    for item in data["interactions"]:
        if "callback_in_lead" not in item:
            continue
        results.append({
            "target": item["target"],
            "agent": item["agent"],
            "task_id": item["task_id"],
            "execution_id": item["execution_id"],
            "callback_in_lead": callback_in_lead(
                lead, item["target"], item["task_id"], item["execution_id"]
            ),
        })
    print(json.dumps({"workflow_id": data["workflow_id"], "callbacks": results}, indent=2))
    return 0 if all(r["callback_in_lead"] for r in results) else 1


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--verify":
        return verify_existing(sys.argv[2])
    settings = Settings()
    registry = TabRegistry(settings.tab_registry_path)
    before = snapshot_tabs()
    workflow_id = f"wf_c212_{uuid.uuid4().hex[:8]}"
    # C2.1.3: Lead tabs are target-scoped. This legacy script verifies recon
    # conversations for one target and uses that target's Lead tab.
    lead_tab = maestro.ensure_lead_tab(workflow_id, "domain-a.com", registry)
    targets = ["domain-a.com", "domain-b.com"]

    interactions: list[dict] = []
    for target in targets:
        for agent in SPECIALISTS:
            task_id = f"task_{uuid.uuid4().hex[:10]}"
            execution_id = f"exec_{uuid.uuid4().hex[:8]}"
            first = run_wrapper(
                "--workflow", workflow_id, "--target", target, "--agent", agent,
                "--prompt",
                f"C212 initial task for {target}. Reasoning only, no tools. "
                "Return findings: [] with workflow_id, target_id, authorized_target.",
                "--task-id", task_id, "--execution-id", execution_id,
                "--lead-tab", lead_tab,
            )
            correction = run_wrapper(
                "--workflow", workflow_id, "--target", target, "--agent", agent,
                "--followup", f"C212 correction for {target}: preserve the exact target_id.",
                "--task-id", task_id, "--execution-id", execution_id,
            )
            tab_id = first["tab_id"]
            visible = wait_for_text(tab_id, "C212 initial task")
            followup_visible = wait_for_text(tab_id, "C212 correction")
            interactions.append({
                "target": target,
                "agent": agent,
                "tab_id": tab_id,
                "first_created": not first["tab_reused"],
                "followup_reused": bool(correction["tab_reused"]),
                "same_tab": correction["tab_id"] == tab_id,
                "lead_tab": first["lead_tab_id"],
                "prompt_in_transcript": visible,
                "correction_in_transcript": followup_visible,
                "callback_in_lead": callback_in_lead(lead_tab, target, task_id, execution_id),
                "task_id": task_id,
                "execution_id": execution_id,
            })

        # A second task for the same specialist/target must reuse the tab.
        second = run_wrapper(
            "--workflow", workflow_id, "--target", target, "--agent", "recon-agent",
            "--prompt", f"C212 second task for {target}. Reasoning only.",
            "--task-id", f"task_{uuid.uuid4().hex[:10]}",
            "--execution-id", f"exec_{uuid.uuid4().hex[:8]}",
            "--lead-tab", lead_tab,
        )
        interactions.append({
            "target": target,
            "agent": "recon-agent",
            "tab_id": second["tab_id"],
            "second_task_reused": bool(second["tab_reused"]),
            "same_tab": second["tab_id"] == next(
                i["tab_id"] for i in interactions
                if i["target"] == target and i["agent"] == "recon-agent"
            ),
        })

    after = snapshot_tabs()
    new_tabs = {
        agent: sorted(tabs - before.get(agent, set()))
        for agent, tabs in after.items()
    }
    new_counts = {agent: len(tabs) for agent, tabs in new_tabs.items()}
    mappings = registry.all_for_workflow(workflow_id)
    events = registry.events_for_workflow(workflow_id)

    summary = {
        "workflow_id": workflow_id,
        "lead_tab_id": lead_tab,
        "lead_tab_before": before.get("hackbot-lead", set()) & {lead_tab},
        "lead_tab_created": lead_tab not in before.get("hackbot-lead", set()),
        "new_tabs_by_agent": new_counts,
        "new_tab_ids": new_tabs,
        "expected_new_tabs": {
            "hackbot-lead": 1,
            **{agent: len(targets) for agent in SPECIALISTS},
        },
        "registry_mapping_count": len(mappings),
        "interaction_count": len(interactions),
        "all_same_tab": all(i.get("same_tab", True) for i in interactions),
        "all_reused_after_first": all(
            i.get("first_created", True) and i.get("followup_reused", True)
            for i in interactions if "first_created" in i
        ),
        "all_prompts_visible": all(
            i.get("prompt_in_transcript", True) and i.get("correction_in_transcript", True)
            for i in interactions if "prompt_in_transcript" in i
        ),
        "all_callbacks_in_lead": all(
            i.get("callback_in_lead", True)
            for i in interactions if "callback_in_lead" in i
        ),
        "new_tab_events": [e for e in events if e["action"] == "create"],
        "refused_events": [e for e in events if e["action"].startswith("refuse")],
        "interactions": interactions,
    }
    print(json.dumps(summary, indent=2, default=list))

    ok = (
        summary["all_same_tab"]
        and summary["all_reused_after_first"]
        and summary["all_prompts_visible"]
        and summary["all_callbacks_in_lead"]
        and summary["registry_mapping_count"] == 1 + len(SPECIALISTS) * len(targets)
        and not summary["refused_events"]
        and new_counts.get("hackbot-lead", 0) == 1
        and all(new_counts.get(a, 0) == len(targets) for a in SPECIALISTS)
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
