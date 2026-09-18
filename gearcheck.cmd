@echo off
rem Double-click: compare using the last-loaded Strava export.
rem Drag a new export zip onto this file: load that zip, then compare.
rem Either way the HTML report opens in your browser when done.

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo No virtualenv found. From this folder run:
    echo   python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

set PYTHONIOENCODING=utf-8

if "%~1"=="" (
    .venv\Scripts\python.exe compare_gear.py --open
) else (
    .venv\Scripts\python.exe compare_gear.py --strava-csv "%~1" --open
)

echo.
pause
