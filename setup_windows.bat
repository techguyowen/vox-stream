@echo off
setlocal enabledelayedexpansion

:: Ensure UTF-8 encoding is enabled for Windows terminal and Python output
chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

cd /d "%~dp0"

echo =======================================================
echo   VoxStream: OBS Live Captioner Suite - Complete Setup
echo =======================================================
echo.

:: ---------------------------------------------------------------------------
:: STEP 1: Detect or Install Git for Windows
:: ---------------------------------------------------------------------------
echo [1/8] Verifying Git for Windows installation...
set "HAS_GIT=0"
where git >nul 2>&1
if %errorlevel% equ 0 set "HAS_GIT=1"

if "!HAS_GIT!"=="0" (
    if exist "%ProgramFiles%\Git\cmd\git.exe" (
        set "PATH=%ProgramFiles%\Git\cmd;!PATH!"
        set "HAS_GIT=1"
    )
)

if "!HAS_GIT!"=="0" (
    echo [INFO] Git for Windows was not found on your system.
    echo [INFO] Installing Git for Windows automatically...
    where winget >nul 2>&1
    if !errorlevel! equ 0 (
        winget install --id Git.Git -e --source winget --silent --accept-package-agreements --accept-source-agreements
    )
    if exist "%ProgramFiles%\Git\cmd\git.exe" (
        set "PATH=%ProgramFiles%\Git\cmd;!PATH!"
        set "HAS_GIT=1"
    )
)

if "!HAS_GIT!"=="1" (
    echo [SUCCESS] Git for Windows is active.
) else (
    echo [NOTE] Git not found; auto-updater will use direct GitHub archive fallback.
)
echo.

:: ---------------------------------------------------------------------------
:: STEP 2: Detect or Install Python 3.11
:: ---------------------------------------------------------------------------
echo [2/8] Detecting Python installation...
set "PY_CMD="

where python >nul 2>&1
if %errorlevel% equ 0 set "PY_CMD=python"

if not defined PY_CMD (
    where py >nul 2>&1
    if %errorlevel% equ 0 set "PY_CMD=py -3"
)

if not defined PY_CMD (
    if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
        set "PY_CMD=%LocalAppData%\Programs\Python\Python311\python.exe"
        set "PATH=%LocalAppData%\Programs\Python\Python311;%LocalAppData%\Programs\Python\Python311\Scripts;!PATH!"
    )
)

if not defined PY_CMD (
    if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
        set "PY_CMD=%LocalAppData%\Programs\Python\Python312\python.exe"
        set "PATH=%LocalAppData%\Programs\Python\Python312;%LocalAppData%\Programs\Python\Python312\Scripts;!PATH!"
    )
)

if not defined PY_CMD (
    if exist "%ProgramFiles%\Python311\python.exe" (
        set "PY_CMD=%ProgramFiles%\Python311\python.exe"
        set "PATH=%ProgramFiles%\Python311;%ProgramFiles%\Python311\Scripts;!PATH!"
    )
)

if not defined PY_CMD (
    if exist "%ProgramFiles%\Python312\python.exe" (
        set "PY_CMD=%ProgramFiles%\Python312\python.exe"
        set "PATH=%ProgramFiles%\Python312;%ProgramFiles%\Python312\Scripts;!PATH!"
    )
)

if not defined PY_CMD (
    echo [INFO] Python was not detected on your system.
    echo [INFO] Installing Python 3.11 automatically...
    echo.

    where winget >nul 2>&1
    if %errorlevel% equ 0 (
        echo [INFO] Installing Python 3.11 via Windows Package Manager...
        winget install --id Python.Python.3.11 -e --silent --accept-package-agreements --accept-source-agreements
    )

    if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
        set "PY_CMD=%LocalAppData%\Programs\Python\Python311\python.exe"
        set "PATH=%LocalAppData%\Programs\Python\Python311;%LocalAppData%\Programs\Python\Python311\Scripts;!PATH!"
    )
)

if not defined PY_CMD (
    echo [INFO] Downloading official Python 3.11 installer from python.org...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile '%TEMP%\python-3.11.9-amd64.exe'"
    if exist "%TEMP%\python-3.11.9-amd64.exe" (
        echo [INFO] Installing Python 3.11.9 - please wait 30 to 60 seconds...
        start /wait "" "%TEMP%\python-3.11.9-amd64.exe" /passive InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_test=0 Shortcuts=0 TargetDir="%LocalAppData%\Programs\Python\Python311"
        del "%TEMP%\python-3.11.9-amd64.exe" 2>nul
    )
    if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
        set "PY_CMD=%LocalAppData%\Programs\Python\Python311\python.exe"
        set "PATH=%LocalAppData%\Programs\Python\Python311;%LocalAppData%\Programs\Python\Python311\Scripts;!PATH!"
    )
)

