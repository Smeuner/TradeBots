@echo off
title Installing v4 Control Bot

echo Creating virtual environment...
python -m venv venv

call venv\Scripts\activate

echo Upgrading pip...
python -m pip install --upgrade pip

echo Installing packages...
python -m pip install hikari==2.1.1 hikari-lightbulb==2.3.5.post1 psutil==7.1.3 aiohttp==3.13.2 certifi==2025.11.12

echo.
echo =============================================
echo ✔ Installation complete!
echo Run the bot with:
echo     run.bat
echo =============================================
pause
