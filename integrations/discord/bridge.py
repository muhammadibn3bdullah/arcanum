"""Discord <-> Maestro bridge (Phase 4).

An allowed Discord user chats with a Maestro agent in a channel. Messages are
forwarded into the registered central Lead tab with
`maestro-cli dispatch <agent> "<message>" --tab <lead-tab-id> --queue` when a
workflow tab exists; `maestro-cli send` is only the no-workflow fallback.

Safety: allowlists are mandatory — no token, no run; no allowed users/channels,
no run. Use this only with agents working inside an authorized scope.
"""

from __future__ import annotations

import asyncio
import fnmatch
import os
import re
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path

import discord

ARCANUM_ROOT = os.environ.get(
    "ARCANUM_ROOT", str(Path.home() / "Projects" / "arcanum")
)
if ARCANUM_ROOT not in sys.path:
    sys.path.insert(0, ARCANUM_ROOT)

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
CLI = os.environ.get("MAESTRO_CLI", str(Path.home() / ".local/bin/maestro-cli"))
AGENT = os.environ.get("MAESTRO_AGENT", "hackbot-lead")
ALLOWED_USERS = {
    u.strip() for u in os.environ.get("ALLOWED_USER_IDS", "").split(",") if u.strip()
}
ALLOWED_CHANNELS = {
    c.strip() for c in os.environ.get("ALLOWED_CHANNEL_IDS", "").split(",") if c.strip()
}
TIMEOUT = int(os.environ.get("AGENT_TIMEOUT", "300"))
HARNESS_DB = Path(
    os.environ.get(
        "HARNESS_DB",
        str(Path(__file__).resolve().parent.parent / "harness" / "hackbots.db"),
    )
)
HUNT_WORDS = re.compile(
    r"\b(hunt|scan|recon|fingerprint|enumerate|attack|exploit|test|probe|crawl)\b",
    re.I,
)
HOST_RE = re.compile(r"(?:https?://)?([a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+)", re.I)


def _host(value: str) -> str:
    return value.strip().lower().split("://")[-1].split("/")[0].split(":")[0]


def _matches(patterns: str, host: str) -> bool:
    for p in re.split(r"[,;\n\r ]+", patterns or ""):
        p = p.strip()
        if not p:
            continue
        if fnmatch.fnmatch(host, p) or fnmatch.fnmatch(host, p.lstrip("*.")):
            return True
        if p.startswith("*.") and host.endswith(p[1:]):
            return True
        if p in host or host.endswith("." + p):
            return True
    return False


def scope_gate(content: str) -> str | None:
    """Refuse hunt-like messages whose hosts are not in a recorded authorized scope."""
    if not HUNT_WORDS.search(content):
        return None
    hosts = {_host(m) for m in HOST_RE.findall(content)}
    hosts = {h for h in hosts if "." in h and not h.endswith((".md", ".py", ".js", ".txt", ".sh"))}
    if not hosts:
        return None
    if not HARNESS_DB.exists():
        return "🚫 Blocked: harness DB not found; no authorized scope can be verified."
    con = sqlite3.connect(HARNESS_DB)
    con.row_factory = sqlite3.Row
    try:
        targets = [dict(r) for r in con.execute("SELECT * FROM targets").fetchall()]
    finally:
        con.close()
    if not targets:
        return (
            "🚫 Blocked: no authorized targets are recorded in the harness. "
            "Add the target with written authorization in the dashboard Targets page first."
        )
    for h in sorted(hosts):
        allowed = any(
            _matches(t["in_scope"], h) and not _matches(t["out_of_scope"], h)
            for t in targets
        )
        if not allowed:
            return (
                f"🚫 Blocked: `{h}` is not inside any recorded authorized scope "
                "(or is explicitly out of scope). Record it in the dashboard with "
                "written authorization before hunting."
            )
    return None

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


