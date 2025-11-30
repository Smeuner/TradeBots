import os
import asyncio
from datetime import datetime
from collections import deque
from typing import Dict, List, Optional

import psutil
import hikari
import lightbulb

# If your monitor file is named differently, adjust this import path.
from src.extensions.Background_Processes.botlogs import (
    BOT_EXECUTABLES,
    run_and_monitor_bot,
    _stop_tailer,
    startup_detected,
    ALERT_CHANNEL_ID,
    ALERT_USER_ID,
)

plugin = lightbulb.Plugin("Unified Control Panel")
plugin.add_checks(lightbulb.owner_only)

# Store last selected bot per user
last_selected: Dict[int, str] = {}   # { user_id : botname }

# Store the persistent panel message info
_panel_message_id: Optional[int] = None
_panel_update_task: Optional[asyncio.Task] = None


# -----------------------------
# Helpers for process control
# -----------------------------

def _is_owner(user_id: int) -> bool:
    # Prefer Lightbulb's configured owner IDs if present
    bot = plugin.bot
    owner_ids = getattr(bot, "owner_ids", None)
    if owner_ids:
        return user_id in owner_ids

    # Fallback: use ALERT_USER_ID from botlogs as the single allowed user
    return user_id == ALERT_USER_ID

def _match_proc_for_exe(proc: psutil.Process, exe_path: str, cwd: str) -> bool:
    exe_path = os.path.abspath(exe_path).lower()
    cwd = os.path.abspath(cwd).lower()
    exe_name = os.path.basename(exe_path)

    try:
        p_exe = (proc.info.get("exe") or "").lower()
        p_name = (proc.info.get("name") or "").lower()
        p_cwd = (proc.info.get("cwd") or "").lower()
        p_cmd = proc.info.get("cmdline") or []
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False

    # Exact exe path match
    if p_exe and os.path.abspath(p_exe).lower() == exe_path:
        return True

    # Name + cwd match
    if p_name == exe_name and p_cwd == cwd:
        return True

    # Command-line [0] match
    try:
        if p_cmd:
            cmd0 = os.path.abspath(p_cmd[0]).lower()
            if cmd0 == exe_path:
                return True
    except Exception:
        pass

    return False


def _find_matching_processes(exe_path: str, cwd: str) -> List[psutil.Process]:
    """Return a list of processes that match this exe path + cwd."""
    matches: List[psutil.Process] = []
    for proc in psutil.process_iter(["pid", "name", "exe", "cwd", "cmdline", "status"]):
        try:
            if _match_proc_for_exe(proc, exe_path, cwd):
                matches.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return matches


async def _terminate_process(proc: psutil.Process) -> str:
    try:
        if not proc.is_running():
            return "not running"
        proc.terminate()
    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
        return f"terminate error: {e}"

    for _ in range(10):  # ~5s
        if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
            return "terminated"
        await asyncio.sleep(0.5)

    try:
        proc.kill()
        for _ in range(6):  # ~3s
            if not proc.is_running():
                return "killed"
            await asyncio.sleep(0.5)
    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
        return f"kill error: {e}"

    return "killed-timeout"


# -----------------------------
# Health helpers
# -----------------------------
def _format_age(seconds: Optional[float]) -> str:
    if seconds is None:
        return "Unknown"
    if seconds < 60:
        return f"{int(seconds)}s ago"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)}m ago"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)}h ago"
    days = hours / 24
    return f"{int(days)}d ago"


def _get_bot_health(botname: str, exe_path: str) -> dict:
    """
    Simple health summary for a bot:
    - Running or not
    - CPU + RAM
    - Last log write time
    - Error file presence
    - Health level (GOOD / WARNING / CRITICAL)
    """
    cwd = os.path.dirname(exe_path)
    log_path = os.path.join(r"C:\v4logs", f"{botname}.log")
    err_path = os.path.join(r"C:\v4logs", f"{botname}.log.err")

    procs = _find_matching_processes(exe_path, cwd)
    pids = [p.pid for p in procs]
    running = bool(pids)
    startup_flag = startup_detected.get(botname, False)

    # CPU + RAM
    total_cpu = 0.0
    total_rss = 0
    for p in procs:
        try:
            total_cpu += p.cpu_percent(interval=0.0)
            total_rss += p.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    ram_mb = total_rss / (1024 * 1024) if total_rss else 0.0

    # Log file age
    log_age_seconds: Optional[float] = None
    if os.path.exists(log_path):
        try:
            mtime = os.path.getmtime(log_path)
            now_ts = datetime.now().timestamp()
            log_age_seconds = max(0.0, now_ts - mtime)
        except OSError:
            log_age_seconds = None

    # Error file check
    has_error = False
    if os.path.exists(err_path):
        try:
            if os.path.getsize(err_path) > 0:
                has_error = True
        except OSError:
            has_error = False

    # Decide health level
    if not running and not startup_flag:
        health_emoji = "🔴"
        health_text = "CRITICAL (offline)"
    elif running and (has_error or (log_age_seconds is not None and log_age_seconds > 300)):
        # 5+ minutes stale or any error -> warning
        health_emoji = "🟧"
        reasons = []
        if has_error:
            reasons.append("errors present")
        if log_age_seconds is not None and log_age_seconds > 300:
            reasons.append("log stale")
        reason_str = ", ".join(reasons) if reasons else "check logs"
        health_text = f"WARNING ({reason_str})"
    else:
        health_emoji = "🟩"
        health_text = "GOOD"

    return {
        "health_emoji": health_emoji,
        "health_text": health_text,
        "running": running or startup_flag,
        "pids": pids,
        "cpu": round(total_cpu, 1),
        "ram_mb": round(ram_mb, 1),
        "log_age_seconds": log_age_seconds,
        "log_age_str": _format_age(log_age_seconds),
        "has_error": has_error,
    }


