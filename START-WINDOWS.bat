@echo off
cd /d "%~dp0"
py -3 --version >nul 2>&1
if not errorlevel 1 (
    py -3 run_local.py
    pause
    exit /b
)
python --version >nul 2>&1
if not errorlevel 1 (
    python run_local.py
    pause
    exit /b
)
echo Python is required. Install Python 3.10 or newer from https://www.python.org/downloads/
echo Enable "Add Python to PATH" during installation, then reopen this file.
pause
