@echo off
rem ============================================================
rem  Boji Kuaichuan - launcher (double click to start)
rem  Requires Python 3.8+. Dependencies are installed on first run.
rem ============================================================
setlocal
cd /d "%~dp0"

set PY=
where python >nul 2>nul && set PY=python
if "%PY%"=="" (
  where py >nul 2>nul && set PY=py
)
if "%PY%"=="" (
  echo [ERROR] Python not found.
  echo Please install Python 3.8+ from https://www.python.org/downloads/
  echo Remember to check "Add Python to PATH" during installation.
  pause
  exit /b 1
)

%PY% -c "import qrcode, PIL" >nul 2>nul
if errorlevel 1 (
  echo [INFO] Installing dependencies...
  %PY% -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [ERROR] Failed to install dependencies. Run manually:
    echo         %PY% -m pip install -r requirements.txt
    pause
    exit /b 1
  )
)

%PY% bifrost_gui.py
pause
