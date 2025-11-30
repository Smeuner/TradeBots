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

[Install Python](https://www.python.org/downloads/release/python-3117/) (Check "add Python to PATH" when you run the installer)
You can install everything else using the batch file.

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

## Optional: Generate BOT_EXECUTABLES Automatically

If your bot folders are always located on your Desktop and contain `v4-bot.exe` and `run.cmd`, you can automatically generate the required `BOT_EXECUTABLES` block for `src/config.py`.

Run the script:

```bat
python generate_bot_list.py
```

It will detect all valid bot folders on your Desktop and print a ready-to-paste dictionary.

Copy this block into your `src/config.py`.

Keep in mind that this will add all folders with those 2 files to the variable. you only want the actual bot paths in there. 

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

## Creating and Inviting a Discord Bot

1. Go to the Discord Developer Portal:  
   https://discord.com/developers/applications

2. Click **New Application**, choose a name, and create the app.

3. In the sidebar, open **Bot** → click **Add Bot**.

4. Under **Token**, click **Reset Token** and copy your bot token.  
   You will paste this into `src/config.py` later.

5. Enable the required intents under the **Privileged Gateway Intents** section:
   - Presence Intent  
   - Server Members Intent  
   - Message Content Intent  

6. In the sidebar, go to **OAuth2 → URL Generator**.

7. Under **Scopes**, select:
   - `bot`
   - `applications.commands`

8. Under **Bot Permissions**, select:
   - **Administrator**  
     (or select only the permissions you want the bot to have)

9. Copy the generated URL and open it in your browser to invite the bot to your server.

10. The bot will appear offline until you run `main.py`.
