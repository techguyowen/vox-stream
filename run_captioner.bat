@echo off
setlocal enabledelayedexpansion
set VOXSTREAM_RUNNER=bat

:: Ensure console and Python use UTF-8 encoding on Windows
chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo [INFO] Virtual environment not found. Running setup first...
    call setup_windows.bat
)

call .venv\Scripts\activate.bat

:: Safeguard: Prepend NVIDIA CUDA/cuDNN DLLs into PATH if present
for /d %%D in (".venv\Lib\site-packages\nvidia\*") do (
    if exist "%%D\bin" (
        set "PATH=%%D\bin;!PATH!"
    )
)

:: Safeguard: Remove conflicting torchaudio binaries if present
if exist ".venv\Lib\site-packages\torchaudio" (
    echo [INFO] Resolving library conflict: removing torchaudio...
    call python -m pip uninstall -y torchaudio >nul 2>&1
)

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
