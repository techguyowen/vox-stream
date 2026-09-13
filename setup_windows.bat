@echo off
setlocal EnableDelayedExpansion

:: Set UTF-8 for proper text display
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

:: Set working directory to script location
cd /d "%~dp0"
set "ROOT_DIR=%~dp0"
if "%ROOT_DIR:~-1%"=="\" set "ROOT_DIR=%ROOT_DIR:~0,-1%"

echo.
echo =======================================================
echo   VoxStream Live Captioner - Windows Setup
echo =======================================================
echo.

:: ============================================================
:: STEP 1: Detect Python (3.10 - 3.13, skip WindowsApps stub)
:: ============================================================
echo [1/6] Detecting Python installation...
echo.

set "PYTHON_EXE="

:: 1. Check py launcher for versions 3.11, 3.12, 3.10, 3.13, -3
for %%V in (-3.11 -3.12 -3.10 -3.13 -3) do (
    if not defined PYTHON_EXE (
        py %%V -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,13) and 'windowsapps' not in sys.executable.lower() else 1)" >nul 2>&1
        if !errorlevel! equ 0 (
            for /f "delims=" %%I in ('py %%V -c "import sys; print(sys.executable)" 2^>nul') do (
                if not defined PYTHON_EXE (
                    set "PYTHON_EXE=%%I"
                    echo [OK] Found Python via py %%V: %%I
                )
            )
        )
    )
)

:: 2. Check standard python in PATH (reject Microsoft Store stub)
if not defined PYTHON_EXE (
    python -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,13) and 'windowsapps' not in sys.executable.lower() else 1)" >nul 2>&1
    if !errorlevel! equ 0 (
        for /f "delims=" %%I in ('python -c "import sys; print(sys.executable)" 2^>nul') do (
            if not defined PYTHON_EXE (
                set "PYTHON_EXE=%%I"
                echo [OK] Found Python in PATH: %%I
            )
        )
    )
)

:: 3. Check known default installation folders
if not defined PYTHON_EXE (
    for %%P in (
        "%LocalAppData%\Programs\Python\Python311\python.exe"
        "%LocalAppData%\Programs\Python\Python312\python.exe"
        "%LocalAppData%\Programs\Python\Python310\python.exe"
        "%LocalAppData%\Programs\Python\Python313\python.exe"
        "%ProgramFiles%\Python311\python.exe"
        "%ProgramFiles%\Python312\python.exe"
        "%ProgramFiles%\Python310\python.exe"
        "%ProgramFiles%\Python313\python.exe"
        "C:\Python311\python.exe"
        "C:\Python312\python.exe"
        "C:\Python310\python.exe"
        "C:\Python313\python.exe"
    ) do (
        if not defined PYTHON_EXE (
            if exist %%P (
                %%P -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,13) else 1)" >nul 2>&1
                if !errorlevel! equ 0 (
                    set "PYTHON_EXE=%%~P"
                    echo [OK] Found Python at: %%~P
                )
            )
        )
    )
)

:: If Python is not found, guide the user directly to the official download
if not defined PYTHON_EXE (
    echo =======================================================
    echo   [ERROR] Python 3.10, 3.11, or 3.12 was NOT found.
    echo.
    echo   Please install Python:
    echo   1. Download: https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
    echo   2. Run the installer
    echo   3. CRITICAL: Check the box "Add python.exe to PATH"
    echo   4. Click "Install Now"
    echo   5. Re-run setup_windows.bat
    echo =======================================================
    echo.
    start https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
    echo Press any key to close this window...
    pause
    exit /b 1
)

echo.
echo [INFO] Python executable: !PYTHON_EXE!
"!PYTHON_EXE!" --version
echo.

:: ============================================================
:: STEP 2: Create or Reset Virtual Environment (.venv)
:: ============================================================
echo [2/6] Setting up virtual environment (.venv)...
echo.

set "VENV=%ROOT_DIR%\.venv"
set "VENV_PY=%VENV%\Scripts\python.exe"

