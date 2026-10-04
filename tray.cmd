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
  py -3.12 -m venv "%ROOT%.venv"
  if errorlevel 1 (
    echo Could not create it: install 64-bit Python 3.12 from python.org and run this again.
    pause
    exit /b 1
  )
)
"%ROOT%.venv\Scripts\python.exe" -m tolmach.tray.startup || pause
