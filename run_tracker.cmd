@echo off
cd /d "%~dp0"
"C:\Users\Anthony.Zunino\AppData\Local\Programs\Python\Python312\python.exe" -m tracker.tracker %* >> "output\scheduler.log" 2>&1
