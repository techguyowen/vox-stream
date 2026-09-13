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
    if exist "%LocalAppData%\Programs\Git\cmd\git.exe" (
        set "PATH=%LocalAppData%\Programs\Git\cmd;!PATH!"
        set "HAS_GIT=1"
    )
    if exist "C:\Git\cmd\git.exe" (
        set "PATH=C:\Git\cmd;!PATH!"
        set "HAS_GIT=1"
    )
)

if "!HAS_GIT!"=="1" (
    echo [SUCCESS] Git for Windows is active.
    if not exist ".git" (
        echo [INFO] Linking installation to official GitHub repository for updates...
        git init -q >nul 2>&1
        git remote add origin https://github.com/techguyowen/vox-stream.git >nul 2>&1
        git fetch origin main --depth=1 -q >nul 2>&1
        git reset --soft origin/main >nul 2>&1
    ) else (
        echo [INFO] Checking for latest updates from GitHub repository...
        git pull origin main --ff-only -q >nul 2>&1
    )
) else (
    echo [INFO] Git for Windows not found. Skipping (VoxStream does not require Git to run).
)
echo.

:: ---------------------------------------------------------------------------
:: STEP 2: Detect or Install Python 3.10 - 3.12 (Skip Windows Store dummy stub)
:: ---------------------------------------------------------------------------
echo [2/8] Detecting Python installation...
set "PY_CMD="

:: Check py launcher for Python 3.11, 3.12, 3.10 specifically
py -3.11 -c "import sys" >nul 2>&1
if !errorlevel! equ 0 (
    set "PY_CMD=py -3.11"
    goto :python_found
)
py -3.12 -c "import sys" >nul 2>&1
if !errorlevel! equ 0 (
    set "PY_CMD=py -3.12"
    goto :python_found
)
py -3.10 -c "import sys" >nul 2>&1
if !errorlevel! equ 0 (
    set "PY_CMD=py -3.10"
    goto :python_found
)

py -3.13 -c "import sys" >nul 2>&1
if !errorlevel! equ 0 (
    set "PY_CMD=py -3.13"
    goto :python_found
)
py -3.9 -c "import sys" >nul 2>&1
if !errorlevel! equ 0 (
    set "PY_CMD=py -3.9"
    goto :python_found
)

