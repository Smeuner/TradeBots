# src/extensions/Background_Processes/bot_log_monitor.py
import hikari
import lightbulb
import asyncio
import os
import time
import re
import json
from collections import defaultdict, deque
import psutil
import sys, datetime
import random
from src.config import (
    LOG_DIR,
    BOT_EXECUTABLES,
    ALERT_CHANNEL_ID,
    ALERT_USER_ID,
    STATUS_REFRESH_SECONDS,
)


# Make stdout line-buffered; helps on Windows consoles
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

def log(msg: str):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)  # always flush


# --- Windows process creation flags for detaching children ---
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000  # helps if your host uses Job Objects

CREATION_FLAGS = (
    DETACHED_PROCESS
    | CREATE_NEW_PROCESS_GROUP
    | CREATE_NO_WINDOW
    | CREATE_BREAKAWAY_FROM_JOB
)
# -------------------------------------------------------------
os.makedirs(LOG_DIR, exist_ok=True)

# Keep rolling logs per bot, accessible to other modules
LOG_BUFFERS: dict[str, deque[str]] = defaultdict(lambda: deque(maxlen=15))

plugin = lightbulb.Plugin("Bot Log Monitor")

# Persist the status message id here (same folder as this file)
STATUS_STATE_FILE = os.path.join(os.path.dirname(__file__), "monitor_status.json")
# Persist latest instant/max so embed has values on startup
COIN_STATE_FILE = os.path.join(os.path.dirname(__file__), "coin_state.json")