def _read_log_tail(log_path: str, max_lines: int = 30) -> str:
    """
    Read the last `max_lines` lines from the log file efficiently.
    Returns a string (may be empty if file missing or unreadable).
    """
    if not os.path.exists(log_path):
        return ""

    try:
        dq = deque(maxlen=max_lines)
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                dq.append(line.rstrip("\n"))
        return "\n".join(dq)
    except OSError:
        return ""


# -----------------------------
# UI builder (simple buttons)
# -----------------------------
def _build_panel_ui(rest: hikari.api.RESTClient) -> tuple:
    # Determine which bot (if any) to show as "selected" in the menu
    selected_for_panel: Optional[str] = None
    if ALERT_USER_ID in last_selected:
        selected_for_panel = last_selected[ALERT_USER_ID]
    elif last_selected:
        selected_for_panel = next(iter(last_selected.values()))

    description = "Select a bot from the menu below, then use the buttons to Start, Stop, Restart, or view the log tail."

    if selected_for_panel:
        description += f"\n\nCurrently selected: **{selected_for_panel}**"

    embed = (
        hikari.Embed(
            title="Bot Control Panel",
            description=description,
            color=0x2F3136,
        )
        .set_footer("Owner-only controls.")
    )

    # Row 1: select menu
    row_select = rest.build_message_action_row()
    menu = row_select.add_text_menu(
        "select_bot",                 # custom_id (positional-only!)
        placeholder="Select a bot",
        min_values=1,
        max_values=1,
    )

    for name in BOT_EXECUTABLES.keys():
        opt = menu.add_option(name, name)
        if selected_for_panel and selected_for_panel == name:
            opt.set_is_default(True)

    # Row 2: buttons
    row_btn = rest.build_message_action_row()
    row_btn.add_interactive_button(
        hikari.ButtonStyle.SUCCESS, "btn_start", label="Start"
    )
    row_btn.add_interactive_button(
        hikari.ButtonStyle.DANGER, "btn_stop", label="Stop"
    )
    row_btn.add_interactive_button(
        hikari.ButtonStyle.PRIMARY, "btn_restart", label="Restart"
    )
    row_btn.add_interactive_button(
        hikari.ButtonStyle.SECONDARY, "btn_logtail", label="View Log Tail"
    )

    return embed, row_select, row_btn

async def _create_or_update_panel_message(rest: hikari.api.RESTClient) -> None:
    global _panel_message_id

    embed, row_select, row_btn = _build_panel_ui(rest)

    if _panel_message_id is None:
        msg = await rest.create_message(
            ALERT_CHANNEL_ID,
            embed=embed,
            components=[row_select, row_btn],
        )
        _panel_message_id = msg.id
    else:
        try:
            await rest.edit_message(
                ALERT_CHANNEL_ID,
                _panel_message_id,
                embed=embed,
                components=[row_select, row_btn],
            )
        except hikari.NotFoundError:
            msg = await rest.create_message(
                ALERT_CHANNEL_ID,
                embed=embed,
                components=[row_select, row_btn],
            )
            _panel_message_id = msg.id


async def _panel_update_loop(rest: hikari.api.RESTClient) -> None:
    while True:
        try:
            await _create_or_update_panel_message(rest)
        except Exception:
            # optional: add logging here
            pass
        await asyncio.sleep(10)  # refresh interval


# ----------------------------------------
# Startup listener → start background loop
# ----------------------------------------
@plugin.listener(hikari.StartedEvent)
async def on_started(event: hikari.StartedEvent) -> None:
    global _panel_update_task

    rest = event.app.rest

    if _panel_update_task is None or _panel_update_task.done():
        _panel_update_task = asyncio.create_task(_panel_update_loop(rest))


# ----------------------------------------
# /controlpanel → optional manual panel
# ----------------------------------------
@plugin.command
@lightbulb.command("controlpanel", "Create a copy of the control panel (ephemeral)")
@lightbulb.implements(lightbulb.SlashCommand)
async def controlpanel(ctx: lightbulb.Context) -> None:
    embed, row_select, row_btn = _build_panel_ui(ctx.app.rest)

    await ctx.respond(
        embed=embed,
        components=[row_select, row_btn],
        flags=hikari.MessageFlag.EPHEMERAL,
    )


