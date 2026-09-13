@echo off
title VoxStream Windows Diagnostic & Setup Debugger
echo ========================================================
echo   VoxStream: Windows Setup Diagnostic Tool
echo   This window will REMAIN OPEN even if an error occurs.
echo ========================================================
echo.
echo Working Directory: %~dp0
echo.
echo [1] Checking Windows Version:
ver
echo.
echo [2] Checking Git in PATH:
where git 2>&1
echo.
echo [3] Checking Python in PATH:
where python 2>&1
echo.
echo [4] Checking Python 3 Launcher:
where py 2>&1
echo.
echo [5] Testing Python Execution:
py -3 --version 2>&1
python --version 2>&1
echo.
echo ========================================================
echo   Now launching setup_windows.bat...
echo ========================================================
echo.
call "%~dp0setup_windows.bat"
set "SETUP_RESULT=%errorlevel%"
echo.
echo ========================================================
echo   Setup exited with code: %SETUP_RESULT%
echo   This diagnostic window stays open so you can read everything.
echo ========================================================
echo.
cmd /k
