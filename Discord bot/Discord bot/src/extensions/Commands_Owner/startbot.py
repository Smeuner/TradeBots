import asyncio
import hikari
import lightbulb

from src.extensions.Background_Processes.botlogs import (
    BOT_EXECUTABLES,
    startup_detected,
    run_and_monitor_bot,
)

plugin = lightbulb.Plugin("Start Bot Command")
plugin.add_checks(lightbulb.owner_only)


@plugin.command
@lightbulb.option(
    "name",
    "The bot to start (or reattach to if already running).",
    choices=list(BOT_EXECUTABLES.keys()),
    required=True,
)
@lightbulb.command("startbot", "Start (or reattach to) a monitored bot by name")
@lightbulb.implements(lightbulb.SlashCommand)
async def startbot(ctx: lightbulb.Context) -> None:
    botname = ctx.options.name
    path = BOT_EXECUTABLES.get(botname)

    if not path:
        await ctx.respond(f"❌ Bot `{botname}` not found in configuration.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    if startup_detected.get(botname):
        await ctx.respond(f"🔁 Re-attaching to `{botname}` (already running)…", flags=hikari.MessageFlag.EPHEMERAL)
    else:
        await ctx.respond(f"🚀 Starting `{botname}`…", flags=hikari.MessageFlag.EPHEMERAL)

    # Use the live BotApp instance from the context
    asyncio.create_task(run_and_monitor_bot(ctx.app, botname, path))


def load(bot: lightbulb.BotApp):
    bot.add_plugin(plugin)


def unload(bot: lightbulb.BotApp):
    bot.remove_plugin(plugin)