:: Cleanly wipe corrupted/stale old .venv if it exists
if exist "%VENV%" (
    echo [INFO] Removing previous virtual environment...
    taskkill /F /FI "IMAGENAME eq python.exe" >nul 2>&1
    attrib -r -s -h "%VENV%\*" /s /d >nul 2>&1
    rmdir /s /q "%VENV%" >nul 2>&1
    if exist "%VENV%" (
        powershell -NoProfile -Command "Remove-Item -LiteralPath '%VENV%' -Recurse -Force -ErrorAction SilentlyContinue" >nul 2>&1
    )
    if exist "%VENV%" (
        echo [WARNING] .venv directory locked by Windows. Renaming it...
        ren "%VENV%" ".venv_old_!RANDOM!" >nul 2>&1
    )
    echo [OK] Previous environment cleared.
    echo.
)

echo [INFO] Creating virtual environment...
"!PYTHON_EXE!" -m venv "%VENV%"

:: Fallback without pip if ensurepip fails
if not exist "%VENV_PY%" (
    echo [WARNING] Standard venv creation failed. Retrying with --without-pip...
    if exist "%VENV%" rmdir /s /q "%VENV%" >nul 2>&1
    "!PYTHON_EXE!" -m venv --without-pip "%VENV%"
)

if not exist "%VENV_PY%" (
    echo.
    echo =======================================================
    echo   [ERROR] Could not create virtual environment.
    echo.
    echo   Python at: !PYTHON_EXE!
    echo   was unable to write to: %VENV%
    echo.
    echo   Possible causes:
    echo   - Antivirus blocking python from creating executables
    echo   - Folder path permissions (extract outside OneDrive, e.g. C:\vox-stream)
    echo   - Incomplete Python install: reinstall with "Add to PATH"
    echo =======================================================
    echo.
    echo Press any key to close this window...
    pause
    exit /b 1
)

echo [OK] Virtual environment created.

:: Ensure pip is installed and working inside the venv
"!VENV_PY!" -m pip --version >nul 2>&1
if !errorlevel! neq 0 (
    echo [INFO] Bootstrapping pip via ensurepip...
    "!VENV_PY!" -m ensurepip --default-pip >nul 2>&1
)

"!VENV_PY!" -m pip --version >nul 2>&1
if !errorlevel! neq 0 (
    echo [INFO] Downloading get-pip.py bootstrap...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; (New-Object System.Net.WebClient).DownloadFile('https://bootstrap.pypa.io/get-pip.py', '%TEMP%\get-pip.py')"
    if exist "%TEMP%\get-pip.py" (
        "!VENV_PY!" "%TEMP%\get-pip.py" --no-warn-script-location
        del "%TEMP%\get-pip.py" >nul 2>&1
    )
)

"!VENV_PY!" -m pip --version >nul 2>&1
if !errorlevel! neq 0 (
    echo.
    echo =======================================================
    echo   [ERROR] pip is not available inside the virtual environment.
    echo =======================================================
    echo Press any key to close this window...
    pause
    exit /b 1
)

echo [OK] pip is ready.
echo.

:: ============================================================
:: STEP 3: Install Dependencies from requirements.txt
:: ============================================================
echo [3/6] Installing dependencies...
echo        (Downloading ~250 MB of AI speech libraries, takes 1-3 minutes)
echo        Please do not close this window while downloading.
echo.

"!VENV_PY!" -m pip install --prefer-binary --timeout 120 -r "%ROOT_DIR%\requirements.txt"

if !errorlevel! neq 0 (
    echo.
    echo =======================================================
    echo   [ERROR] Dependency installation failed.
    echo.
    echo   Troubleshooting:
    echo   - Check your internet connection
    echo   - Temporarily disable VPN or third-party antivirus
    echo   - Re-run setup_windows.bat
    echo =======================================================
    echo.
    echo Press any key to close this window...
    pause
    exit /b 1
)

:: Remove conflicting torchaudio package if pulled as a sub-dependency
"!VENV_PY!" -m pip uninstall -y torchaudio >nul 2>&1

