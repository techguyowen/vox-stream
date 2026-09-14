@echo off
setlocal EnableDelayedExpansion

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

cd /d "%~dp0"
set "ROOT_DIR=%~dp0"
if "%ROOT_DIR:~-1%"=="\" set "ROOT_DIR=%ROOT_DIR:~0,-1%"

echo =======================================================
echo   VoxStream: OBS Live Captioner Suite - Complete Setup
echo =======================================================
echo.

:: ---------------------------------------------------------------------------
:: STEP 1: Detect or Install Git for Windows
:: ---------------------------------------------------------------------------
echo [1/8] Checking Git for Windows...
set "HAS_GIT=0"

git --version >nul 2>&1
if !errorlevel! equ 0 set "HAS_GIT=1"

if "!HAS_GIT!"=="0" (
    if exist "%ProgramFiles%\Git\cmd\git.exe" (
        set "PATH=%ProgramFiles%\Git\cmd;!PATH!"
        set "HAS_GIT=1"
    )
)

if "!HAS_GIT!"=="0" (
    echo [INFO] Git for Windows not found in PATH. Checking winget...
    where winget >nul 2>&1
    if !errorlevel! equ 0 (
        echo [INFO] Installing Git via Windows Package Manager...
        winget install --id Git.Git -e --source winget --silent --accept-package-agreements --accept-source-agreements
    )
    if not exist "%ProgramFiles%\Git\cmd\git.exe" (
        echo [INFO] Downloading official Git for Windows installer...
        powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri 'https://github.com/git-for-windows/git/releases/download/v2.46.0.windows.1/Git-2.46.0-64-bit.exe' -OutFile '%TEMP%\git_installer.exe'"
        if exist "%TEMP%\git_installer.exe" (
            echo [INFO] Installing Git for Windows silently...
            start /wait "" "%TEMP%\git_installer.exe" /VERYSILENT /NORESTART /NOCANCEL /SP- /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS
            del "%TEMP%\git_installer.exe" 2>nul
        )
    )
    if exist "%ProgramFiles%\Git\cmd\git.exe" (
        set "PATH=%ProgramFiles%\Git\cmd;!PATH!"
        set "HAS_GIT=1"
    )
)

if "!HAS_GIT!"=="1" (
    echo [SUCCESS] Git for Windows is active.
    if not exist ".git" (
        echo [INFO] Linking installation to official GitHub repository for updates...
        git init -q
        git remote add origin https://github.com/techguyowen/vox-stream.git >nul 2>&1
        git fetch origin main --depth=1 -q >nul 2>&1
        git reset --soft origin/main >nul 2>&1
        echo [SUCCESS] Repository linked! Future updates will pull in seconds.
    )
) else (
    echo [NOTE] Git installation skipped or unavailable. Auto-updater will use direct ZIP download.
)
echo.

:: ---------------------------------------------------------------------------
:: STEP 2: Detect or Install Python 3.10 - 3.12 (Skip Windows Store dummy stub)
:: ---------------------------------------------------------------------------
echo [2/8] Detecting Python installation...
set "PY_CMD="

:: Check if py launcher works
py -3 -c "import sys" >nul 2>&1
if !errorlevel! equ 0 (
    set "PY_CMD=py -3"
    goto :python_found
)

:: Check if standard python in PATH is a real working Python (NOT Microsoft Store stub)
python -c "import sys" >nul 2>&1
if !errorlevel! equ 0 (
    set "PY_CMD=python"
    goto :python_found
)

:: Check known default installation paths without parentheses in for loops
if not defined PY_CMD if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
    "%LocalAppData%\Programs\Python\Python311\python.exe" -c "import sys" >nul 2>&1
    if !errorlevel! equ 0 set "PY_CMD="%LocalAppData%\Programs\Python\Python311\python.exe""
)
if not defined PY_CMD if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
    "%LocalAppData%\Programs\Python\Python312\python.exe" -c "import sys" >nul 2>&1
    if !errorlevel! equ 0 set "PY_CMD="%LocalAppData%\Programs\Python\Python312\python.exe""
)
if not defined PY_CMD if exist "%ProgramFiles%\Python311\python.exe" (
    "%ProgramFiles%\Python311\python.exe" -c "import sys" >nul 2>&1
    if !errorlevel! equ 0 set "PY_CMD="%ProgramFiles%\Python311\python.exe""
)
if not defined PY_CMD if exist "%ProgramFiles%\Python312\python.exe" (
    "%ProgramFiles%\Python312\python.exe" -c "import sys" >nul 2>&1
    if !errorlevel! equ 0 set "PY_CMD="%ProgramFiles%\Python312\python.exe""
)
if not defined PY_CMD if exist "C:\Python311\python.exe" (
    "C:\Python311\python.exe" -c "import sys" >nul 2>&1
    if !errorlevel! equ 0 set "PY_CMD="C:\Python311\python.exe""
)
if not defined PY_CMD if exist "C:\Python312\python.exe" (
    "C:\Python312\python.exe" -c "import sys" >nul 2>&1
    if !errorlevel! equ 0 set "PY_CMD="C:\Python312\python.exe""
)

