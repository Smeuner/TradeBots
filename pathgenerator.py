import os
import textwrap


def find_bots_on_desktop() -> dict[str, str]:
    bots: dict[str, str] = {}

    # Resolve Desktop path for the current user
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")

    if not os.path.isdir(desktop):
        print(f"Desktop folder not found: {desktop}")
        return bots

    print(f"Scanning desktop: {desktop}\n")

    for entry in os.listdir(desktop):
        folder = os.path.join(desktop, entry)
        if not os.path.isdir(folder):
            continue

        exe = os.path.join(folder, "v4-bot.exe")
        cmd = os.path.join(folder, "run.cmd")

        if os.path.isfile(exe) and os.path.isfile(cmd):
            # Use folder name as bot name
            botname = entry
            # Use forward slashes to avoid escaping issues
            exe_path = exe.replace("\\", "/")
            bots[botname] = exe_path
            print(f"  [+] Found bot folder: {botname} -> {exe_path}")

    if not bots:
        print("No bot folders found (need folders on Desktop with v4-bot.exe and run.cmd).")

    return bots


def print_config_snippet(bots: dict[str, str]) -> None:
    if not bots:
        return

    print("\n\n=== Generated BOT_EXECUTABLES snippet ===\n")

    # Build a nice, readable Python dict snippet
    lines = ["BOT_EXECUTABLES = {"]

    # Sort by name for stable order
    for name in sorted(bots.keys(), key=str.lower):
        path = bots[name]
        # repr() will properly escape the string for Python
        lines.append(f'    "{name}": r"{path}",')

    lines.append("}")
    snippet = "\n".join(lines)

    print(snippet)
    print("\nCopy the above block into src/config.py\n")


def main() -> None:
    bots = find_bots_on_desktop()
    print_config_snippet(bots)
    input("\nPress Enter to exit...")


if __name__ == "__main__":
    main()