# -----------------------------
# Dropdown selection handler (silent)
# -----------------------------
@plugin.listener(hikari.InteractionCreateEvent)
async def on_select(event: hikari.InteractionCreateEvent) -> None:
    if not isinstance(event.interaction, hikari.ComponentInteraction):
        return

    if event.interaction.custom_id != "select_bot":
        return

    user = event.interaction.user
    if not _is_owner(user.id):
        # still return error for non-owners
        await event.interaction.create_initial_response(
            hikari.ResponseType.MESSAGE_CREATE,
            content="❌ You are not allowed to use this control panel.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    # update internal state, but do NOT display anything
    botname = event.interaction.values[0]
    last_selected[user.id] = botname

    # silent acknowledgement
    try:
        await event.interaction.create_initial_response(
            hikari.ResponseType.DEFERRED_MESSAGE_UPDATE
        )
    except hikari.NotFoundError:
        # in rare cases Discord requires a follow-up
        pass

# -----------------------------
# Button handler
# -----------------------------
@plugin.listener(hikari.InteractionCreateEvent)
async def on_button(event: hikari.InteractionCreateEvent) -> None:
    if not isinstance(event.interaction, hikari.ComponentInteraction):
        return

    cid = event.interaction.custom_id
    user = event.interaction.user
    app = event.app

    # Ignore non-control buttons
    if cid not in {"btn_start", "btn_stop", "btn_restart", "btn_logtail"}:
        return

    # Owner-only guard for all control actions
    if not _is_owner(user.id):
        await event.interaction.create_initial_response(
            hikari.ResponseType.MESSAGE_CREATE,
            content="❌ You are not allowed to use this control panel.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    # Must have selected a bot first
    if user.id not in last_selected:
        await event.interaction.create_initial_response(
            hikari.ResponseType.MESSAGE_CREATE,
            content="⚠️ Select a bot first from the dropdown.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    botname = last_selected[user.id]
    exe_path = BOT_EXECUTABLES.get(botname)
    if not exe_path:
        await event.interaction.create_initial_response(
            hikari.ResponseType.MESSAGE_CREATE,
            content=f"❌ Bot `{botname}` not found in configuration.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    cwd = os.path.dirname(exe_path)

    # STOP
    if cid == "btn_stop":
        await event.interaction.create_initial_response(
            hikari.ResponseType.MESSAGE_CREATE,
            content=f"🛑 Stopping `{botname}`…",
            flags=hikari.MessageFlag.EPHEMERAL,
        )

        matches = _find_matching_processes(exe_path, cwd)

        if not matches:
            msg = f"⚠️ No running process found for `{botname}`."
        else:
            results = []
            for p in matches:
                res = await _terminate_process(p)
                results.append(f"PID {p.pid}: {res}")
            msg = f"🛑 Stopped `{botname}` → " + "; ".join(results)

        _stop_tailer(botname)
        startup_detected[botname] = False

        # Ephemeral follow-up only, no public ping
        await event.interaction.create_followup_message(
            msg,
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    # START
    if cid == "btn_start":
        await event.interaction.create_initial_response(
            hikari.ResponseType.MESSAGE_CREATE,
            content=f"▶️ Starting `{botname}`…",
            flags=hikari.MessageFlag.EPHEMERAL,
        )

        asyncio.create_task(run_and_monitor_bot(app, botname, exe_path))
        return

    # RESTART
    if cid == "btn_restart":
        await event.interaction.create_initial_response(
            hikari.ResponseType.MESSAGE_CREATE,
            content=f"🔁 Restarting `{botname}`…",
            flags=hikari.MessageFlag.EPHEMERAL,
        )

        matches = _find_matching_processes(exe_path, cwd)
        for p in matches:
            await _terminate_process(p)

        _stop_tailer(botname)
        startup_detected[botname] = False

        # small delay so your monitor can pick up the new process cleanly
        await asyncio.sleep(1.5)
        asyncio.create_task(run_and_monitor_bot(app, botname, exe_path))
        return

    # LOG TAIL
    if cid == "btn_logtail":
        log_path = os.path.join(r"C:\v4logs", f"{botname}.log")
        tail = _read_log_tail(log_path, max_lines=30)

        if not tail:
            content = f"📄 No log data available for `{botname}`."
        else:
            # 2000 char Discord limit; keep some margin
            if len(tail) > 1800:
                tail = tail[-1800:]
                content = f"```log\n{tail}\n```\n... (truncated)"
            else:
                content = f"```log\n{tail}\n```"

        await event.interaction.create_initial_response(
            hikari.ResponseType.MESSAGE_CREATE,
            content=content,
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return


def load(bot: lightbulb.BotApp) -> None:
    bot.add_plugin(plugin)


def unload(bot: lightbulb.BotApp) -> None:
    bot.remove_plugin(plugin)
