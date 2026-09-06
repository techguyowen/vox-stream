@echo off
setlocal enabledelayedexpansion

:: Ensure UTF-8 encoding is enabled for Windows terminal and Python output
chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

cd /d "%~dp0"

echo =======================================================
echo   VoxStream: OBS Live Captioner Suite - Windows Setup
echo =======================================================
echo.

:: 1. Check for existing Python installation
set "PY_CMD="

where python >nul 2>&1
if %errorlevel% equ 0 (
    set "PY_CMD=python"
)

if not defined PY_CMD (
    where py >nul 2>&1
    if %errorlevel% equ 0 (
        set "PY_CMD=py -3"
    )
)

if not defined PY_CMD (
    if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
        set "PY_CMD=%LocalAppData%\Programs\Python\Python311\python.exe"
        set "PATH=%LocalAppData%\Programs\Python\Python311;%LocalAppData%\Programs\Python\Python311\Scripts;%PATH%"
    ) else if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
        set "PY_CMD=%LocalAppData%\Programs\Python\Python312\python.exe"
        set "PATH=%LocalAppData%\Programs\Python\Python312;%LocalAppData%\Programs\Python\Python312\Scripts;%PATH%"
    ) else if exist "%ProgramFiles%\Python311\python.exe" (
        set "PY_CMD=%ProgramFiles%\Python311\python.exe"
        set "PATH=%ProgramFiles%\Python311;%ProgramFiles%\Python311\Scripts;%PATH%"
    ) else if exist "%ProgramFiles%\Python312\python.exe" (
        set "PY_CMD=%ProgramFiles%\Python312\python.exe"
        set "PATH=%ProgramFiles%\Python312;%ProgramFiles%\Python312\Scripts;%PATH%"
    )
)

:: 2. If Python is still not found, download and install Python 3.11 automatically
if not defined PY_CMD (
    echo [INFO] Python was not detected on your system.
    echo [INFO] Attempting automatic installation of Python 3.11...
    echo.

    where winget >nul 2>&1
    if %errorlevel% equ 0 (
        echo [INFO] Installing Python 3.11 via Windows Package Manager (winget)...
        winget install --id Python.Python.3.11 -e --silent --accept-package-agreements --accept-source-agreements
    )

    if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
        set "PY_CMD=%LocalAppData%\Programs\Python\Python311\python.exe"
        set "PATH=%LocalAppData%\Programs\Python\Python311;%LocalAppData%\Programs\Python\Python311\Scripts;%PATH%"
    ) else (
        echo [INFO] Downloading official Python 3.11 from python.org...
        powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile '%TEMP%\python-3.11.9-amd64.exe'"
        if exist "%TEMP%\python-3.11.9-amd64.exe" (
            echo [INFO] Installing Python 3.11.9 (please wait 30-60 seconds)...
            start /wait "" "%TEMP%\python-3.11.9-amd64.exe" /passive InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_test=0 Shortcuts=0 TargetDir="%LocalAppData%\Programs\Python\Python311"
            del "%TEMP%\python-3.11.9-amd64.exe" 2>nul
        ) else (
            echo [ERROR] Failed to download Python installer automatically.
            echo Please manually download and install Python 3.11 from: https://www.python.org/downloads/
            echo (Make sure to check the box Add Python to PATH)
            start https://www.python.org/downloads/
            pause
            exit /b 1
        )
    )

    if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
        set "PY_CMD=%LocalAppData%\Programs\Python\Python311\python.exe"
        set "PATH=%LocalAppData%\Programs\Python\Python311;%LocalAppData%\Programs\Python\Python311\Scripts;%PATH%"
    ) else if exist "%ProgramFiles%\Python311\python.exe" (
        set "PY_CMD=%ProgramFiles%\Python311\python.exe"
        set "PATH=%ProgramFiles%\Python311;%ProgramFiles%\Python311\Scripts;%PATH%"
    ) else (
        set "PY_CMD=python"
    )
)

echo [SUCCESS] Using Python:
%PY_CMD% --version
echo.

:: 3. Setup or repair virtual environment
echo [1/6] Setting up virtual environment (.venv)...
if not exist ".venv\Scripts\activate.bat" (
    if exist ".venv" (
        echo [INFO] Cleaning up incomplete virtual environment folder...
        rmdir /s /q ".venv" >nul 2>&1
    )
    %PY_CMD% -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment with %PY_CMD%.
        pause
        exit /b 1
    )
)

if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] .venv\Scripts\activate.bat was not created successfully.
    pause
    exit /b 1
)

:: 4. Activate virtual environment
echo [2/6] Activating virtual environment...
call .venv\Scripts\activate.bat

:: 5. Install dependencies
echo [3/6] Upgrading pip and installing required packages...
call .venv\Scripts\python.exe -m pip install --upgrade pip
call .venv\Scripts\python.exe -m pip install -r requirements.txt
call .venv\Scripts\python.exe -m pip uninstall -y torchaudio >nul 2>&1

:: 6. Check for NVIDIA GPU and install CUDA acceleration automatically
echo [4/6] Checking for NVIDIA GPU acceleration...
set "HAS_NVIDIA=0"
nvidia-smi >nul 2>&1
if %errorlevel% equ 0 (
    set "HAS_NVIDIA=1"
) else (
    powershell -NoProfile -Command "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name" 2>nul | findstr /i "NVIDIA GeForce RTX GTX Quadro" >nul 2>&1
    if %errorlevel% equ 0 (
        set "HAS_NVIDIA=1"
    )
)

if "!HAS_NVIDIA!"=="1" (
    echo [SUCCESS] NVIDIA GPU detected!
    echo [INFO] Installing CUDA and cuDNN runtime packages for Faster-Whisper...
    call .venv\Scripts\python.exe -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
    echo [SUCCESS] NVIDIA GPU acceleration installed!
) else (
    echo [INFO] No dedicated NVIDIA GPU detected. Configured for fast CPU int8 inference.
)

:: 7. Setup Configuration
echo [5/6] Verifying configuration file...
if not exist "config.json" (
    copy config.json.example config.json >nul
    echo Created fresh config.json from template.
) else (
    echo Existing config.json preserved.
)

:: 8. Preload default offline AI models
echo [6/6] Pre-caching default offline AI speech models...
call .venv\Scripts\python.exe -m obs_captioner.model_downloader --preload-defaults

echo.
echo =======================================================
echo   Available Audio Input Devices on your PC:
echo =======================================================
call .venv\Scripts\python.exe -m obs_captioner.main --list-devices
echo.
echo =======================================================
echo   [SUCCESS] Setup Complete!
echo   1. Double-click run_captioner.bat to start VoxStream.
echo   2. Open In-OBS Dock at: http://127.0.0.1:8765/dashboard
echo =======================================================
pause