def resolve_message_target(content: str) -> str:
    """Resolve a target from trusted scope data; never invent one."""
    try:
        from arcanum.config.settings import Settings
        from arcanum.memory.database import Database
        from arcanum.security.scope_manager import ScopeManager

        settings = Settings()
        db = Database(settings.db_path)
        domains = sorted(set(ScopeManager(db, settings).extract_domains(content)))
        db.close()
        if len(domains) == 1:
            return domains[0]
    except Exception:
        pass
    return ""


def publish_to_canonical_room(content: str, target: str, message_id: str) -> str:
    """Publish a Discord message once into the target's canonical shared room."""
    if not target:
        return ""
    try:
        from arcanum.config.settings import Settings
        from arcanum.core.rooms import RoomMessageType
        from arcanum.runtime import rooms

        settings = Settings()
        return rooms.publish_message(
            "discord", target, "hackbot-lead", RoomMessageType.REVIEW,
            content, idempotency_key=f"discord:{message_id}", settings=settings,
        ).room_id
    except Exception:
        return ""


def lead_delivery_args(content: str, target: str = "") -> list[str]:
    """Deliver chat into the registered Lead tab for the resolved target."""
    if target:
        try:
            from arcanum.runtime.maestro import latest_lead_tab_for_target

            lead_tab = latest_lead_tab_for_target(target)
            if lead_tab:
                return ["dispatch", "--tab", lead_tab, "--queue", AGENT, content]
        except Exception:
            pass
    return ["send", AGENT, content]


