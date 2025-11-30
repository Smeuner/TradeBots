# src/extensions/Commands_Owner/tail.py
import lightbulb
import hikari

# Try common background module names
try:
    from ..Background_Processes import bot_log_monitor as botlogs
except ImportError:
    try:
        from ..Background_Processes import botlogs as botlogs
    except ImportError as e:
        raise ImportError(
            "Could not import background monitor module. "
            "Expected one of: Background_Processes.bot_log_monitor or Background_Processes.botlogs"
        ) from e

plugin = lightbulb.Plugin("owner_tail")
plugin.add_checks(lightbulb.owner_only)

@plugin.command
@lightbulb.option("name", "Bot name", required=True, autocomplete=True)
@lightbulb.option("lines", "Number of lines (1-50)", type=int, required=False, default=15)
@lightbulb.command("tail", "Show the last N log lines from a bot (ephemeral)")
@lightbulb.implements(lightbulb.SlashCommand)
async def tail(ctx: lightbulb.Context):
    name = ctx.options.name
    n = max(1, min(50, ctx.options.lines))

    buf = botlogs.LOG_BUFFERS.get(name)
    if not buf:
        await ctx.respond(f"Unknown bot `{name}` or no logs yet.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    lines = list(buf)[-n:]
    content = "```\n" + "\n".join(lines) + "\n```"
    if len(content) > 1900:
        while len(content) > 1900 and lines:
            lines.pop(0)
            content = "```\n" + "\n".join(lines) + "\n```"
        if not lines:
            content = "_(output truncated)_"

    await ctx.respond(content, flags=hikari.MessageFlag.EPHEMERAL)

# ⬇️ no type hint here; keep it version-agnostic
@tail.autocomplete("name")
async def ac_name(option, interaction):
    # Offer names we’ve seen buffers for; fallback to configured bots
    names = list(botlogs.LOG_BUFFERS.keys()) or list(botlogs.BOT_EXECUTABLES.keys())
    names = names[:25]  # Discord limit

    # Return a list of choices (name=value)
    return [hikari.CommandChoice(name=n, value=n) for n in names]

def load(bot):
    bot.add_plugin(plugin)

def unload(bot):
    bot.remove_plugin(plugin)
