# src/extensions/Commands_Owner/restart_all.py
import asyncio
import os
import psutil
import hikari
import lightbulb
from typing import List

from src.extensions.Background_Processes.botlogs import (
    BOT_EXECUTABLES,
    startup_detected,
    run_and_monitor_bot,
    _stop_tailer,
    _schedule_update,   # if you don't have this, import _update_embed as _schedule_update
)

plugin = lightbulb.Plugin("Restart All Bots")

# -----------------------------
# Helpers (kept lightweight)
# -----------------------------
def _match_proc_for_exe(proc: psutil.Process, exe_path: str, cwd: str) -> bool:
    """Match by exe path/name + working directory to avoid false positives."""
    try:
        pexe = proc.exe() if proc.exe() else ""
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False

    try:
        pcwd = proc.cwd() if proc.cwd() else ""
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pcwd = ""

    # exe filename must match AND cwd must match (how your duplicates are avoided)
    want_name = os.path.basename(exe_path)
    got_name = os.path.basename(pexe) if pexe else ""
    return got_name.lower() == want_name.lower() and os.path.normcase(pcwd) == os.path.normcase(cwd)


async def _terminate_process(proc: psutil.Process) -> str:
    """Try terminate() then kill() with short waits; return an action string."""
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


def _delete_session_json(cwd: str) -> str:
    """Delete <cwd>/session.json if it exists; return a short status string."""
    session_path = os.path.join(cwd, "session.json")
    try:
        if os.path.exists(session_path):
            os.remove(session_path)
            return "session.json deleted"
        return "no session.json"
    except Exception as e:
        return f"delete error: {e.__class__.__name__}"


# --------------------------------------
# /restartall (now with wipe flag)
# --------------------------------------
@plugin.command
@lightbulb.option(
    "wipe",
    "Delete session.json in each bot folder before relaunch?",
    type=bool,
    required=False,
    default=False,
)
@lightbulb.option(
    "stagger",
    "Seconds to wait between each relaunch.",
    type=float,
    required=False,
    default=0.75,
)
@lightbulb.command(
    "restartall",
    "Stop all configured bots and relaunch them (optionally wipe session.json first)",
)
@lightbulb.implements(lightbulb.SlashCommand)
async def restartall(ctx: lightbulb.Context) -> None:
    total = len(BOT_EXECUTABLES)
    do_wipe: bool = bool(ctx.options.wipe)
    stagger: float = max(0.0, float(ctx.options.stagger))

    await ctx.respond(
        f"🔁 Restarting **{total}** bots"
        + (" with session wipe…" if do_wipe else "…"),
        flags=hikari.MessageFlag.EPHEMERAL,
    )

    # -------- Stop phase --------
    stop_report: List[str] = []
    for name, exe_path in BOT_EXECUTABLES.items():
        cwd = os.path.dirname(exe_path)
        matches: List[psutil.Process] = []
        for proc in psutil.process_iter(["pid", "name", "exe", "cwd", "cmdline", "status"]):
            try:
                if _match_proc_for_exe(proc, exe_path, cwd):
                    matches.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if not matches:
            stop_report.append(f"{name}: not running")
            _stop_tailer(name)
            startup_detected[name] = False
            continue

        results = []
        for p in matches:
            res = await _terminate_process(p)
            results.append(f"PID {p.pid}: {res}")
        stop_report.append(f"{name}: " + "; ".join(results))

        _stop_tailer(name)
        startup_detected[name] = False

    # Refresh status embed after stop
    try:
        await _schedule_update(ctx.app, debounce_seconds=0)
    except Exception:
        pass

    # -------- Optional wipe phase --------
    wipe_report: List[str] = []
    if do_wipe:
        for name, exe_path in BOT_EXECUTABLES.items():
            cwd = os.path.dirname(exe_path)
            wipe_report.append(f"{name}: {_delete_session_json(cwd)}")

    # -------- Start phase (staggered) --------
    launched = 0
    for name, path in BOT_EXECUTABLES.items():
        # Reattach monitors + tailers using your existing runner
        asyncio.create_task(run_and_monitor_bot(ctx.app, name, path))
        launched += 1
        if stagger > 0:
            await asyncio.sleep(stagger)

    # Summaries (trim to keep ephemeral reply readable)
    def _brief(lines: List[str], n: int = 10) -> str:
        return " | ".join(lines[:n]) + (" …" if len(lines) > n else "")

    parts = [
        "✅ Restart complete.",
        f"Launched: **{launched}/{total}**",
        f"Stops: {_brief(stop_report)}",
    ]
    if do_wipe:
        parts.append(f"Wipe:  {_brief(wipe_report)}")

    await ctx.respond("\n".join(parts), flags=hikari.MessageFlag.EPHEMERAL)


def load(bot: lightbulb.BotApp):
    bot.add_plugin(plugin)


def unload(bot: lightbulb.BotApp):
    bot.remove_plugin(plugin)
