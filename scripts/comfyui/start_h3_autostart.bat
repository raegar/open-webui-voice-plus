@echo off
setlocal
cd /d "%~dp0"
title MiniMax H3 - ComfyUI (autostart)

rem Autostart variant of start_h3.bat: same flags, but no --auto-launch so a
rem browser tab is not opened on every boot, and no pause so a failure does not
rem leave a window waiting for a keypress.

rem Bail out if ComfyUI is already serving 8188. The scheduled task's action
rem returns as soon as it has spawned the process, so Task Scheduler's
rem MultipleInstances policy cannot prevent a second copy on its own; without
rem this guard a logon while ComfyUI is already running starts a duplicate that
rem loads models, fails to bind the port, and exits.
netstat -ano -p tcp | findstr /r /c:"LISTENING" | findstr /c:":8188 " >nul 2>&1
if not errorlevel 1 (
  echo ComfyUI is already listening on 8188. Nothing to do.
  exit /b 0
)

for /f %%G in ('powershell -NoProfile -Command "[math]::Floor((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)"') do set RAM_GB=%%G

if %RAM_GB% GEQ 48 goto highram

venv\Scripts\python.exe main.py --reserve-vram 1 --disable-pinned-memory --disable-async-offload --enable-manager
goto end

:highram
venv\Scripts\python.exe main.py --reserve-vram 1 --enable-manager

:end
exit /b %errorlevel%
