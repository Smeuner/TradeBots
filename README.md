# v4 Bot Control System

This package contains everything needed to run the Discord control system for multiple external `v4-bot.exe` bots.

The bot is written for **Python 3.11.7** and uses:
- Hikari 2.1.1  
- Lightbulb 2.3.5.post1  
- psutil  
- aiohttp

---

## 1. Requirements

- Windows 10/11 or Windows Server  
- Python **3.11.7** (exact version recommended)  
- A Discord bot token  
- Your own folders containing `v4-bot.exe`

---

## 2. Installation

You can install everything using the batch file.

### Automatic install

Run:

```bat
install.bat
```

This will:
- create the virtual environment
- activate it
- install everything from `requirements.txt`

---

## 3. Configuration

Inside the `Discord bot/src/` folder you will find:

```
config.py
```

Then edit `config.py` and set:

- `DISCORD_TOKEN` — your Discord bot token  
- `BOT_EXECUTABLES` — names + paths to your `v4-bot.exe` folders  
- `ALERT_CHANNEL_ID` — channel where the status panel is posted  
- `ALERT_USER_ID` — user to ping when a bot goes offline  

`config.py` is your private file and should not be shared.

---

## 4. Running the bot

Activate the virtual environment:

```bat
venv\Scripts\activate
```

Then run:

```bat
python main.py
```

The bot will log into Discord and automatically:
- start the log monitor  
- detect running bots  
- post/update the status panel in the alert channel  

---

## 5. File Layout

```
install.bat
install.py
requirements.txt
Discord bot/
    main.py
    src/
        config.example.py
        config.py   (user-created)
        extensions/
            ...
```

---

## Notes

- Do not share your `config.py` or bot token.
- The external `v4-bot.exe` files are **not** included.
- The system is designed for Windows-based executable bots.

