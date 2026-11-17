@echo off
cd /d "%~dp0"
if not exist output mkdir output
".venv\Scripts\python.exe" -m tracker.tracker %* >> "output\scheduler.log" 2>&1