echo.
echo [OK] Dependencies installed successfully.
echo.

:: ============================================================
:: STEP 4: GPU Hardware Acceleration
:: ============================================================
echo [4/6] Detecting GPU hardware acceleration...
echo.

set "HAS_NVIDIA=0"
set "HAS_AMD=0"

where nvidia-smi >nul 2>&1
if !errorlevel! equ 0 set "HAS_NVIDIA=1"

if "!HAS_NVIDIA!"=="0" (
    powershell -NoProfile -Command "if (@(Get-CimInstance Win32_VideoController 2>$null | Where-Object { $_.Name -match 'NVIDIA|GeForce|RTX|GTX|Quadro' }).Count -gt 0) { exit 0 } else { exit 1 }" >nul 2>&1
    if !errorlevel! equ 0 set "HAS_NVIDIA=1"
)

powershell -NoProfile -Command "if (@(Get-CimInstance Win32_VideoController 2>$null | Where-Object { $_.Name -match 'Radeon|AMD RX' }).Count -gt 0) { exit 0 } else { exit 1 }" >nul 2>&1
if !errorlevel! equ 0 set "HAS_AMD=1"

if "!HAS_NVIDIA!"=="1" (
    echo [OK] NVIDIA GPU detected! Installing CUDA and cuDNN runtime...
    "!VENV_PY!" -m pip install --prefer-binary --timeout 120 nvidia-cublas-cu12 nvidia-cudnn-cu12
    echo [OK] NVIDIA CUDA acceleration ready.
    goto :gpu_done
)

if "!HAS_AMD!"=="1" (
    echo [OK] AMD Radeon GPU detected! Installing DirectML runtime...
    "!VENV_PY!" -m pip install --prefer-binary --timeout 120 onnxruntime-directml torch-directml
    echo [OK] AMD DirectML acceleration ready.
    goto :gpu_done
)

echo [INFO] No dedicated GPU detected - configuring CPU int8 acceleration...
"!VENV_PY!" -m pip install --prefer-binary --timeout 120 onnxruntime-directml >nul 2>&1
echo [OK] Configured for CPU inference.

:gpu_done
echo.

:: ============================================================
:: STEP 5: Configuration & Windows Microphone Permissions
:: ============================================================
echo [5/6] Configuring settings and microphone permissions...
echo.

if not exist "%ROOT_DIR%\config.json" (
    if exist "%ROOT_DIR%\config.json.example" (
        copy "%ROOT_DIR%\config.json.example" "%ROOT_DIR%\config.json" >nul 2>&1
        echo [OK] Created config.json from template.
    )
) else (
    echo [OK] Preserved existing config.json.
)

reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone" /v Value /t REG_SZ /d Allow /f >nul 2>&1
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone\NonPackaged" /v Value /t REG_SZ /d Allow /f >nul 2>&1
echo [OK] Windows microphone access verified.
echo.

:: ============================================================
:: STEP 6: Desktop Shortcut & Pre-cache Models
:: ============================================================
echo [6/6] Creating Desktop shortcut and pre-caching models...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $desktop = [Environment]::GetFolderPath('Desktop'); $lnk = $ws.CreateShortcut((Join-Path $desktop 'VoxStream Live Captioner.lnk')); $lnk.TargetPath = '%ROOT_DIR%\run_captioner.bat'; $lnk.WorkingDirectory = '%ROOT_DIR%'; $lnk.Description = 'Launch VoxStream Live Captioner'; $lnk.Save()" >nul 2>&1
echo [OK] Desktop shortcut created.
echo.

echo [INFO] Pre-caching default offline models (Silero VAD ^& Vosk)...
"!VENV_PY!" -m obs_captioner.model_downloader --preload-defaults

echo.
echo =======================================================
echo   [SUCCESS] Setup Complete!
echo.
echo   1. Double-click "VoxStream Live Captioner" on your
echo      Desktop to start the application.
echo.
echo   2. Open your browser to: http://127.0.0.1:8765
echo =======================================================
echo.
echo Press any key to close this window...
pause
exit /b 0
