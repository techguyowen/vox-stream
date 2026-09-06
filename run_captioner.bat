@echo off
setlocal enabledelayedexpansion
set "VOXSTREAM_RUNNER=bat"

:: Ensure console and Python use UTF-8 encoding on Windows
chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

cd /d "%~dp0"

echo =======================================================
echo   VoxStream Live Captioner Launcher
echo =======================================================
echo.

:: 1. Verify virtual environment exists
if not exist ".venv\Scripts\activate.bat" (
    echo [INFO] Virtual environment not found or incomplete.
    echo [INFO] Launching setup_windows.bat now...
    echo.
    call setup_windows.bat
)

if not exist ".venv\Scripts\activate.bat" (
    echo.
    echo =======================================================
    echo   [ERROR] Virtual environment is missing or incomplete!
    echo   Setup did not finish creating .venv\Scripts\activate.bat.
    echo.
    echo   Please run setup_windows.bat directly to see the error.
    echo =======================================================
    echo.
    pause
    exit /b 1
)

:: 2. Activate virtual environment
call .venv\Scripts\activate.bat

:: 3. Prepend NVIDIA CUDA/cuDNN DLLs into PATH if present
if exist ".venv\Lib\site-packages\nvidia" (
    for /d %%D in (.venv\Lib\site-packages\nvidia\*) do (
        if exist "%%D\bin" (
            set "PATH=%%D\bin;!PATH!"
        )
    )
)

:: 4. Resolve library conflict: remove torchaudio if present
if exist ".venv\Lib\site-packages\torchaudio" (
    echo [INFO] Resolving library conflict: removing torchaudio...
    call .venv\Scripts\python.exe -m pip uninstall -y torchaudio >nul 2>&1
)

:: 5. Main application loop (supports exit code 42 instant reload)
:app_loop
echo =======================================================
echo   Starting VoxStream Live Captioner Backend...
echo =======================================================
call .venv\Scripts\python.exe -m obs_captioner.main %*
set "APP_EXIT_CODE=!errorlevel!"

:: Exit code 42 indicates an intentional application restart
if "!APP_EXIT_CODE!"=="42" (
    echo.
    echo [VoxStream] Application restart requested. Reloading...
    timeout /t 1 /nobreak >nul
    goto app_loop
)

:: Any other exit code (including 0 or crash) - ALWAYS pause so the window does not close!
echo.
echo =======================================================
if not "!APP_EXIT_CODE!"=="0" (
    echo   [ERROR] VoxStream stopped with exit code: !APP_EXIT_CODE!
) else (
    echo   [INFO] VoxStream stopped normally.
)
echo   Press any key to close this window...
echo =======================================================
pause >nul
