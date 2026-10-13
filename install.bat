@echo off
setlocal
cd /d "%~dp0"

rem Installation writes machine/network mode settings and performs preflight.
rem Service startup is intentionally handled only by start.bat.
net session >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator privileges...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    if errorlevel 1 (
        echo [!] Administrator elevation failed. Right-click install.bat and choose ^"Run as administrator^".
        pause
        exit /b 1
    )
    exit /b 0
)

rem Tzy OS installer entry.
rem Interpreter: prefer the bundled portable runtime (target PC needs no
rem Python), else fall back to system Python.

set "PYEXE=runtime\python\python.exe"
if exist "%PYEXE%" goto :run_setup
set "PYEXE=python"

:run_setup
rem Python 3.7+ supports -X utf8.  Older system Python versions (common on
rem Win7) do not, so only pass it to the bundled runtime.
if /i "%PYEXE%"=="python" (
    "%PYEXE%" tools\nova_setup.py %*
) else (
    "%PYEXE%" -X utf8 tools\nova_setup.py %*
)

if errorlevel 1 pause