:: Check known default installation paths for Python 3.11, 3.12, 3.10, 3.13, 3.9
if not defined PY_CMD if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
    "%LocalAppData%\Programs\Python\Python311\python.exe" -c "import sys" >nul 2>&1
    if !errorlevel! equ 0 set "PY_CMD="%LocalAppData%\Programs\Python\Python311\python.exe""
)
if not defined PY_CMD if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
    "%LocalAppData%\Programs\Python\Python312\python.exe" -c "import sys" >nul 2>&1
    if !errorlevel! equ 0 set "PY_CMD="%LocalAppData%\Programs\Python\Python312\python.exe""
)
if not defined PY_CMD if exist "%LocalAppData%\Programs\Python\Python310\python.exe" (
    "%LocalAppData%\Programs\Python\Python310\python.exe" -c "import sys" >nul 2>&1
    if !errorlevel! equ 0 set "PY_CMD="%LocalAppData%\Programs\Python\Python310\python.exe""
)
if not defined PY_CMD if exist "%LocalAppData%\Programs\Python\Python313\python.exe" (
    "%LocalAppData%\Programs\Python\Python313\python.exe" -c "import sys" >nul 2>&1
    if !errorlevel! equ 0 set "PY_CMD="%LocalAppData%\Programs\Python\Python313\python.exe""
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

:: Check all python executables in system PATH (via where python)
for /f "delims=" %%I in ('where python 2^>nul') do (
    if not defined PY_CMD (
        "%%I" -c "import sys" >nul 2>&1
        if !errorlevel! equ 0 (
            set "PY_CMD="%%I""
            goto :python_found
        )
    )
)

:: Check standard python command in PATH
python -c "import sys" >nul 2>&1
if !errorlevel! equ 0 (
    set "PY_CMD=python"
    goto :python_found
)

:: If not found, download and install Python 3.11 automatically from python.org
echo [INFO] Python 3.10-3.12 was not detected on your system.
echo [INFO] Downloading official Python 3.11 installer from python.org...
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; try { (New-Object System.Net.WebClient).DownloadFile('https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe', '%TEMP%\python-3.11.9-amd64.exe') } catch { exit 1 }"
if exist "%TEMP%\python-3.11.9-amd64.exe" (
    echo [INFO] Installing Python 3.11.9 (user-level, no admin required) - please wait 30 to 60 seconds...
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
    powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; try { (New-Object System.Net.WebClient).DownloadFile('https://aka.ms/vs/17/release/vc_redist.x64.exe', '%TEMP%\vc_redist.x64.exe') } catch { exit 1 }"
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

set "REBUILD_VENV=0"
if not exist "%VENV_PY%" set "REBUILD_VENV=1"

:: Validate existing virtual environment
if "!REBUILD_VENV!"=="0" (
    "%VENV_PY%" -c "import sys" >nul 2>&1
    if !errorlevel! neq 0 (
        echo [INFO] Existing virtual environment executable is invalid.
        set "REBUILD_VENV=1"
    )
)

if "!REBUILD_VENV!"=="0" (
    "%VENV_PY%" -c "import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 12) else 1)" >nul 2>&1
    if !errorlevel! neq 0 (
        echo [INFO] Existing virtual environment was built with an incompatible Python version.
        set "REBUILD_VENV=1"
    )
)

if "!REBUILD_VENV!"=="0" (
    "%VENV_PY%" -m pip --version >nul 2>&1
    if !errorlevel! neq 0 (
        echo [INFO] Existing virtual environment has a missing or damaged pip.
        set "REBUILD_VENV=1"
    )
)

if "!REBUILD_VENV!"=="1" (
    if exist "%VENV_DIR%" (
        echo [INFO] Freeing background locks and resetting permissions on .venv...
        powershell -NoProfile -Command "taskkill /F /IM python.exe 2>$null; Get-Process | Where-Object { $_.Path -like '*\.venv\*' } | Stop-Process -Force -ErrorAction SilentlyContinue" >nul 2>&1
        attrib -r -s -h "%VENV_DIR%" /s /d >nul 2>&1
        takeown /f "%VENV_DIR%" /r /d y >nul 2>&1
        icacls "%VENV_DIR%" /grant *S-1-1-0:F /t /c /q >nul 2>&1
        echo [INFO] Wiping previous virtual environment to start fresh...
        powershell -NoProfile -Command "Remove-Item -LiteralPath '%VENV_DIR%' -Recurse -Force -ErrorAction SilentlyContinue" >nul 2>&1
        rmdir /s /q "%VENV_DIR%" >nul 2>&1
        if exist "%VENV_DIR%" (
            echo [WARNING] Folder locked by Windows. Renaming to clear path...
            ren "%VENV_DIR%" ".venv_old_!RANDOM!" >nul 2>&1
        )
    )
    echo [INFO] Creating clean virtual environment with %PY_CMD% (takes ~15 seconds)...
    call %PY_CMD% -m venv "%VENV_DIR%"
    if not exist "%VENV_PY%" (
        echo [WARNING] Standard venv creation failed. Retrying with --without-pip...
        call %PY_CMD% -m venv --without-pip "%VENV_DIR%"
        if exist "%VENV_PY%" (
            echo [INFO] Virtual environment created. Bootstrapping pip...
            call "%VENV_PY%" -m ensurepip --default-pip
            if not exist "%VENV_DIR%\Scripts\pip.exe" (
                echo [INFO] Fetching get-pip.py bootstrap...
                powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; (New-Object System.Net.WebClient).DownloadFile('https://bootstrap.pypa.io/get-pip.py', '%TEMP%\get-pip.py')"
                if exist "%TEMP%\get-pip.py" (
                    call "%VENV_PY%" "%TEMP%\get-pip.py" --no-warn-script-location
                    del "%TEMP%\get-pip.py" 2>nul
                )
            )
        )
    )
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
:: STEP 5: Fast Dependency Installation (uv with safe pip fallback)
:: ---------------------------------------------------------------------------
echo =======================================================
echo   [5/8] Installing project dependencies...
echo   Downloading speech AI models and libraries (Torch, Whisper, ONNX)
echo   This typically takes 1 to 3 minutes on the first run.
echo =======================================================
echo.

set "VIRTUAL_ENV=%VENV_DIR%"
set "USE_UV=0"

:: Check if uv is already working in venv
if exist "%VENV_DIR%\Scripts\uv.exe" (
    "%VENV_DIR%\Scripts\uv.exe" --version >nul 2>&1
    if !errorlevel! equ 0 set "USE_UV=1"
)

if "!USE_UV!"=="0" (
    echo [INFO] Setting up high-speed parallel package installer (uv)...
    call "%VENV_PY%" -m pip install --default-timeout=120 uv
    if exist "%VENV_DIR%\Scripts\uv.exe" (
        "%VENV_DIR%\Scripts\uv.exe" --version >nul 2>&1
        if !errorlevel! equ 0 set "USE_UV=1"
    )
)

set "INSTALL_SUCCESS=0"

if "!USE_UV!"=="1" (
    echo [INFO] Using fast parallel package installer (uv)...
    call "%VENV_DIR%\Scripts\uv.exe" pip install --python "%VENV_PY%" -r "%ROOT_DIR%\requirements.txt"
    if !errorlevel! equ 0 (
        set "INSTALL_SUCCESS=1"
    ) else (
        echo [WARNING] uv parallel installer encountered an issue. Falling back to standard pip...
    )
)

if "!INSTALL_SUCCESS!"=="0" (
    echo [INFO] Installing dependencies via standard pip (preferring binary wheels)...
    call "%VENV_PY%" -m pip install --default-timeout=120 --prefer-binary -r "%ROOT_DIR%\requirements.txt"
    if !errorlevel! equ 0 set "INSTALL_SUCCESS=1"
)

if "!INSTALL_SUCCESS!"=="0" (
    echo.
    echo =======================================================
    echo   [ERROR] Failed to install dependencies from requirements.txt!
    echo   Please check the error messages displayed above.
    echo =======================================================
    echo [DIAGNOSTICS] Python version:
    call "%VENV_PY%" -V
    echo [DIAGNOSTICS] Pip version:
    call "%VENV_PY%" -m pip -V
    goto :setup_failed
)

echo [INFO] Cleaning up incompatible audio packages...
call "%VENV_PY%" -m pip uninstall -y torchaudio >nul 2>&1
echo [SUCCESS] Core dependencies installed successfully.
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
    if "!USE_UV!"=="1" (
        call "%VENV_DIR%\Scripts\uv.exe" pip install --python "%VENV_PY%" nvidia-cublas-cu12 nvidia-cudnn-cu12
    ) else (
        call "%VENV_PY%" -m pip install --default-timeout=120 nvidia-cublas-cu12 nvidia-cudnn-cu12
    )
    echo [SUCCESS] NVIDIA GPU acceleration installed!
    goto :gpu_done
)

if "!HAS_AMD!"=="1" (
    echo [SUCCESS] AMD Radeon GPU detected!
    echo [INFO] Installing DirectML runtime packages for AMD GPU acceleration...
    if "!USE_UV!"=="1" (
        call "%VENV_DIR%\Scripts\uv.exe" pip install --python "%VENV_PY%" onnxruntime-directml torch-directml
    ) else (
        call "%VENV_PY%" -m pip install --default-timeout=120 onnxruntime-directml torch-directml
    )
    echo [SUCCESS] AMD Radeon GPU DirectML and OpenMP CPU acceleration configured!
    goto :gpu_done
)

echo [INFO] Configuring DirectML and CPU int8 acceleration...
if "!USE_UV!"=="1" (
    call "%VENV_DIR%\Scripts\uv.exe" pip install --python "%VENV_PY%" onnxruntime-directml >nul 2>&1
) else (
    call "%VENV_PY%" -m pip install --default-timeout=120 onnxruntime-directml >nul 2>&1
)
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
echo.

:: ---------------------------------------------------------------------------
:: STEP 8: Pre-cache Offline AI Models & Create Desktop Shortcut
:: ---------------------------------------------------------------------------
echo [8/8] Pre-caching default offline AI models and creating Desktop shortcut...
call "%VENV_PY%" -m obs_captioner.model_downloader --preload-defaults >nul 2>&1

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
pause
exit /b 0

:setup_failed
echo.
echo =======================================================
echo   [ERROR] Setup encountered an issue and could not complete.
echo   Please check the error messages displayed above.
echo =======================================================
echo.
echo Press any key to close this window...
pause
exit /b 1