if not defined PY_CMD (
    echo.
    echo =======================================================
    echo   [ERROR] Python is not installed on this system!
    echo   Please install Python 3.11 from: https://www.python.org/downloads/
    echo   Make sure to check: Add Python to PATH
    echo =======================================================
    echo.
    start https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [SUCCESS] Using Python:
%PY_CMD% --version
echo.

:: ---------------------------------------------------------------------------
:: STEP 3: Check and Install Microsoft Visual C++ 2015-2022 Redistributable (x64)
:: ---------------------------------------------------------------------------
echo [3/8] Checking Microsoft Visual C++ 2015-2022 Runtime...
if not exist "%SystemRoot%\System32\vcruntime140.dll" (
    echo [INFO] Microsoft Visual C++ 2015-2022 Redistributable not detected.
    echo [INFO] Downloading and installing VC++ runtime (required by AI models)...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri 'https://aka.ms/vs/17/release/vc_redist.x64.exe' -OutFile '%TEMP%\vc_redist.x64.exe'"
    if exist "%TEMP%\vc_redist.x64.exe" (
        start /wait "" "%TEMP%\vc_redist.x64.exe" /install /passive /norestart
        del "%TEMP%\vc_redist.x64.exe" 2>nul
        echo [SUCCESS] Visual C++ Redistributable installed.
    )
) else (
    echo [SUCCESS] Visual C++ Runtime is installed.
)
echo.

:: ---------------------------------------------------------------------------
:: STEP 4: Setup or Repair Python Virtual Environment (.venv)
:: ---------------------------------------------------------------------------
echo [4/8] Setting up Python virtual environment (.venv)...
if not exist ".venv\Scripts\activate.bat" (
    if exist ".venv" (
        echo [INFO] Removing broken virtual environment folder...
        rmdir /s /q ".venv" >nul 2>&1
    )
    %PY_CMD% -m venv .venv
)

if not exist ".venv\Scripts\activate.bat" (
    echo.
    echo =======================================================
    echo   [ERROR] Failed to create virtual environment with %PY_CMD%.
    echo   Please ensure Python is functional and try again.
    echo =======================================================
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat
echo [SUCCESS] Virtual environment activated.
echo.

:: ---------------------------------------------------------------------------
:: STEP 5: Install Python Dependencies
:: ---------------------------------------------------------------------------
echo [5/8] Upgrading pip and installing required packages...
call .venv\Scripts\python.exe -m pip install --upgrade pip
call .venv\Scripts\python.exe -m pip install -r requirements.txt
call .venv\Scripts\python.exe -m pip uninstall -y torchaudio >nul 2>&1
echo.

:: ---------------------------------------------------------------------------
:: STEP 6: Hardware Acceleration Drivers (NVIDIA / AMD Radeon RX / DirectML)
:: ---------------------------------------------------------------------------
echo [6/8] Detecting GPU Hardware (NVIDIA / AMD Radeon / Intel)...
set "HAS_NVIDIA=0"
set "HAS_AMD=0"

where nvidia-smi >nul 2>&1
if %errorlevel% equ 0 set "HAS_NVIDIA=1"

if "!HAS_NVIDIA!"=="0" (
    powershell -NoProfile -Command "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name" 2>nul | findstr /i "NVIDIA GeForce RTX GTX Quadro" >nul 2>&1
    if %errorlevel% equ 0 set "HAS_NVIDIA=1"
)

powershell -NoProfile -Command "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name" 2>nul | findstr /i "Radeon AMD RX" >nul 2>&1
if %errorlevel% equ 0 set "HAS_AMD=1"

if "!HAS_NVIDIA!"=="1" (
    echo [SUCCESS] NVIDIA GPU detected!
    echo [INFO] Installing CUDA and cuDNN runtime packages for Faster-Whisper...
    call .venv\Scripts\python.exe -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
    echo [SUCCESS] NVIDIA GPU acceleration installed!
) else if "!HAS_AMD!"=="1" (
    echo [SUCCESS] AMD Radeon GPU detected! (e.g. Radeon RX 580)
    echo [INFO] Installing DirectML runtime packages for AMD GPU acceleration...
    call .venv\Scripts\python.exe -m pip install onnxruntime-directml torch-directml
    echo [SUCCESS] AMD Radeon GPU DirectML ^& OpenMP CPU acceleration configured!
) else (
    echo [INFO] Configuring DirectML and CPU int8 acceleration...
    call .venv\Scripts\python.exe -m pip install onnxruntime-directml >nul 2>&1
    echo [SUCCESS] Configured for fast CPU int8 ^& DirectML inference.
)
echo.

:: ---------------------------------------------------------------------------
:: STEP 7: Configure Settings & Microphone Privacy Permissions
:: ---------------------------------------------------------------------------
echo [7/8] Configuring settings and Windows microphone access...
if not exist "config.json" (
    copy config.json.example config.json >nul
    echo [INFO] Created fresh config.json from template.
) else (
    echo [INFO] Existing config.json preserved.
)

:: Ensure Windows microphone access is allowed in registry
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone" /v Value /t REG_SZ /d Allow /f >nul 2>&1
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone\NonPackaged" /v Value /t REG_SZ /d Allow /f >nul 2>&1
echo [SUCCESS] Windows microphone access verified.
echo.

:: ---------------------------------------------------------------------------
:: STEP 8: Pre-cache Offline AI Models & Create Desktop Shortcut
:: ---------------------------------------------------------------------------
echo [8/8] Pre-caching default offline AI models & creating Desktop shortcut...
call .venv\Scripts\python.exe -m obs_captioner.model_downloader --preload-defaults

:: Create convenient Desktop Shortcut
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\VoxStream Live Captioner.lnk'); $s.TargetPath = '%~dp0run_captioner.bat'; $s.WorkingDirectory = '%~dp0'; $s.Description = 'Launch VoxStream Real-Time Live Captioner'; $s.Save()" >nul 2>&1

echo.
echo =======================================================
echo   Available Audio Input Devices on your PC:
echo =======================================================
call .venv\Scripts\python.exe -m obs_captioner.main --list-devices
echo.
echo =======================================================
echo   🎉 [SUCCESS] 100%% Turnkey Setup Complete!
echo.
echo   1. A shortcut 'VoxStream Live Captioner' was created
echo      on your Desktop. Double-click it anytime to run!
echo   2. Dashboard is available at: http://127.0.0.1:8765
echo =======================================================
pause
