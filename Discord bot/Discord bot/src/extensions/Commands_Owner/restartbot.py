import asyncio
import os
import psutil
import hikari
import lightbulb

from src.extensions.Background_Processes.botlogs import (
    BOT_EXECUTABLES,
    run_and_monitor_bot,
    startup_detected,
    _stop_tailer,
    _schedule_update,  # or _update_embed as _schedule_update
)

plugin = lightbulb.Plugin("Restart Bot Command")
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
    "Delete each bot's session.json before restarting.",
    type=bool,
    required=False,
    default=False,
)
@lightbulb.option(
    "names",
    "Comma-separated bot names to restart (e.g. bot1, bot2).",
    required=True,
    autocomplete=True,
)
@lightbulb.command("restartbot", "Restart one or more monitored bots by name.")
@lightbulb.implements(lightbulb.SlashCommand)
async def restartbot(ctx: lightbulb.Context) -> None:
    botnames = [n.strip() for n in ctx.options.names.split(",") if n.strip()]
    wipe = ctx.options.wipe

    invalid = [n for n in botnames if n not in BOT_EXECUTABLES]
    if invalid:
        await ctx.respond(
            f"❌ Unknown bot name(s): {', '.join(invalid)}",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    results = []

    for botname in botnames:
        exe_path = BOT_EXECUTABLES[botname]
        cwd = os.path.dirname(exe_path)

        # --- Stop phase ---
        matches: list[psutil.Process] = []
        for proc in psutil.process_iter(["pid", "name", "exe", "cwd", "cmdline", "status"]):
            try:
                if _match_proc_for_exe(proc, exe_path, cwd):
                    matches.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if matches:
            for p in matches:
                res = await _terminate_process(p)
                results.append(f"{botname} → PID {p.pid}: {res}")
        else:
            results.append(f"{botname}: no running process found")

        _stop_tailer(botname)
        startup_detected[botname] = False

        # --- Optional wipe phase ---
        if wipe:
            session_path = os.path.join(cwd, "session.json")
            try:
                if os.path.exists(session_path):
                    os.remove(session_path)
                    results.append(f"{botname}: session.json deleted")
                else:
                    results.append(f"{botname}: no session.json found")
            except Exception as e:
                results.append(f"{botname}: wipe failed ({e})")

        # --- Restart phase ---
        try:
            asyncio.create_task(run_and_monitor_bot(ctx.app, botname, exe_path))
            results.append(f"{botname}: restarting...")
        except Exception as e:
            results.append(f"{botname}: restart failed ({e})")

    # --- Update embed ---
    try:
        await _schedule_update(ctx.app, debounce_seconds=0)
    except TypeError:
        try:
            await asyncio.sleep(3)
            await _schedule_update(ctx.app)
        except Exception:
            pass
    except Exception:
        pass

    await ctx.respond(
        "🔄 Restart results:\n" + "\n".join(f"- {r}" for r in results),
        flags=hikari.MessageFlag.EPHEMERAL,
    )


@restartbot.autocomplete("names")
async def names_autocomplete(
    opt: hikari.AutocompleteInteractionOption, inter: hikari.AutocompleteInteraction
):
    query = opt.value.lower() if opt.value else ""
    suggestions = [name for name in BOT_EXECUTABLES.keys() if query in name.lower()]
    return [hikari.CommandChoice(name=n, value=n) for n in suggestions[:25]]


def load(bot: lightbulb.BotApp):
    bot.add_plugin(plugin)


def unload(bot: lightbulb.BotApp):
    bot.remove_plugin(plugin)