def _persist_coin_state() -> None:
    try:
        data = {
            "instant": {k: (None if v is None else float(v)) for k, v in instant_coins.items()},
            "max": {k: (None if v is None else float(v)) for k, v in max_coins.items()},
        }
        with open(COIN_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
        log("[STATE] coin state persisted")
    except Exception as e:
        log(f"[STATE] coin persist failed: {e}")

def _load_coin_state() -> None:
    try:
        if not os.path.exists(COIN_STATE_FILE):
            log("[STATE] no persisted coin state found")
            return
        with open(COIN_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        inst = data.get("instant", {})
        mx = data.get("max", {})
        # populate known bots; ignore unknown keys
        for name in BOT_EXECUTABLES:
            if name in inst and inst[name] is not None:
                try:
                    instant_coins[name] = float(inst[name])
                except Exception:
                    pass
            if name in mx and mx[name] is not None:
                try:
                    max_coins[name] = float(mx[name])
                except Exception:
                    pass
        log("[STATE] coin state loaded")
    except Exception as e:
        log(f"[STATE] coin load failed: {e}")


# Parse: "Instant payout amount : 201.59 (201.59/2000)"
INSTANT_RX = re.compile(
    r"Instant\s+payout\s+amount\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*\(\s*[0-9]+(?:\.[0-9]+)?\s*/\s*([0-9]+(?:\.[0-9]+)?)\s*\)",
    re.IGNORECASE,
)

# Latest parsed Instant coins and Max, per bot
instant_coins: dict[str, float | None] = {name: None for name in BOT_EXECUTABLES}
max_coins: dict[str, float | None] = {name: None for name in BOT_EXECUTABLES}

# Load persisted values so the embed isn't empty on startup
_load_coin_state()

# Strict pattern: match number immediately before "Depositable items"
DEPOSITABLE_RE = re.compile(r"\b(\d+)\s+depositable\s+items\b", re.IGNORECASE)

# State
startup_detected = {name: False for name in BOT_EXECUTABLES}
trade_counts = {name: 0 for name in BOT_EXECUTABLES}
status_message_id: int | None = None
last_seen: dict[str, float] = {name: 0.0 for name in BOT_EXECUTABLES}  # heartbeat timestamps

# Heartbeat / refresh tuning
STALE_AFTER_SECONDS = 300  # (9) mark as stale if no log within 5 minutes
_periodic_task: asyncio.Task | None = None

# Serialize embed edits to avoid race conditions + debounce/throttle (4)
_embed_lock = asyncio.Lock()
_update_scheduled = False
# --- embed rate limiting / coalescing ---
MIN_EDIT_INTERVAL = 5.0  # seconds; tune 3–8s
_last_edit_ts: float = 0.0
_pending_force = False
# (_embed_lock and _update_scheduled are already defined above)

# One tail task per bot *and* stream ("out" for stdout, "err" for stderr)
TAIL_TASKS: dict[tuple[str, str], asyncio.Task] = {}

def _is_already_running(exe_full_path: str, workdir: str) -> bool:
    """
    Return True if a process with this exact exe path is already running.
    Fallback: match by process name + cwd (for cases where exe path is unavailable).
    """
    exe_full_path = os.path.abspath(exe_full_path).lower()
    workdir = os.path.abspath(workdir).lower()
    exe_name = os.path.basename(exe_full_path)

    for p in psutil.process_iter(["pid", "name", "exe", "cwd", "cmdline"]):
        try:
            # Skip ourselves
            if p.pid == os.getpid():
                continue

            p_exe = (p.info.get("exe") or "").lower()
            p_name = (p.info.get("name") or "").lower()
            p_cwd  = (p.info.get("cwd") or "").lower()

            # Strong match: exact exe path
            if p_exe and os.path.abspath(p_exe).lower() == exe_full_path:
                return True

            # Fallback: match by name + cwd
            if p_name == exe_name.lower() and p_cwd == workdir:
                return True

            # Extra fallback: if exe missing, check cmdline[0]
            cmd0 = ""
            try:
                cl = p.info.get("cmdline") or []
                if cl:
                    cmd0 = os.path.abspath(cl[0]).lower()
            except Exception:
                pass
            if cmd0 and cmd0 == exe_full_path:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False

def _persist_status_id(msg_id: int) -> None:
    """(2) Persist the status message id on disk."""
    try:
        with open(STATUS_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"status_message_id": int(msg_id)}, f)
        log(f"[STATE] persisted status_message_id={msg_id}")
    except Exception as e:
        log(f"[STATE] persist failed: {e}")

def _load_status_id() -> int | None:
    """(2) Load persisted status message id if present."""
    try:
        if os.path.exists(STATUS_STATE_FILE):
            with open(STATUS_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            mid = int(data.get("status_message_id"))
            log(f"[STATE] loaded status_message_id={mid}")
            return mid
        else:
            log("[STATE] no persisted status_message_id found")
    except Exception as e:
        log(f"[STATE] load failed: {e}")
    return None

def _tailer_running(name: str, label: str) -> bool:
    t = TAIL_TASKS.get((name, label))
    return t is not None and not t.done() and not t.cancelled()


def _start_tailer(bot: lightbulb.BotApp, name: str, log_path: str, label: str):
    """
    Start a single tailer per (bot, stream). `label` should be "out" or "err".
    Both streams append into LOG_BUFFERS[name].
    """
    key = (name, label)
    if _tailer_running(name, label):
        log(f"[{name}] tailer already running for {label} -> {log_path}")
        return
    log(f"[{name}] starting tailer for {label} -> {log_path}")
    TAIL_TASKS[key] = asyncio.create_task(_tail_log_and_parse(bot, name, log_path))

def _stop_tailer(name: str):
    """
    Cancel and remove both stdout and stderr tailers for a bot, if any.
    Kept same name/signature so existing stop command keeps working.
    """
    for label in ("out", "err"):
        key = (name, label)
        t = TAIL_TASKS.pop(key, None)
        if t and not t.done() and not t.cancelled():
            log(f"[{name}] stopping tailer ({label})")
            t.cancel()

async def _build_embed() -> hikari.Embed:
    """Build the embed showing bot statuses, depositable items, and instant (current/max)."""
    timestamp = int(time.time())

    header = (
        "Bot".ljust(20)
        + "Started".ljust(10)
        + "Depo".ljust(6)
        + "Instant".ljust(16)  # shows "current/max"
    )
    lines = [header, "-" * len(header)]

    for name in BOT_EXECUTABLES:
        started = "✅" if startup_detected.get(name) else "❌"
        depo = str(trade_counts.get(name, 0))
        ic = instant_coins.get(name)
        mx = max_coins.get(name)

        if isinstance(ic, (int, float)) and isinstance(mx, (int, float)):
            inst_str = f"{ic:.2f}/{mx:.0f}"
        elif isinstance(ic, (int, float)):
            inst_str = f"{ic:.2f}/—"
        else:
            inst_str = "—"

        lines.append(
            name.ljust(20)
            + started.ljust(10)
            + depo.ljust(6)
            + inst_str.ljust(16)
        )

    return hikari.Embed(
        title="📊 v4 Bot Log Monitor",
        description=f"Last checked: <t:{timestamp}:R>\n\n```\n" + "\n".join(lines) + "\n```",
        color=hikari.Color(0x3498DB),
    )



async def _update_embed(bot: lightbulb.BotApp, *, force: bool = False):
    """Edit the status embed, respecting a minimum interval to avoid rate limits."""
    global status_message_id, _last_edit_ts
    if not status_message_id:
        log("[EMBED] no status_message_id; skipping update")
        return

    now = time.time()
    if not force and (now - _last_edit_ts) < MIN_EDIT_INTERVAL:
        # Too soon; let the scheduler call us later
        return

    async with _embed_lock:
        embed = await _build_embed()
        try:
            await bot.rest.edit_message(ALERT_CHANNEL_ID, status_message_id, embed=embed)
            _last_edit_ts = time.time()
            log(f"[EMBED] edited message id={status_message_id}")
        except hikari.NotFoundError:
            # Message was deleted: recreate and persist
            log("[EMBED] status message not found; recreating")
            msg = await bot.rest.create_message(ALERT_CHANNEL_ID, embed=embed)
            status_message_id = msg.id
            _persist_status_id(status_message_id)
            _last_edit_ts = time.time()
        except Exception as e:
            log(f"[EMBED] update failed: {e}")


async def _schedule_update(bot: lightbulb.BotApp, *, debounce_seconds: float = 2.0, force: bool = False):
    """Coalesce multiple update requests and apply one edit after a debounce window."""
    global _update_scheduled, _pending_force
    _pending_force = _pending_force or force  # any caller can request a force

    if _update_scheduled:
        return
    _update_scheduled = True
    try:
        await asyncio.sleep(debounce_seconds)
        # Try a normal update; if it’s still within MIN_EDIT_INTERVAL and we have a force pending,
        # call with force to push it through (use sparingly).
        await _update_embed(bot, force=_pending_force)
    finally:
        _pending_force = False
        _update_scheduled = False


async def _tail_log_and_parse(bot: lightbulb.BotApp, name: str, log_path: str):
    """Follow the log file and reuse your existing parsing + embed updates.
       Also parses 'Instant payout amount : <coins> (<current>/<max>)' to update Instant coins + Max,
       and persists the latest values to disk when they change."""
    try:
        # Ensure regex + state exist (in case not defined elsewhere yet)
        rx = globals().get("INSTANT_RX")
        if rx is None:
            globals()["INSTANT_RX"] = re.compile(
                r"Instant\s+payout\s+amount\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*\(\s*[0-9]+(?:\.[0-9]+)?\s*/\s*([0-9]+(?:\.[0-9]+)?)\s*\)",
                re.IGNORECASE,
            )
            rx = globals()["INSTANT_RX"]

        if "instant_coins" not in globals():
            globals()["instant_coins"] = {bn: None for bn in BOT_EXECUTABLES}
        if "max_coins" not in globals():
            globals()["max_coins"] = {bn: None for bn in BOT_EXECUTABLES}

        # Wait until the file exists (handles the "already running" case too)
        while not os.path.exists(log_path):
            await asyncio.sleep(0.5)

        log(f"[{name}] tailer attached -> {log_path}")

        # Start at end; change to 0 if you want full replay
        with open(log_path, "rb", buffering=0) as f:
            f.seek(0, os.SEEK_END)
            while True:
                line = f.readline()
                if not line:
                    await asyncio.sleep(0.2)
                    continue

                text = line.decode("utf-8", errors="ignore").rstrip()
                log(f"[{name}] {text}")  # echo to your console once per real line
                LOG_BUFFERS[name].append(text)
                # last_seen[name] = time.time()  # keep commented if you've removed 'stale'

                # Depositable items
                m = DEPOSITABLE_RE.search(text)
                if m:
                    new_val = int(m.group(1))
                    if trade_counts.get(name) != new_val:
                        trade_counts[name] = new_val
                        log(f"[{name}] UPDATED: Depositable items -> {new_val}")
                        try:
                            await _schedule_update(bot)  # or _schedule_update(bot) if you prefer debounce
                        except Exception as e:
                            log(f"[{name}] ERROR updating embed on keyword: {e}")

                # Instant payout amount : <coins> (<current>/<max>)  -> capture coins and max
                p = rx.search(text)
                if p:
                    coins_val = None
                    max_val = None
                    try:
                        coins_val = float(p.group(1))
                    except ValueError:
                        pass
                    try:
                        max_val = float(p.group(2))
                    except ValueError:
                        pass

                    changed = False
                    if coins_val is not None and globals()["instant_coins"].get(name) != coins_val:
                        globals()["instant_coins"][name] = coins_val
                        log(f"[{name}] UPDATED: Instant -> {coins_val:.2f}")
                        changed = True
                    if max_val is not None and globals()["max_coins"].get(name) != max_val:
                        globals()["max_coins"][name] = max_val
                        log(f"[{name}] UPDATED: Max -> {max_val:.0f}")
                        changed = True

                    if changed:
                        # Persist to JSON if the helper is available
                        try:
                            persist_fn = globals().get("_persist_coin_state")
                            if callable(persist_fn):
                                persist_fn()
                        except Exception as e:
                            log(f"[{name}] coin persist error: {e}")

                        # Refresh embed
                        try:
                            await _schedule_update(bot)  # or await _schedule_update(bot) if you prefer debounce
                        except Exception as e:
                            log(f"[{name}] ERROR updating embed on instant: {e}")

    except asyncio.CancelledError:
        log(f"[{name}] tailer cancelled")
    except Exception as e:
        log(f"[{name}] tail error: {e}")




async def _spawn_via_powershell(path: str, cwd: str, log_path: str, err_log_path: str) -> int:
    """
    Start the bot via PowerShell Start-Process so it escapes the parent Job.
    No PIPEs are used (avoids 'closed pipe' on shutdown). PID is written to a
    per-bot file (derived from cwd) and read back.
    """
    def _ps_escape(s: str) -> str:
        return s.replace("`", "``").replace('"', '`"')

    # Use the working directory name to make a unique PID file for each bot
    bot_key = os.path.basename(os.path.abspath(cwd)) or "v4bot"
    pid_file = os.path.join(LOG_DIR, f"{bot_key}.pid")

    # Clean any stale pid file first
    try:
        if os.path.exists(pid_file):
            os.remove(pid_file)
    except Exception:
        pass

    script = (
        '[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;'
        f'$p = Start-Process -FilePath "{_ps_escape(path)}" '
        f'-WorkingDirectory "{_ps_escape(cwd)}" '
        '-WindowStyle Hidden '
        f'-RedirectStandardOutput "{_ps_escape(log_path)}" '
        f'-RedirectStandardError "{_ps_escape(err_log_path)}" '
        '-PassThru;'
        f'[System.IO.File]::WriteAllText("{_ps_escape(pid_file)}", $p.Id.ToString(), [System.Text.Encoding]::ASCII);'
    )

    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy", "Bypass",
        "-Command", script,
    ]

    log(f"[PS] launching via Start-Process: {path} (cwd={cwd}) -> out={log_path} err={err_log_path} pid={pid_file}")
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
    )
    await proc.wait()

    # Read PID from the unique file with a longer retry window
    pid = None
    for _ in range(50):  # up to ~5s
        try:
            if os.path.exists(pid_file):
                with open(pid_file, "r", encoding="ascii", errors="ignore") as f:
                    txt = f.read().strip()
                if txt.isdigit():
                    pid = int(txt)
                    break
        except Exception:
            pass
        await asyncio.sleep(0.1)

    if pid is None:
        raise RuntimeError(f"PowerShell did not write PID file: {pid_file}")

    log(f"[PS] child PID={pid} (pid_file={pid_file})")
    return pid



