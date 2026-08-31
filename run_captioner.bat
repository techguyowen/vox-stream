@echo off
setlocal

cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo [INFO] Virtual environment not found. Running setup first...
    call setup_windows.bat
)

call .venv\Scripts\activate.bat

:app_loop
echo =======================================================
echo   Starting VoxStream Live Captioner...
echo =======================================================
python -m obs_captioner.main %*
set APP_EXIT_CODE=%errorlevel%

:: Exit code 42 indicates an intentional application restart
if %APP_EXIT_CODE% EQU 42 (
    echo.
    echo [VoxStream] Application restart requested. Reloading...
    timeout /t 1 /nobreak >nul
    goto app_loop
)

if %APP_EXIT_CODE% NEQ 0 (
    echo.
    echo [ERROR] Captioner stopped with an error code: %APP_EXIT_CODE%
    pause
)