def run_cli(args: list[str]) -> str:
    try:
        result = subprocess.run(
            [CLI, *args],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
        output = (result.stdout or "").strip()
        if result.returncode != 0:
            output = (output + "\n" + (result.stderr or "")).strip()
        return output or f"(no output, exit {result.returncode})"
    except subprocess.TimeoutExpired:
        return f"ERROR: agent timed out after {TIMEOUT}s"
    except Exception as exc:
        return f"ERROR: {exc}"


def arcanum_runtime():
    """Import the Arcanum runtime API lazily so the bridge still runs without it."""
    from arcanum import runtime
    return runtime


def arcanum_approve(approval_id: str, operator: str) -> str:
    result = arcanum_runtime().approve_pending(approval_id, operator)
    grant = result.get("grant_id") or "none"
    return f"Approval `{approval_id}`: {result['decision']} | grant `{grant}`"


def arcanum_deny(approval_id: str, operator: str, reason: str) -> str:
    arcanum_runtime().deny_pending(approval_id, operator, reason)
    return f"Approval `{approval_id}`: DENIED"


def arcanum_chat(objective: str, operator: str) -> str | None:
    result = arcanum_runtime().handle_chat(objective, operator)
    if result is None:
        return None
    return (
        f"Arcanum phase `{result['task_id']}` | {result['decision']} | "
        f"allowed={result['allowed']} | {result['reason']}"
    )


def arcanum_phase(skill: str, capability: str, target: str,
                  operation: str, task_type: str, operator: str) -> str:
    result = arcanum_runtime().run_phase(
        objective=f"Assess {target} via {skill}",
        skill_id=skill,
        capability=capability,
        operation=operation,
        target=target,
        operator=operator,
        task_type=task_type,
    )
    return (
        f"Task `{result['task_id']}` | {result['decision']} | "
        f"allowed={result['allowed']} | {result['reason']}"
    )


def allowed(message: discord.Message) -> bool:
    if message.author.bot:
        return False
    if ALLOWED_USERS and str(message.author.id) not in ALLOWED_USERS:
        return False
    if ALLOWED_CHANNELS and str(message.channel.id) not in ALLOWED_CHANNELS:
        return False
    return True


@client.event
async def on_ready() -> None:
    print(f"Logged in as {client.user} (agent={AGENT})", flush=True)


@client.event
async def on_message(message: discord.Message) -> None:
    if not allowed(message):
        return
    content = message.content.strip()
    if not content:
        return

    if content.startswith("!help"):
        await message.reply(
            "Send any message to talk to the Maestro agent.\n"
            "`!status` — Auto Run status\n"
            "`!agent <name>` — show current agent\n"
            "`!pending` — list approvals awaiting a human\n"
            "`!approve <id>` / `!deny <id> [reason]` — decide an approval\n"
            "`!phase <skill> <capability> <target> [operation] [task-type]` — "
            "run one deterministic Arcanum phase\n"
            "Only allowlisted users/channels are served."
        )
        return
    if content.startswith("!status"):
        out = await asyncio.to_thread(
            run_cli, ["auto-run-status", "--agent", AGENT]
        )
        await message.reply(f"```{out[:1900]}```")
        return
    if content.startswith("!agent"):
        await message.reply(f"Current agent: `{AGENT}`")
        return
    if content.startswith("!pending"):
        out = await asyncio.to_thread(arcanum_runtime().format_pending)
        await message.reply(f"```{out[:1900]}```")
        return
    if content.startswith("!approve "):
        approval_id = content.split(maxsplit=1)[1].strip()
        out = await asyncio.to_thread(
            arcanum_approve, approval_id, str(message.author)
        )
        await message.reply(out)
        return
    if content.startswith("!deny "):
        parts = content.split(maxsplit=2)
        approval_id = parts[1] if len(parts) > 1 else ""
        reason = parts[2] if len(parts) > 2 else ""
        if not approval_id:
            await message.reply("Usage: `!deny <approval_id> [reason]`")
            return
        out = await asyncio.to_thread(
            arcanum_deny, approval_id, str(message.author), reason
        )
        await message.reply(out)
        return
    if content.startswith("!phase "):
        parts = content.split()
        # !phase <skill> <capability> <target> [operation] [task-type]
        if len(parts) < 4:
            await message.reply(
                "Usage: `!phase <skill> <capability> <target> [operation] [task-type]`"
            )
            return
        skill, capability, target = parts[1], parts[2], parts[3]
        operation = parts[4] if len(parts) > 4 else "read"
        task_type = parts[5] if len(parts) > 5 else "general"
        blocked = scope_gate(content)
        if blocked:
            await message.reply(blocked)
            return
        out = await asyncio.to_thread(
            arcanum_phase, skill, capability, target, operation, task_type,
            str(message.author),
        )
        await message.reply(out)
        return

    blocked = scope_gate(content)
    if blocked:
        await message.reply(blocked)
        return

    await message.channel.send("⏳ Working…")
    out = None
    # Normal chat MUST go to Maestro so hackbot-lead can dispatch visible
    # specialist tabs. The Python runtime path is opt-in via !phase only.
    if os.environ.get("ARCANUM_CHAT_ROUTING", "0") == "1":
        out = await asyncio.to_thread(arcanum_chat, content, str(message.author))
    if out is None:
        target = resolve_message_target(content)
        room_id = publish_to_canonical_room(content, target, str(message.id))
        out = await asyncio.to_thread(run_cli, lead_delivery_args(content, target))
        if room_id:
            out = f"[room {room_id}]\n{out}"
    # Trim to Discord's 2000-char limit, keep the tail if too long.
    if len(out) > 1900:
        out = "…(truncated)\n" + out[-1850:]
    await message.reply(out)


def check() -> None:
    print("token:", "set" if TOKEN else "MISSING")
    print("maestro cli:", CLI, "exists:", Path(CLI).exists())
    print("agent:", AGENT)
    print("allowed users:", sorted(ALLOWED_USERS) or "NONE (will deny all)")
    print("allowed channels:", sorted(ALLOWED_CHANNELS) or "NONE (any channel)")
    ok = bool(TOKEN) and Path(CLI).exists() and bool(ALLOWED_USERS)
    print("READY" if ok else "NOT READY — see missing items above")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    if "--check" in sys.argv:
        check()
    else:
        if not TOKEN:
            sys.exit("ERROR: set DISCORD_BOT_TOKEN")
        if not Path(CLI).exists():
            sys.exit(f"ERROR: maestro-cli not found: {CLI}")
        client.run(TOKEN)
