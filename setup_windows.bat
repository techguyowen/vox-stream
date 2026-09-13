@echo off
setlocal EnableDelayedExpansion

:: Set UTF-8 for proper text display
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

:: Move to the folder containing this script
cd /d "%~dp0"
set "ROOT_DIR=%~dp0"
if "%ROOT_DIR:~-1%"=="\" set "ROOT_DIR=%ROOT_DIR:~0,-1%"

echo.
echo =======================================================
echo   VoxStream Live Captioner - Windows Setup
echo =======================================================
echo.

:: ============================================================
:: STEP 1: Python - Detect or Guide Install
:: ============================================================
echo [1/6] Detecting Python...
echo.

set "PYTHON_EXE="

:: Check explicit versioned py launcher entries first (most reliable)
for %%V in (3.12 3.11 3.10) do (
    if not defined PYTHON_EXE (
        py -%%V -c "import sys" >nul 2>&1
        if !errorlevel! equ 0 (
            set "PYTHON_EXE=py -%%V"
            echo [OK] Found Python %%V via py launcher.
        )
    )
)

:: Check common install paths
if not defined PYTHON_EXE (
    for %%P in (
        "%LocalAppData%\Programs\Python\Python312\python.exe"
        "%LocalAppData%\Programs\Python\Python311\python.exe"
        "%LocalAppData%\Programs\Python\Python310\python.exe"
        "%ProgramFiles%\Python312\python.exe"
        "%ProgramFiles%\Python311\python.exe"
        "%ProgramFiles%\Python310\python.exe"
        "C:\Python312\python.exe"
        "C:\Python311\python.exe"
        "C:\Python310\python.exe"
    ) do (
        if not defined PYTHON_EXE (
            if exist %%P (
                %%P -c "import sys" >nul 2>&1
                if !errorlevel! equ 0 (
                    set "PYTHON_EXE=%%P"
                    echo [OK] Found Python at %%P
                )
            )
        )
    )
)

:: Check python in PATH but block Windows Store dummy
if not defined PYTHON_EXE (
    python -c "import sys; exit(0 if (3,10)<=sys.version_info[:2]<=(3,13) and 'windowsapps' not in sys.executable.lower() else 1)" >nul 2>&1
    if !errorlevel! equ 0 (
        set "PYTHON_EXE=python"
        echo [OK] Found Python in PATH.
    )
)

if not defined PYTHON_EXE (
    echo.
    echo =======================================================
    echo   Python 3.10, 3.11, or 3.12 was not found.
    echo.
    echo   Please install it:
    echo   1. Download: https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
    echo   2. Run the installer
    echo   3. CHECK THE BOX: "Add python.exe to PATH"
    echo   4. Re-run this setup script
    echo =======================================================
    echo.
    start https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
    echo Press any key to close this window...
    pause
    exit /b 1
)

echo.
echo [INFO] Python version in use:
%PYTHON_EXE% --version
echo.

:: ============================================================
:: STEP 2: Create Virtual Environment
:: ============================================================
echo [2/6] Setting up virtual environment (.venv)...
echo.

set "VENV=%ROOT_DIR%\.venv"
set "VENV_PY=%VENV%\Scripts\python.exe"
set "VENV_PIP=%VENV%\Scripts\pip.exe"

:: Wipe the old venv if it exists (avoids all stale/locked state issues)
if exist "%VENV%" (
    echo [INFO] Removing old virtual environment...
    :: Kill any python processes in the venv first
    taskkill /F /FI "IMAGENAME eq python.exe" >nul 2>&1
    :: Remove read-only/system/hidden attributes
    attrib -r -s -h "%VENV%" /s /d >nul 2>&1
    :: Try PowerShell Remove-Item first (handles deep paths better)
    powershell -NoProfile -Command "Remove-Item -LiteralPath '%VENV%' -Recurse -Force -ErrorAction SilentlyContinue" >nul 2>&1
    :: Then rmdir as backup
    if exist "%VENV%" rmdir /s /q "%VENV%" >nul 2>&1
    :: If still stuck, rename it out of the way
    if exist "%VENV%" (
        echo [INFO] Folder still locked - renaming it to free up the path...
        ren "%VENV%" ".venv_old_%RANDOM%" >nul 2>&1
    )
    echo [OK] Old environment cleared.
)

echo [INFO] Creating fresh virtual environment...
%PYTHON_EXE% -m venv "%VENV%"

if not exist "%VENV_PY%" (
    echo.
    echo [WARNING] Standard venv failed - trying without bundled pip...
    %PYTHON_EXE% -m venv --without-pip "%VENV%"
)

if not exist "%VENV_PY%" (
    echo.
    echo =======================================================
    echo   [ERROR] Could not create the virtual environment.
    echo.
    echo   This usually means your Python install is incomplete.
    echo   Try uninstalling Python completely, then re-installing
    echo   from: https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
    echo   (Make sure to check "Add python.exe to PATH")
    echo =======================================================
    echo Press any key to close this window...
    pause
    exit /b 1
)

