@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto deps

set "PY="
for %%v in (3.12 3.13 3.14) do if not defined PY (py -%%v -c "" >nul 2>&1 && set "PY=py -%%v")

if not defined PY (
  echo Python 3.12-3.14 was not found. Trying to install Python 3.12...
  py install 3.12 --yes >nul 2>&1 || winget install -e --id Python.Python.3.12 --scope user --silent
  py -3.12 -c "" >nul 2>&1 && set "PY=py -3.12"
)
if not defined PY (
  echo.
  echo Could not find or install Python 3.12. Install it from https://www.python.org/downloads/
  echo then run setup.cmd again.
  pause
  exit /b 1
)

echo Creating virtual environment with %PY% ...
%PY% -m venv .venv || (echo Failed to create .venv & pause & exit /b 1)

:deps
echo Installing Python packages...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || (echo Package install failed & pause & exit /b 1)

echo Starting setup wizard...
".venv\Scripts\python.exe" setup_wizard.py %*
