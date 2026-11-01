@echo off
rem Tolmach launcher - double-click to start (or restart) the tray.
rem Runs the startup checks in this window first: each step prints a line,
rem missing libraries and models are offered for install, and everything
rem lands in %USERPROFILE%\.tolmach\logs\startup.log.
rem Safe to click "just in case": a tray that is already running is left
rem alone unless you answer "y" to the restart question.
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"
if not exist "%ROOT%.venv\Scripts\python.exe" (
  echo Creating the virtual environment in %ROOT%.venv ...
  call :make_venv py
  if errorlevel 2 call :make_venv python
  if errorlevel 2 call :no_python
  if errorlevel 1 (
    pause
    exit /b 1
  )
)
"%ROOT%.venv\Scripts\python.exe" -m tolmach.tray.startup || pause
exit /b

rem Any Python can run make_venv.py, a 32-bit or an old one too: it lists the
rem Pythons, says which one will do and why the others will not, and makes
rem .venv with a 64-bit Python 3.12 or newer. 0 - made, 1 - it said why not,
rem 2 - there is no such command or it is not a working Python.
:make_venv
%1 -c "pass" >nul 2>nul || exit /b 2
%1 "%ROOT%scripts\make_venv.py" "%ROOT%.venv"
if errorlevel 1 exit /b 1
exit /b 0

:no_python
echo No Python found: Tolmach needs a 64-bit Python 3.12 or newer.
echo What to do:
echo   1. Open https://www.python.org/downloads/windows/ and download
echo      "Windows installer (64-bit)" of Python 3.12 or newer.
echo   2. Install it.
echo   3. Run tolmach.cmd again.
exit /b 1
