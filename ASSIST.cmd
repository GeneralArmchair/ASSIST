@echo off
rem Starts ASSIST with the project's own Python (.venv), so the pinned Qt is used and options reach it.
rem Double-click this file, or pin a shortcut to it. Options are passed on, e.g.: ASSIST.cmd --replay <name>
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo ASSIST isn't set up yet. Double-click Setup.cmd in this folder first.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" main.py %*
