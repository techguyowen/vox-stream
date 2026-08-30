@echo off
setlocal

cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo [INFO] Virtual environment not found. Running setup first...
    call setup_windows.bat
)

call .venv\Scripts\activate.bat

echo =======================================================
echo   Starting VoxStream Live Captioner...
echo =======================================================
python -m obs_captioner.main %*

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Captioner stopped with an error code: %errorlevel%
    pause
)
