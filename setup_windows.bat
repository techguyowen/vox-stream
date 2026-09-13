@echo off
setlocal EnableDelayedExpansion

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

cd /d "%~dp0"
set "ROOT_DIR=%~dp0"
if "%ROOT_DIR:~-1%"=="\" set "ROOT_DIR=%ROOT_DIR:~0,-1%"

echo.
echo =======================================================
echo   VoxStream Live Captioner - Windows Setup
echo =======================================================
echo.

:: ============================================================
:: STEP 1: Find Python 3.10 / 3.11 / 3.12 (real install only)
:: ============================================================
echo [1/6] Detecting Python...
echo.

set "PYTHON_EXE="

:: Resolve py launcher to an actual path for each version
:: This avoids the "space in variable" venv bug AND catches
:: Windows Store stubs that the py launcher might point to.

for %%V in (3.11 3.12 3.10) do (
    if not defined PYTHON_EXE (
        for /f "usebackq tokens=*" %%P in (`py -%%V -c "import sys; print(sys.executable)" 2^>nul`) do (
            set "_CANDIDATE=%%P"
            echo [DEBUG] py -%%V resolves to: %%P
            :: Reject Windows Store sandbox paths
            echo %%P | findstr /i "windowsapps" >nul 2>&1
            if !errorlevel! neq 0 (
                if exist "%%P" (
                    set "PYTHON_EXE=%%P"
                    echo [OK] Found Python %%V at: %%P
                )
            ) else (
                echo [SKIP] %%P is a Windows Store stub - ignoring.
            )
        )
    )
)

:: Also check hard-coded install paths
if not defined PYTHON_EXE (
    for %%P in (
        "%LocalAppData%\Programs\Python\Python311\python.exe"
        "%LocalAppData%\Programs\Python\Python312\python.exe"
        "%LocalAppData%\Programs\Python\Python310\python.exe"
        "%ProgramFiles%\Python311\python.exe"
        "%ProgramFiles%\Python312\python.exe"
        "C:\Python311\python.exe"
        "C:\Python312\python.exe"
        "C:\Python310\python.exe"
    ) do (
        if not defined PYTHON_EXE (
            if exist %%P (
                echo [DEBUG] Checking path: %%P
                %%P -c "import sys; exit(0)" >nul 2>&1
                if !errorlevel! equ 0 (
                    set "PYTHON_EXE=%%~P"
                    echo [OK] Found Python at: %%~P
                )
            )
        )
    )
)

if not defined PYTHON_EXE (
    echo.
    echo =======================================================
    echo   Python 3.10, 3.11, or 3.12 was NOT found.
    echo.
    echo   Please do this BEFORE re-running setup:
    echo.
    echo   1. Go to: https://www.python.org/downloads/
    echo   2. Download Python 3.11 for Windows (64-bit)
    echo   3. Run the installer
    echo   4. IMPORTANT: Check "Add python.exe to PATH"
    echo   5. Click Install Now
    echo   6. Close this window, then re-run setup_windows.bat
    echo =======================================================
    echo.
    start https://www.python.org/downloads/release/python-3119/
    echo Press any key to close...
    pause
    exit /b 1
)

echo.
echo [INFO] Python selected:
"%PYTHON_EXE%" --version
echo [INFO] Path: %PYTHON_EXE%
echo.

:: ============================================================
:: STEP 2: Create Virtual Environment (.venv)
:: ============================================================
echo [2/6] Setting up virtual environment (.venv)...
echo.

set "VENV=%ROOT_DIR%\.venv"
set "VENV_PY=%VENV%\Scripts\python.exe"
set "VENV_PIP=%VENV%\Scripts\pip.exe"

:: Always wipe old .venv to avoid stale/locked state
if exist "%VENV%" (
    echo [INFO] Removing old virtual environment...
    taskkill /F /FI "IMAGENAME eq python.exe" >nul 2>&1
    attrib -r -s -h "%VENV%" /s /d >nul 2>&1
    powershell -NoProfile -Command "Remove-Item -LiteralPath '%VENV%' -Recurse -Force -ErrorAction SilentlyContinue" >nul 2>&1
    if exist "%VENV%" rmdir /s /q "%VENV%" >nul 2>&1
    if exist "%VENV%" (
        echo [INFO] Folder locked - renaming it out of the way...
        ren "%VENV%" ".venv_old_%RANDOM%" >nul 2>&1
    )
    echo [OK] Old environment cleared.
    echo.
)

echo [INFO] Creating virtual environment with: %PYTHON_EXE%
"%PYTHON_EXE%" -m venv "%VENV%"
echo [INFO] venv command finished (exit code: !errorlevel!)

if not exist "%VENV_PY%" (
    echo.
    echo [WARNING] venv failed. Retrying with --without-pip...
    "%PYTHON_EXE%" -m venv --without-pip "%VENV%"
    echo [INFO] Retry finished (exit code: !errorlevel!)
)

