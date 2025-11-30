import subprocess, sys, os, venv

print("\n=== Installing v4 Control Bot ===\n")

builder = venv.EnvBuilder(with_pip=True)
builder.create("venv")

py = "venv/Scripts/python.exe" if os.name == "nt" else "venv/bin/python"

subprocess.check_call([py, "-m", "pip", "install", "--upgrade", "pip"])

packages = [
    "hikari==2.1.1",
    "lightbulb==2.3.5.post1",
    "psutil==7.1.3",
    "aiohttp==3.13.2",
    "certifi==2025.11.12"
]

subprocess.check_call([py, "-m", "pip", "install"] + packages)

print("\n=== ✔ All dependencies installed ===")
print(f"Run the bot with:\n  {py} main.py")
