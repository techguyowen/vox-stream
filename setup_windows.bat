@echo off
setlocal enabledelayedexpansion

echo =======================================================
echo   🎙️ VoxStream: OBS Live Captioner Suite - Windows Setup
echo =======================================================
echo.

:: 1. Check for existing Python installation
set "PY_CMD="
python --version >nul 2>&1
if %errorlevel% equ 0 (
    set "PY_CMD=python"
    goto :python_found
)

py -3 --version >nul 2>&1
if %errorlevel% equ 0 (
    set "PY_CMD=py -3"
    goto :python_found
)

:: Check default install directories
if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
    set "PY_CMD=%LocalAppData%\Programs\Python\Python311\python.exe"
    set "PATH=%LocalAppData%\Programs\Python\Python311;%LocalAppData%\Programs\Python\Python311\Scripts;%PATH%"
    goto :python_found
)
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
    set "PY_CMD=%LocalAppData%\Programs\Python\Python312\python.exe"
    set "PATH=%LocalAppData%\Programs\Python\Python312;%LocalAppData%\Programs\Python\Python312\Scripts;%PATH%"
    goto :python_found
)

:: 2. Python missing -> Attempt automatic installation
echo [INFO] Python was not detected on your system.
echo [INFO] Attempting automatic installation of Python 3.11...
echo.

:: Try winget first if available
winget --version >nul 2>&1
if %errorlevel% equ 0 (
    echo [INFO] Found winget. Attempting installation via Windows Package Manager...
    winget install --id Python.Python.3.11 -e --silent --accept-package-agreements --accept-source-agreements
)

:: Check if winget succeeded
if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
    set "PY_CMD=%LocalAppData%\Programs\Python\Python311\python.exe"
    set "PATH=%LocalAppData%\Programs\Python\Python311;%LocalAppData%\Programs\Python\Python311\Scripts;%PATH%"
    goto :python_found
)

:: If winget is missing or failed -> Fallback to direct PowerShell download from python.org
echo [INFO] winget was unavailable or failed. Downloading official Python 3.11 from python.org...
set "INSTALLER_PATH=%TEMP%\python-3.11.9-amd64.exe"

powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; (New-Object Net.WebClient).DownloadFile('https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe', '%INSTALLER_PATH%')"

if exist "%INSTALLER_PATH%" (
    echo [INFO] Installing Python 3.11.9 (please wait 30-60 seconds)...
    start /wait "" "%INSTALLER_PATH%" /passive InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_test=0 Shortcuts=0 TargetDir="%LocalAppData%\Programs\Python\Python311"
    del "%INSTALLER_PATH%" 2>nul
) else (
    echo [ERROR] Failed to download Python installer automatically.
    echo Please manually download and install Python 3.11 from: https://www.python.org/downloads/
    echo (Make sure to check the box "Add Python to PATH")
    start https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Refresh and check standard installation directories
if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
    set "PY_CMD=%LocalAppData%\Programs\Python\Python311\python.exe"
    set "PATH=%LocalAppData%\Programs\Python\Python311;%LocalAppData%\Programs\Python\Python311\Scripts;%PATH%"
) else if exist "%ProgramFiles%\Python311\python.exe" (
    set "PY_CMD=%ProgramFiles%\Python311\python.exe"
    set "PATH=%ProgramFiles%\Python311;%ProgramFiles%\Python311\Scripts;%PATH%"
) else (
    set "PY_CMD=python"
)

%PY_CMD% --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [NOTE] Python was installed, but Windows needs to refresh its path.
    echo Please close this window and double-click setup_windows.bat again.
    pause
    exit /b 0
)

:python_found
echo [SUCCESS] Using Python:
%PY_CMD% --version
echo.

:: 3. Create virtual environment
echo [1/4] Setting up virtual environment (.venv)...
if not exist ".venv" (
    %PY_CMD% -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

:: 4. Activate virtual environment
echo [2/4] Activating virtual environment...
call .venv\Scripts\activate.bat

:: 5. Install dependencies
echo [3/4] Upgrading pip and installing required packages...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

:: 6. Setup Configuration
echo [4/4] Verifying configuration file...
if not exist "config.json" (
    copy config.json.example config.json >nul
    echo Created fresh config.json from template.
) else (
    echo Existing config.json preserved.
)

echo.
echo =======================================================
echo   Available Audio Input Devices on your PC:
echo =======================================================
python -m obs_captioner.main --list-devices
echo.
echo =======================================================
echo   🎉 Setup Complete!
echo   1. Double-click 'run_captioner.bat' to start.
echo   2. Open In-OBS Dock at: http://127.0.0.1:8765/dashboard
echo =======================================================
pause
