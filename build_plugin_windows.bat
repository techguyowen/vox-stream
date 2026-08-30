@echo off
setlocal enabledelayedexpansion

echo =======================================================
echo   🔨 Building Native OBS Studio C++ Plugin
echo =======================================================
echo.

:: Check for CMake
cmake --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] CMake is not installed or not in PATH!
    echo Please install CMake from https://cmake.org/download/
    pause
    exit /b 1
)

:: Create build folder
if not exist "build" mkdir build
cd build

echo [1/3] Configuring CMake build...
cmake ../obs_native_plugin -DCMAKE_BUILD_TYPE=Release
if %errorlevel% neq 0 (
    echo [ERROR] CMake configuration failed. Ensure OBS Studio development SDK and Qt6 are installed.
    cd ..
    pause
    exit /b 1
)

echo [2/3] Compiling obs-live-captions.dll...
cmake --build . --config Release
if %errorlevel% neq 0 (
    echo [ERROR] Build failed.
    cd ..
    pause
    exit /b 1
)

echo [3/3] Locating compiled binary...
if exist "Release\obs-live-captions.dll" (
    echo [SUCCESS] Compiled plugin: build\Release\obs-live-captions.dll
    echo.
    echo To install manually:
    echo Copy 'build\Release\obs-live-captions.dll' to:
    echo 'C:\Program Files\obs-studio\obs-plugins\64bit\'
) else if exist "obs-live-captions.dll" (
    echo [SUCCESS] Compiled plugin: build\obs-live-captions.dll
)

cd ..
echo.
echo =======================================================
echo   Build Finished!
echo =======================================================
pause