async def _watch_pid_and_alert(bot: lightbulb.BotApp, name: str, pid: int):
    """Poll the spawned process by PID; when it exits, update embed and alert."""
    try:
        p = psutil.Process(pid)
    except psutil.Error:
        p = None

    if p is not None:
        try:
            while p.is_running():
                await asyncio.sleep(2)
        except psutil.Error:
            pass

    # Process is gone
    startup_detected[name] = False
    _stop_tailer(name)
    try:
        await _schedule_update(bot, debounce_seconds=0)
        await bot.rest.create_message(
            ALERT_CHANNEL_ID,
            content=f"<@!{ALERT_USER_ID}> ⚠️ Bot **{name}** just went offline!",
            user_mentions=True,
        )
        log(f"[{name}] offline alert sent (pid={pid})")
    except Exception as e:
        log(f"[{name}] ERROR sending offline alert: {e}")
        

async def start_all_bots(bot: lightbulb.BotApp):
    """Create/reuse the status embed, launch all bots, and start periodic refresh."""
    global status_message_id, _periodic_task

    # Try to reuse existing status message; self-heal if missing
    status_message_id = _load_status_id()
    if status_message_id:
        try:
            await _schedule_update(bot)  # test-edit; also builds current embed
            log(f"[DEBUG] Reusing existing status message id={status_message_id}")
        except hikari.NotFoundError:
            log("[DEBUG] persisted status message not found; will create new")
            status_message_id = None
        except Exception as e:
            log(f"[DEBUG] failed to edit persisted status message: {e}")
            status_message_id = None

    if not status_message_id:
        initial = await _build_embed()
        msg = await bot.rest.create_message(ALERT_CHANNEL_ID, embed=initial)
        status_message_id = msg.id
        _persist_status_id(status_message_id)
        log(f"[DEBUG] Created log monitor message id={status_message_id}")

    # --- launch all bots with a tiny stagger ---
    LAUNCH_STAGGER_SECONDS = 0.75  # tune 0.3–1.5s
    LAUNCH_JITTER_SECONDS = 0.25   # +/- random jitter

    count = 0
    for name, path in BOT_EXECUTABLES.items():
        asyncio.create_task(run_and_monitor_bot(bot, name, path))
        count += 1
        # smooth out log/pid writes and any service logins
        await asyncio.sleep(
            LAUNCH_STAGGER_SECONDS + (random.random() * LAUNCH_JITTER_SECONDS)
        )
    log(f"[DEBUG] launching {count} bot(s) with stagger")

    # Periodic refresh to keep <t:...:R> fresh and reflect counters
    async def _periodic():
        while True:
            try:
                await asyncio.sleep(STATUS_REFRESH_SECONDS)  # tune as needed
                await _schedule_update(bot)
                log("[PERIODIC] embed refreshed")
            except asyncio.CancelledError:
                log("[PERIODIC] cancelled")
                break
            except Exception as e:
                log(f"[PERIODIC] refresh failed: {e}")

    # Cancel old periodic task if any, then start a new one
    if _periodic_task:
        try:
            _periodic_task.cancel()
        except Exception:
            pass
    _periodic_task = asyncio.create_task(_periodic())


