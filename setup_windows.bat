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

:: 2. Python missing -> Attempt automatic winget installation
echo [INFO] Python was not detected on your system.
echo [INFO] Checking for Windows Package Manager (winget)...

winget --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] winget is not available on this Windows version.
    echo Please manually download and install Python 3.11 from:
    echo https://www.python.org/downloads/
    echo (Make sure to check "Add Python to PATH" during installation)
    pause
    exit /b 1
)

echo [INFO] Installing Python 3.11 automatically via winget (please wait)...
winget install --id Python.Python.3.11 -e --silent --accept-package-agreements --accept-source-agreements

if %errorlevel% neq 0 (
    echo [WARNING] winget returned an exit code: %errorlevel%. Checking if Python was installed...
)

:: Refresh PATH for current session
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
    echo [ERROR] Python installation completed, but a shell restart is required.
    echo Please close this window and double-click setup_windows.bat again.
    pause
    exit /b 1
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
echo   2. Open In-OBS Dock at: http://127.0.0.1:8080/dashboard
echo =======================================================
pause
