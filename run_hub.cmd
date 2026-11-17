@echo off
cd /d "%~dp0"
set "PYEXE=%~dp0.venv\Scripts\python.exe"
set "PYARG="
if not exist "%PYEXE%" (
  set "PYEXE=python"
  where py >nul 2>&1
  if not errorlevel 1 (
    set "PYEXE=py"
    set "PYARG=-3"
  )
)
"%PYEXE%" %PYARG% -m tracker.hub %*
