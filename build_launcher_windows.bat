@echo off
setlocal EnableDelayedExpansion

cd /d "%~dp0"
set "ROOT_DIR=%~dp0"
if "%ROOT_DIR:~-1%"=="\" set "ROOT_DIR=%ROOT_DIR:~0,-1%"
set "VENV_DIR=%ROOT_DIR%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "VENV_UV=%VENV_DIR%\Scripts\uv.exe"

echo =======================================================
echo   VoxStream Standalone Executable Builder (Windows)
echo =======================================================
echo.
echo Compiling VoxStream into a native standalone Windows application (VoxStream.exe)...
echo.

if not exist "%VENV_PY%" (
    echo [ERROR] Virtual environment not found. Please run setup_windows.bat first.
    pause
    exit /b 1
)

:: 1. Ensure PyInstaller is installed in virtual environment
call "%VENV_PY%" -c "import PyInstaller" >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Installing PyInstaller into virtual environment...
    if exist "%VENV_UV%" (
        call "%VENV_UV%" pip install pyinstaller
    ) else (
        call "%VENV_PY%" -m pip install pyinstaller
    )
)

:: 2. Compile standalone exe
echo.
echo [INFO] Running PyInstaller build...
call "%VENV_PY%" -m PyInstaller ^
    --noconsole ^
    --onefile ^
    --name "VoxStream" ^
    --icon "%ROOT_DIR%\obs_captioner\web\static\favicon.ico" ^
    --add-data "%ROOT_DIR%\obs_captioner\web\static;obs_captioner\web\static" ^
    --clean ^
    "%ROOT_DIR%\run_launcher.py"

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] PyInstaller build failed. Check the error output above.
    pause
    exit /b 1
)

:: 3. Copy executable to root for easy access
if exist "%ROOT_DIR%\dist\VoxStream.exe" (
    copy /y "%ROOT_DIR%\dist\VoxStream.exe" "%ROOT_DIR%\VoxStream.exe" >nul
    echo.
    echo =======================================================
    echo   [SUCCESS] VoxStream.exe built successfully!
    echo   Location: %ROOT_DIR%\VoxStream.exe
    echo.
    echo   To add to your Windows Taskbar:
    echo     1. Right-click 'VoxStream.exe' in this folder
    echo     2. Click 'Pin to taskbar'
    echo =======================================================
) else (
    echo [WARNING] VoxStream.exe was not found in dist folder.
)

echo.
pause