if not exist "%VENV_PY%" (
    echo.
    echo =======================================================
    echo   [ERROR] Could not create a virtual environment.
    echo.
    echo   Most likely fix: Uninstall Python, then reinstall it
    echo   from python.org making sure to check:
    echo   "Add python.exe to PATH"
    echo.
    echo   Download: https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
    echo =======================================================
    echo.
    echo Press any key to close...
    pause
    exit /b 1
)

echo [OK] Virtual environment created.

:: Bootstrap pip if venv was created without it
if not exist "%VENV_PIP%" (
    echo [INFO] Bootstrapping pip...
    "%VENV_PY%" -m ensurepip --default-pip
)
if not exist "%VENV_PIP%" (
    echo [INFO] Downloading get-pip.py...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; (New-Object System.Net.WebClient).DownloadFile('https://bootstrap.pypa.io/get-pip.py','%TEMP%\get-pip.py')"
    if exist "%TEMP%\get-pip.py" (
        "%VENV_PY%" "%TEMP%\get-pip.py"
        del "%TEMP%\get-pip.py" 2>nul
    )
)

echo.

:: ============================================================
:: STEP 3: Install Dependencies
:: ============================================================
echo [3/6] Installing project dependencies...
echo        (Downloads ~250 MB of AI libraries - takes 1 to 3 minutes)
echo        You will see a progress bar below. Do NOT close this window.
echo.

"%VENV_PIP%" install --prefer-binary --timeout 120 -r "%ROOT_DIR%\requirements.txt"

if !errorlevel! neq 0 (
    echo.
    echo =======================================================
    echo   [ERROR] Dependency installation failed.
    echo   Check the error messages printed above.
    echo   Common fixes:
    echo     - Disable VPN or antivirus temporarily
    echo     - Re-run setup_windows.bat
    echo =======================================================
    echo.
    echo Press any key to close...
    pause
    exit /b 1
)

:: Remove torchaudio if present (causes audio conflicts)
"%VENV_PIP%" uninstall -y torchaudio >nul 2>&1

echo.
echo [OK] Dependencies installed successfully.
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
    powershell -NoProfile -Command "if((Get-CimInstance Win32_VideoController | Where-Object{$_.Name -match 'NVIDIA|GeForce|RTX|GTX'}).Count -gt 0){exit 0}else{exit 1}" >nul 2>&1
    if !errorlevel! equ 0 set "HAS_NVIDIA=1"
)

powershell -NoProfile -Command "if((Get-CimInstance Win32_VideoController | Where-Object{$_.Name -match 'Radeon|AMD RX'}).Count -gt 0){exit 0}else{exit 1}" >nul 2>&1
if !errorlevel! equ 0 set "HAS_AMD=1"

if "!HAS_NVIDIA!"=="1" (
    echo [OK] NVIDIA GPU detected - installing CUDA libraries...
    "%VENV_PIP%" install --prefer-binary --timeout 120 nvidia-cublas-cu12 nvidia-cudnn-cu12
    echo [OK] NVIDIA CUDA acceleration ready.
    goto :gpu_done
)

if "!HAS_AMD!"=="1" (
    echo [OK] AMD Radeon GPU detected - installing DirectML libraries...
    "%VENV_PIP%" install --prefer-binary --timeout 120 onnxruntime-directml torch-directml
    echo [OK] AMD DirectML acceleration ready.
    goto :gpu_done
)

echo [INFO] CPU mode - configuring DirectML for best CPU performance...
"%VENV_PIP%" install --prefer-binary --timeout 120 onnxruntime-directml >nul 2>&1
echo [OK] Configured for fast CPU inference.

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

reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone" /v Value /t REG_SZ /d Allow /f >nul 2>&1
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone\NonPackaged" /v Value /t REG_SZ /d Allow /f >nul 2>&1
echo [OK] Microphone access enabled.
echo.

:: ============================================================
:: STEP 6: Desktop Shortcut
:: ============================================================
echo [6/6] Creating Desktop shortcut...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws=New-Object -ComObject WScript.Shell; $lnk=$ws.CreateShortcut([IO.Path]::Combine([Environment]::GetFolderPath('Desktop'),'VoxStream Live Captioner.lnk')); $lnk.TargetPath='%ROOT_DIR%\run_captioner.bat'; $lnk.WorkingDirectory='%ROOT_DIR%'; $lnk.Description='Launch VoxStream'; $lnk.Save()" >nul 2>&1
echo [OK] Desktop shortcut created.
echo.

:: Pre-cache default models quietly
"%VENV_PY%" -m obs_captioner.model_downloader --preload-defaults >nul 2>&1

echo.
echo =======================================================
echo   [SUCCESS] Setup Complete!
echo.
echo   Double-click "VoxStream Live Captioner" on your
echo   Desktop to launch the app.
echo.
echo   Dashboard: http://127.0.0.1:8765
echo =======================================================
echo.
echo Press any key to close this window...
pause
exit /b 0
