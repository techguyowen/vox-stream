@echo off
setlocal EnableDelayedExpansion

cd /d "%~dp0"
set "ROOT_DIR=%~dp0"
if "%ROOT_DIR:~-1%"=="\" set "ROOT_DIR=%ROOT_DIR:~0,-1%"
set "VENV_PY=%ROOT_DIR%\.venv\Scripts\python.exe"

echo =======================================================
echo   VoxStream Shortcut Creator (Desktop & Taskbar)
echo =======================================================
echo.

if exist "%VENV_PY%" (
    call "%VENV_PY%" -m obs_captioner.launcher --create-shortcuts
) else (
    python -m obs_captioner.launcher --create-shortcuts
)

if %errorlevel% equ 0 (
    echo.
    echo =======================================================
    echo   [SUCCESS] Shortcuts created!
    echo.
    echo   To Pin VoxStream to your Windows Taskbar:
    echo     1. Click the Windows Start button
    echo     2. Search for 'VoxStream'
    echo     3. Right-click the VoxStream icon and click 'Pin to taskbar'
    echo =======================================================
) else (
    echo.
    echo   [WARNING] Could not automatically create shortcuts.
)

echo.
pause