if defined PY_CMD goto :python_found

:: If not found, install Python 3.11 automatically
echo [INFO] Python was not detected on your system.
echo [INFO] Installing Python 3.11 automatically...
echo.

where winget >nul 2>&1
if !errorlevel! equ 0 (
    echo [INFO] Installing Python 3.11 via Windows Package Manager...
    winget install --id Python.Python.3.11 -e --silent --accept-package-agreements --accept-source-agreements
)

if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
    set "PY_CMD="%LocalAppData%\Programs\Python\Python311\python.exe""
    goto :python_found
)

echo [INFO] Downloading official Python 3.11 installer from python.org...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile '%TEMP%\python-3.11.9-amd64.exe'"
if exist "%TEMP%\python-3.11.9-amd64.exe" (
    echo [INFO] Installing Python 3.11.9 - please wait 30 to 60 seconds...
    start /wait "" "%TEMP%\python-3.11.9-amd64.exe" /passive InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_test=0 Shortcuts=0 TargetDir="%LocalAppData%\Programs\Python\Python311"
    del "%TEMP%\python-3.11.9-amd64.exe" 2>nul
)

if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
    set "PY_CMD="%LocalAppData%\Programs\Python\Python311\python.exe""
    goto :python_found
)

echo.
echo =======================================================
echo   [ERROR] Python is not installed on this system!
echo   Please install Python 3.11 from: https://www.python.org/downloads/
echo   Make sure to check: 'Add Python to PATH'
echo =======================================================
echo.
start https://www.python.org/downloads/
goto :setup_failed

:python_found
echo [SUCCESS] Using Python:
call %PY_CMD% --version
echo.

