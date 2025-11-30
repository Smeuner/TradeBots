import asyncio
import os
import psutil
import hikari
import lightbulb

from src.extensions.Background_Processes.botlogs import (
    BOT_EXECUTABLES,
    startup_detected,
    _stop_tailer,
    _schedule_update,  # If you don't have this, import _update_embed as _schedule_update
)

plugin = lightbulb.Plugin("Stop Bot Command")
plugin.add_checks(lightbulb.owner_only)


def _match_proc_for_exe(proc: psutil.Process, exe_full_path: str, workdir: str) -> bool:
    exe_full_path = os.path.abspath(exe_full_path).lower()
    workdir = os.path.abspath(workdir).lower()
    exe_name = os.path.basename(exe_full_path)

    try:
        p_exe = (proc.info.get("exe") or "").lower()
        p_name = (proc.info.get("name") or "").lower()
        p_cwd  = (proc.info.get("cwd") or "").lower()
        p_cmd  = proc.info.get("cmdline") or []
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False

    if p_exe and os.path.abspath(p_exe).lower() == exe_full_path:
        return True
    if p_name == exe_name.lower() and p_cwd == workdir:
        return True
    try:
        if p_cmd:
            cmd0 = os.path.abspath(p_cmd[0]).lower()
            if cmd0 == exe_full_path:
                return True
    except Exception:
        pass
    return False


async def _terminate_process(proc: psutil.Process) -> str:
    try:
        proc.terminate()
    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
        return f"terminate error: {e}"

    for _ in range(10):  # up to ~5s
        if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
            return "terminated"
        await asyncio.sleep(0.5)

    try:
        proc.kill()
        for _ in range(6):  # up to ~3s
            if not proc.is_running():
                return "killed"
            await asyncio.sleep(0.5)
    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
        return f"kill error: {e}"

    return "killed-timeout"


@plugin.command
@lightbulb.option(
    "wipe",
    "Delete the bot's session.json before stopping (like restart all).",
    type=bool,
    required=False,
    default=False,
)
@lightbulb.option(
    "name",
    "The bot to stop",
    choices=list(BOT_EXECUTABLES.keys()),
    required=True,
)
@lightbulb.command("stopbot", "Stop a monitored bot by name, optionally wiping session.json.")
@lightbulb.implements(lightbulb.SlashCommand)
async def stopbot(ctx: lightbulb.Context) -> None:
    botname = ctx.options.name
    exe_path = BOT_EXECUTABLES.get(botname)
    wipe = ctx.options.wipe

    if not exe_path:
        await ctx.respond(f"❌ Bot `{botname}` not found in configuration.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    matches: list[psutil.Process] = []
    for proc in psutil.process_iter(["pid", "name", "exe", "cwd", "cmdline", "status"]):
        try:
            if _match_proc_for_exe(proc, exe_path, os.path.dirname(exe_path)):
                matches.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if not matches:
        await ctx.respond(f"⚠️ No running process found for `{botname}`.", flags=hikari.MessageFlag.EPHEMERAL)
        _stop_tailer(botname)
        startup_detected[botname] = False
        try:
            await _schedule_update(ctx.app)
        except Exception:
            pass
        return

    results = []
    for p in matches:
        res = await _terminate_process(p)
        results.append(f"PID {p.pid}: {res}")

    # Optionally wipe session.json before marking as stopped
    if wipe:
        session_path = os.path.join(os.path.dirname(exe_path), "session.json")
        try:
            if os.path.exists(session_path):
                os.remove(session_path)
                results.append("session.json deleted")
            else:
                results.append("no session.json found")
        except Exception as e:
            results.append(f"wipe failed: {e}")

    _stop_tailer(botname)
    startup_detected[botname] = False

    # Refresh embed with the live BotApp instance
    try:
        await _schedule_update(ctx.app, debounce_seconds=0)
    except TypeError:
        try:
            await _schedule_update(ctx.app)
        except Exception:
            pass
    except Exception:
        pass

    await ctx.respond(
        f"🛑 Stopped `{botname}` → " + "; ".join(results),
        flags=hikari.MessageFlag.EPHEMERAL,
    )


def load(bot: lightbulb.BotApp):
    bot.add_plugin(plugin)


def unload(bot: lightbulb.BotApp):
    bot.remove_plugin(plugin)
