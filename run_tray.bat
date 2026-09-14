@echo off
setlocal EnableDelayedExpansion
set "VOXSTREAM_RUNNER=bat"

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

cd /d "%~dp0"
set "ROOT_DIR=%~dp0"
if "%ROOT_DIR:~-1%"=="\" set "ROOT_DIR=%ROOT_DIR:~0,-1%"
set "VENV_DIR=%ROOT_DIR%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

echo =======================================================
echo   VoxStream Live Captioner with Windows System Tray
echo =======================================================
echo.

:: 1. Verify virtual environment exists
if not exist "%VENV_PY%" (
    echo [INFO] Virtual environment not found. Launching setup_windows.bat...
    call "%ROOT_DIR%\setup_windows.bat"
)

if not exist "%VENV_PY%" (
    echo [ERROR] Virtual environment is missing or incomplete!
    pause
    exit /b 1
)

:: 2. Prepend NVIDIA CUDA/cuDNN DLLs into PATH if present
if exist "%VENV_DIR%\Lib\site-packages\nvidia" (
    for /d %%D in ("%VENV_DIR%\Lib\site-packages\nvidia\*") do (
        if exist "%%~D\bin" (
            set "PATH=%%~D\bin;!PATH!"
        )
    )
)

:: 3. Prepend DirectML DLLs for AMD Radeon GPUs if present
if exist "%VENV_DIR%\Lib\site-packages\torch_directml" (
    set "PATH=%VENV_DIR%\Lib\site-packages\torch_directml;!PATH!"
)
if exist "%VENV_DIR%\Lib\site-packages\onnxruntime\capi" (
    set "PATH=%VENV_DIR%\Lib\site-packages\onnxruntime\capi;!PATH!"
)

:: 4. Resolve library conflict: remove torchaudio if present
if exist "%VENV_DIR%\Lib\site-packages\torchaudio" (
    echo [INFO] Resolving library conflict: removing torchaudio...
    call "%VENV_PY%" -m pip uninstall -y torchaudio >nul 2>&1
)

:: 5. Main application loop (supports exit code 42 instant reload)
:app_loop
echo [INFO] Starting VoxStream with Taskbar System Tray active...
echo [INFO] Look for the VoxStream icon in the Windows notification area (bottom-right).
call "%VENV_PY%" -m obs_captioner.main --tray %*
set "APP_EXIT_CODE=!errorlevel!"

:: Exit code 42 indicates an intentional application restart
if "!APP_EXIT_CODE!"=="42" (
    echo.
    echo [VoxStream] Application restart requested. Verifying dependencies and reloading...
    if exist "%ROOT_DIR%\requirements.txt" (
        if exist "%VENV_DIR%\Scripts\uv.exe" (
            call "%VENV_DIR%\Scripts\uv.exe" pip install -q -r "%ROOT_DIR%\requirements.txt"
        ) else (
            call "%VENV_PY%" -m pip install -q -r "%ROOT_DIR%\requirements.txt"
        )
    )
    timeout /t 1 /nobreak >nul
    goto app_loop
)

if not "!APP_EXIT_CODE!"=="0" (
    echo [ERROR] VoxStream stopped with exit code: !APP_EXIT_CODE!
    pause
)
exit /b !APP_EXIT_CODE!