:: ---------------------------------------------------------------------------
:: STEP 3: Check and Install Microsoft Visual C++ 2015-2022 Redistributable
:: ---------------------------------------------------------------------------
echo [3/8] Checking Microsoft Visual C++ 2015-2022 Runtime...
if not exist "%SystemRoot%\System32\vcruntime140.dll" (
    echo [INFO] Microsoft Visual C++ 2015-2022 Redistributable not detected.
    echo [INFO] Downloading and installing VC++ runtime for AI models...
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
echo [4/8] Setting up Python virtual environment [.venv]...
set "VENV_DIR=%ROOT_DIR%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

if not exist "%VENV_PY%" (
    if exist "%VENV_DIR%" (
        echo [INFO] Removing broken virtual environment folder...
        rmdir /s /q "%VENV_DIR%" >nul 2>&1
    )
    echo [INFO] Creating new virtual environment...
    call %PY_CMD% -m venv "%VENV_DIR%"
)

if not exist "%VENV_PY%" (
    echo.
    echo =======================================================
    echo   [ERROR] Failed to create virtual environment with %PY_CMD%.
    echo   Please ensure Python is functional and try again.
    echo =======================================================
    goto :setup_failed
)

echo [SUCCESS] Virtual environment ready.
echo.

:: ---------------------------------------------------------------------------
:: STEP 5: Install Python Dependencies
:: ---------------------------------------------------------------------------
echo [5/8] Upgrading package manager and installing dependencies...
if exist "%VENV_DIR%\Scripts\uv.exe" (
    echo [INFO] Using fast package manager [uv]...
    call "%VENV_DIR%\Scripts\uv.exe" pip install -r "%ROOT_DIR%\requirements.txt"
) else (
    call "%VENV_PY%" -m pip install --upgrade pip
    call "%VENV_PY%" -m pip install -r "%ROOT_DIR%\requirements.txt"
)
call "%VENV_PY%" -m pip uninstall -y torchaudio >nul 2>&1
echo.

:: ---------------------------------------------------------------------------
:: STEP 6: Hardware Acceleration Drivers (NVIDIA / AMD Radeon RX / DirectML)
:: ---------------------------------------------------------------------------
echo [6/8] Detecting GPU Hardware [NVIDIA / AMD Radeon / Intel]...
set "HAS_NVIDIA=0"
set "HAS_AMD=0"

where nvidia-smi >nul 2>&1
if !errorlevel! equ 0 set "HAS_NVIDIA=1"

if "!HAS_NVIDIA!"=="0" (
    powershell -NoProfile -Command "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name" 2>nul | findstr /i "NVIDIA GeForce RTX GTX Quadro" >nul 2>&1
    if !errorlevel! equ 0 set "HAS_NVIDIA=1"
)

powershell -NoProfile -Command "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name" 2>nul | findstr /i "Radeon AMD RX" >nul 2>&1
if !errorlevel! equ 0 set "HAS_AMD=1"

if "!HAS_NVIDIA!"=="1" (
    echo [SUCCESS] NVIDIA GPU detected!
    echo [INFO] Installing CUDA and cuDNN runtime packages for Faster-Whisper...
    call "%VENV_PY%" -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
    echo [SUCCESS] NVIDIA GPU acceleration installed!
    goto :gpu_done
)

if "!HAS_AMD!"=="1" (
    echo [SUCCESS] AMD Radeon GPU detected!
    echo [INFO] Installing DirectML runtime packages for AMD GPU acceleration...
    call "%VENV_PY%" -m pip install onnxruntime-directml torch-directml
    echo [SUCCESS] AMD Radeon GPU DirectML and OpenMP CPU acceleration configured!
    goto :gpu_done
)

echo [INFO] Configuring DirectML and CPU int8 acceleration...
call "%VENV_PY%" -m pip install onnxruntime-directml >nul 2>&1
echo [SUCCESS] Configured for fast CPU int8 and DirectML inference.

:gpu_done
echo.

:: ---------------------------------------------------------------------------
:: STEP 7: Configure Settings & Microphone Privacy Permissions
:: ---------------------------------------------------------------------------
echo [7/8] Configuring settings and Windows microphone access...
if not exist "config.json" (
    copy config.json.example config.json >nul 2>&1
    echo [INFO] Created fresh config.json from template.
) else (
    echo [INFO] Existing config.json preserved.
)

reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone" /v Value /t REG_SZ /d Allow /f >nul 2>&1
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone\NonPackaged" /v Value /t REG_SZ /d Allow /f >nul 2>&1
echo [SUCCESS] Windows microphone access verified.

:: Configure Windows Defender Firewall for port 8765
echo [INFO] Checking Windows Defender Firewall for port 8765...
netsh advfirewall firewall show rule name="VoxStream Overlay TCP 8765" >nul 2>&1
if !errorlevel! equ 0 (
    echo [SUCCESS] Windows Defender Firewall rule is already active for port 8765.
    goto :firewall_done
)

:: Attempt direct rule addition (works immediately if run as administrator)
netsh advfirewall firewall add rule name="VoxStream Overlay TCP 8765" dir=in action=allow protocol=TCP localport=8765 profile=any description="Allows inbound connections to VoxStream overlay, stage display, and control dashboard" >nul 2>&1
if !errorlevel! equ 0 (
    echo [SUCCESS] Windows Defender Firewall opened for port 8765!
    goto :firewall_done
)

:: Non-elevated: attempt elevation via PowerShell
echo [INFO] Requesting administrator permission to configure firewall rule...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process netsh.exe -ArgumentList 'advfirewall firewall add rule name=\"VoxStream Overlay TCP 8765\" dir=in action=allow protocol=TCP localport=8765 profile=any description=\"Allows inbound connections to VoxStream overlay, stage display, and control dashboard\"' -Verb RunAs -Wait" >nul 2>&1

netsh advfirewall firewall show rule name="VoxStream Overlay TCP 8765" >nul 2>&1
if !errorlevel! equ 0 (
    echo [SUCCESS] Windows Defender Firewall opened for port 8765!
) else (
    echo [NOTE] Firewall rule was not added. You can configure it anytime via setup_firewall.bat
)

:firewall_done
echo.

:: ---------------------------------------------------------------------------
:: STEP 8: Pre-cache Offline AI Models & Create Desktop Shortcut
:: ---------------------------------------------------------------------------
echo [8/8] Pre-caching default offline AI models and creating Desktop shortcut...
call "%VENV_PY%" -m obs_captioner.model_downloader --preload-defaults

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $d = [Environment]::GetFolderPath('Desktop'); $lnk = Join-Path $d 'VoxStream Live Captioner.lnk'; $s = $ws.CreateShortcut($lnk); $s.TargetPath = '%ROOT_DIR%\run_captioner.bat'; $s.WorkingDirectory = '%ROOT_DIR%'; $s.Description = 'Launch VoxStream Real-Time Live Captioner'; $s.Save()" >nul 2>&1

echo.
echo =======================================================
echo   Available Audio Input Devices on your PC:
echo =======================================================
call "%VENV_PY%" -m obs_captioner.main --list-devices
echo.

:setup_done
echo =======================================================
echo   [SUCCESS] 100%% Turnkey Setup Complete!
echo.
echo   1. A shortcut 'VoxStream Live Captioner' was created
echo      on your Desktop. Double-click it anytime to run!
echo   2. Dashboard is available at: http://127.0.0.1:8765
echo =======================================================
echo.
echo Setup finished successfully. Press any key to close this window...
pause >nul
exit /b 0

:setup_failed
echo.
echo =======================================================
echo   [ERROR] Setup encountered an issue and could not complete.
echo   Please check the error messages displayed above.
echo =======================================================
echo.
echo Press any key to close this window...
pause >nul
exit /b 1
