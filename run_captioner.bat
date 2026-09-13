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
echo   VoxStream Live Captioner Launcher
echo =======================================================
echo.

:: 1. Verify virtual environment exists
if not exist "%VENV_PY%" (
    echo [INFO] Virtual environment not found or incomplete.
    echo [INFO] Launching setup_windows.bat now...
    echo.
    call "%ROOT_DIR%\setup_windows.bat"
)

if not exist "%VENV_PY%" (
    echo.
    echo =======================================================
    echo   [ERROR] Virtual environment is missing or incomplete!
    echo   Setup did not finish creating .venv\Scripts\python.exe.
    echo.
    echo   Please run setup_windows.bat directly to see the error.
    echo =======================================================
    echo.
    echo Press any key to close this window...
    pause >nul
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
echo =======================================================
echo   Starting VoxStream Live Captioner Backend...
echo =======================================================
call "%VENV_PY%" -m obs_captioner.main %*
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
exit /b !APP_EXIT_CODE!
