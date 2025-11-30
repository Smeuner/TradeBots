import ssl, certifi, aiohttp
from src.config import DISCORD_TOKEN

# Force aiohttp to use certifi bundle
old_init = aiohttp.TCPConnector.__init__

def new_init(self, *args, **kwargs):
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    kwargs["ssl"] = ssl_context
    old_init(self, *args, **kwargs)

aiohttp.TCPConnector.__init__ = new_init

import os
import hikari
import lightbulb

bot = lightbulb.BotApp(
    token=DISCORD_TOKEN,
    intents=hikari.Intents.ALL_UNPRIVILEGED | hikari.Intents.MESSAGE_CONTENT,
    ignore_bots=True,
    banner=None,
    help_slash_command=False,
    help_class=None,
)

bot.load_extensions_from("./src/extensions/", recursive=True)

bot.run(
    status = hikari.Status.DO_NOT_DISTURB,
    activity = hikari.Activity(
        name = "with your kids",
        type = hikari.ActivityType.PLAYING,
        
    ),
)
