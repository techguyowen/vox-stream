@echo off
setlocal EnableDelayedExpansion
set "VOXSTREAM_RUNNER=launcher_bat"

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

cd /d "%~dp0"
set "ROOT_DIR=%~dp0"
if "%ROOT_DIR:~-1%"=="\" set "ROOT_DIR=%ROOT_DIR:~0,-1%"
set "VENV_DIR=%ROOT_DIR%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo [INFO] Virtual environment not found. Running setup_windows.bat...
    call "%ROOT_DIR%\setup_windows.bat"
)

if not exist "%VENV_PY%" (
    echo [ERROR] Virtual environment is missing or incomplete!
    pause
    exit /b 1
)

:: Prepend GPU DLLs if present
if exist "%VENV_DIR%\Lib\site-packages\nvidia" (
    for /d %%D in ("%VENV_DIR%\Lib\site-packages\nvidia\*") do (
        if exist "%%~D\bin" (
            set "PATH=%%~D\bin;!PATH!"
        )
    )
)
if exist "%VENV_DIR%\Lib\site-packages\torch_directml" (
    set "PATH=%VENV_DIR%\Lib\site-packages\torch_directml;!PATH!"
)
if exist "%VENV_DIR%\Lib\site-packages\onnxruntime\capi" (
    set "PATH=%VENV_DIR%\Lib\site-packages\onnxruntime\capi;!PATH!"
)

call "%VENV_PY%" -m obs_captioner.launcher %*
exit /b %errorlevel%
