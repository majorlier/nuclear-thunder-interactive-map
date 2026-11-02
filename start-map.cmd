@echo off
rem Starts a local web server for the map and opens the default browser.
rem Any arguments are passed to server.ps1, for example:
rem   start-map.cmd -Port 9000
rem   start-map.cmd -NoBrowser
rem   start-map.cmd -Internal
setlocal
cd /d "%~dp0"

if not exist "%~dp0index.html" (
    echo.
    echo  index.html was not found next to this launcher.
    echo  Keep start-map.cmd and server.ps1 inside the map folder.
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0server.ps1" %*