def _find_running_pid(exe_full_path: str, workdir: str) -> int | None:
    """Return the PID of a running v4-bot that matches this exe/cwd, else None."""
    exe_full_path = os.path.abspath(exe_full_path).lower()
    workdir = os.path.abspath(workdir).lower()
    exe_name = os.path.basename(exe_full_path)

    for p in psutil.process_iter(["pid", "name", "exe", "cwd", "cmdline"]):
        try:
            if p.pid == os.getpid():
                continue
            p_exe = (p.info.get("exe") or "").lower()
            p_name = (p.info.get("name") or "").lower()
            p_cwd  = (p.info.get("cwd") or "").lower()
            if p_exe and os.path.abspath(p_exe).lower() == exe_full_path:
                return p.pid
            if p_name == exe_name.lower() and p_cwd == workdir:
                return p.pid
            cl = p.info.get("cmdline") or []
            if cl:
                cmd0 = os.path.abspath(cl[0]).lower()
                if cmd0 == exe_full_path:
                    return p.pid
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


async def run_and_monitor_bot(bot: lightbulb.BotApp, name: str, path: str):
    """
    Start one bot process and monitor its logs
    (PowerShell Start-Process to escape Job Object + file tailing + PID watcher).
    Works whether the process is freshly spawned or already running.
    """
    cwd = os.path.dirname(path)
    log_path = os.path.join(LOG_DIR, f"{name}.log")       # stdout
    err_log_path = log_path + ".err"                      # stderr

    # Ensure no stale tailers remain
    _stop_tailer(name)

    # --- already running? don't spawn a duplicate ---
    if _is_already_running(path, cwd):
        log(f"[{name}] already running; not launching a duplicate.")
        startup_detected[name] = True
        try:
            await _schedule_update(bot)
        except Exception as e:
            log(f"[{name}] embed update failed on duplicate check: {e}")

        # Start tailers for both streams so we see all output
        _start_tailer(bot, name, log_path, "out")
        _start_tailer(bot, name, err_log_path, "err")

        # Attach a watcher to the existing PID so offline alert & state still work
        pid = _find_running_pid(path, cwd)
        if pid:
            log(f"[{name}] attaching watcher to existing PID {pid}")
            asyncio.create_task(_watch_pid_and_alert(bot, name, pid))
        else:
            log(f"[{name}] WARNING: could not find PID for already-running process")
        return

    # --- spawn fresh via PowerShell (no PIPEs), then read PID from file ---
    try:
        child_pid = await _spawn_via_powershell(path, cwd, log_path, err_log_path)
    except Exception as e:
        log(f"[{name}] failed to start: {e}")
        try:
            await bot.rest.create_message(
                ALERT_CHANNEL_ID,
                f"❌ Failed to start `{name}`: `{e}`",
            )
        except Exception:
            pass
        return

    # Mark started + update embed
    startup_detected[name] = True
    try:
        await _schedule_update(bot)
    except Exception as e:
        log(f"[{name}] ERROR updating embed on start: {e}")

    # Tail both streams
    _start_tailer(bot, name, log_path, "out")
    _start_tailer(bot, name, err_log_path, "err")

    # Watch the PID and alert on exit; cancels both tailers on exit via _watch_pid_and_alert
    asyncio.create_task(_watch_pid_and_alert(bot, name, child_pid))


@plugin.listener(hikari.StartedEvent)
async def on_started(_: hikari.StartedEvent):
    log("[LIFECYCLE] Bot started; kicking off monitors")
    asyncio.create_task(start_all_bots(plugin.bot))

@plugin.listener(hikari.StoppingEvent)
async def on_stopping(_: hikari.StoppingEvent):
    # Stop periodic task (4)
    log("[LIFECYCLE] Bot stopping; cancelling periodic task")
    global _periodic_task
    if _periodic_task:
        _periodic_task.cancel()
        _periodic_task = None

def load(bot):
    log("[EXT] loading Bot Log Monitor")
    bot.add_plugin(plugin)

def unload(bot):
    log("[EXT] unloading Bot Log Monitor")
    bot.remove_plugin(plugin)
