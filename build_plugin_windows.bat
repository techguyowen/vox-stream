@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

echo =======================================================
echo   🔨 Building VoxStream Native OBS C++ Plugin (.dll)
echo =======================================================
echo.

:: 1. Check for CMake
cmake --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] CMake is not installed or not found in PATH!
    echo Please download and install CMake from: https://cmake.org/download/
    echo (Make sure to check "Add CMake to system PATH" during install)
    pause
    exit /b 1
)

:: 2. Clean previous failed build cache if any
if exist "build\CMakeCache.txt" (
    echo [INFO] Clearing previous build cache...
    del /f /q "build\CMakeCache.txt" 2>nul
)

:: 3. Configure CMake with 64-bit architecture
echo [1/3] Configuring 64-bit CMake build with bundled OBS SDK...
cmake -B build -S obs_native_plugin -A x64 -DCMAKE_BUILD_TYPE=Release

if %errorlevel% neq 0 (
    echo.
    echo =======================================================
    echo   ❌ CMake Configuration Error
    echo =======================================================
    echo If CMake says 'No CMAKE_CXX_COMPILER could be found':
    echo You need Visual Studio with C++ Desktop Development tools.
    echo.
    echo To install C++ tools automatically, run in PowerShell/CMD:
    echo   winget install Microsoft.VisualStudio.2022.BuildTools --override "--passive --add Microsoft.VisualStudio.Workload.VCTools"
    echo.
    echo 💡 REMINDER: You do NOT need to compile this C++ plugin!
    echo You can run the Python app right now by double-clicking 'run_captioner.bat'.
    echo In OBS, simply set your ASIO soundboard Audio Monitoring to 'Monitor and Output'.
    echo =======================================================
    pause
    exit /b 1
)

:: 4. Compile the DLL
echo.
echo [2/3] Compiling obs-live-captions.dll (64-bit Release)...
cmake --build build --config Release

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Compilation failed. Check the error log above.
    pause
    exit /b 1
)

:: 5. Locate and install DLL
echo.
echo [3/3] Locating compiled binary...
set "DLL_PATH="
if exist "build\Release\obs-live-captions.dll" (
    set "DLL_PATH=build\Release\obs-live-captions.dll"
) else if exist "build\obs-live-captions.dll" (
    set "DLL_PATH=build\obs-live-captions.dll"
)

if defined DLL_PATH (
    echo [SUCCESS] Compiled plugin: !DLL_PATH!
    echo.
    set "OBS_DIR=C:\Program Files\obs-studio\obs-plugins\64bit"
    if exist "!OBS_DIR!" (
        echo Installing plugin into OBS: !OBS_DIR!...
        copy /y "!DLL_PATH!" "!OBS_DIR!\obs-live-captions.dll" >nul 2>&1
        if %errorlevel% equ 0 (
            echo [SUCCESS] Installed to OBS Studio successfully!
        ) else (
            echo [NOTE] Administrator permissions required to copy to Program Files.
            echo Please copy '!DLL_PATH!' into '!OBS_DIR!'.
        )
    ) else (
        echo [INFO] Copy '!DLL_PATH!' into your OBS plugins 64bit folder.
    )
) else (
    echo [WARNING] Build completed but DLL file was not found in build directory.
)

echo.
echo =======================================================
echo   🎉 Native Plugin Build Finished!
echo =======================================================
pause