:: Bootstrap pip if it's missing
if not exist "%VENV_PIP%" (
    echo [INFO] Bootstrapping pip into the virtual environment...
    "%VENV_PY%" -m ensurepip --default-pip
)
if not exist "%VENV_PIP%" (
    echo [INFO] Downloading get-pip.py to install pip...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; (New-Object System.Net.WebClient).DownloadFile('https://bootstrap.pypa.io/get-pip.py', '%TEMP%\get-pip.py')"
    if exist "%TEMP%\get-pip.py" (
        "%VENV_PY%" "%TEMP%\get-pip.py"
        del "%TEMP%\get-pip.py" 2>nul
    )
)

echo [OK] Virtual environment ready.
echo.

:: ============================================================
:: STEP 3: Install Dependencies
:: ============================================================
echo [3/6] Installing project dependencies...
echo        (This downloads ~250 MB of AI libraries - takes 1 to 3 minutes)
echo.

call "%VENV_PIP%" install --prefer-binary --timeout 120 -r "%ROOT_DIR%\requirements.txt"

if !errorlevel! neq 0 (
    echo.
    echo =======================================================
    echo   [ERROR] Dependency installation failed.
    echo   Check the error messages above.
    echo   Common fixes:
    echo     - Check your internet connection
    echo     - Disable VPN or antivirus temporarily
    echo     - Re-run setup_windows.bat
    echo =======================================================
    echo Press any key to close this window...
    pause
    exit /b 1
)

:: Remove torchaudio if it crept in (causes audio conflicts)
call "%VENV_PIP%" uninstall -y torchaudio >nul 2>&1

echo.
echo [OK] Dependencies installed.
echo.

:: ============================================================
:: STEP 4: GPU Acceleration (Optional)
:: ============================================================
echo [4/6] Detecting GPU for hardware acceleration...
echo.

set "HAS_NVIDIA=0"
set "HAS_AMD=0"

where nvidia-smi >nul 2>&1
if !errorlevel! equ 0 set "HAS_NVIDIA=1"

if "!HAS_NVIDIA!"=="0" (
    powershell -NoProfile -Command "Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match 'NVIDIA|GeForce|RTX|GTX' } | Measure-Object | Select-Object -ExpandProperty Count" 2>nul | findstr /r "[1-9]" >nul 2>&1
    if !errorlevel! equ 0 set "HAS_NVIDIA=1"
)

powershell -NoProfile -Command "Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match 'Radeon|AMD RX' } | Measure-Object | Select-Object -ExpandProperty Count" 2>nul | findstr /r "[1-9]" >nul 2>&1
if !errorlevel! equ 0 set "HAS_AMD=1"

if "!HAS_NVIDIA!"=="1" (
    echo [OK] NVIDIA GPU detected - installing CUDA libraries...
    call "%VENV_PIP%" install --prefer-binary --timeout 120 nvidia-cublas-cu12 nvidia-cudnn-cu12
    echo [OK] NVIDIA CUDA acceleration ready.
    goto :gpu_done
)

if "!HAS_AMD!"=="1" (
    echo [OK] AMD Radeon GPU detected - installing DirectML libraries...
    call "%VENV_PIP%" install --prefer-binary --timeout 120 onnxruntime-directml torch-directml
    echo [OK] AMD DirectML acceleration ready.
    goto :gpu_done
)

echo [INFO] No dedicated GPU detected - using CPU mode (works great for most setups).
call "%VENV_PIP%" install --prefer-binary --timeout 120 onnxruntime-directml >nul 2>&1

:gpu_done
echo.

:: ============================================================
:: STEP 5: Settings and Microphone
:: ============================================================
echo [5/6] Configuring settings and microphone access...
echo.

if not exist "%ROOT_DIR%\config.json" (
    if exist "%ROOT_DIR%\config.json.example" (
        copy "%ROOT_DIR%\config.json.example" "%ROOT_DIR%\config.json" >nul 2>&1
        echo [OK] Created config.json from template.
    )
) else (
    echo [OK] Existing config.json preserved.
)

:: Allow microphone for desktop apps via registry
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone" /v Value /t REG_SZ /d Allow /f >nul 2>&1
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone\NonPackaged" /v Value /t REG_SZ /d Allow /f >nul 2>&1
echo [OK] Microphone access enabled.
echo.

:: ============================================================
:: STEP 6: Desktop Shortcut
:: ============================================================
echo [6/6] Creating Desktop shortcut...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $d = [Environment]::GetFolderPath('Desktop'); $lnk = Join-Path $d 'VoxStream Live Captioner.lnk'; $s = $ws.CreateShortcut($lnk); $s.TargetPath = '%ROOT_DIR%\run_captioner.bat'; $s.WorkingDirectory = '%ROOT_DIR%'; $s.Description = 'Launch VoxStream Live Captioner'; $s.Save()" >nul 2>&1
echo [OK] Desktop shortcut created.
echo.

:: Pre-cache models quietly in background
call "%VENV_PY%" -m obs_captioner.model_downloader --preload-defaults >nul 2>&1

echo.
echo =======================================================
echo   Setup Complete!
echo.
echo   Double-click "VoxStream Live Captioner" on your
echo   Desktop to start, or run:  run_captioner.bat
echo.
echo   Dashboard: http://127.0.0.1:8765
echo =======================================================
echo.
echo Press any key to close this window...
pause
exit /b 0
