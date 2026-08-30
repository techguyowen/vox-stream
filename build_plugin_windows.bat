@echo off
setlocal enabledelayedexpansion

echo =======================================================
echo   🔨 Building VoxStream Native OBS C++ Plugin (.dll)
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

echo [1/3] Configuring CMake build using bundled OBS SDK headers...
cmake ../obs_native_plugin -DCMAKE_BUILD_TYPE=Release
if %errorlevel% neq 0 (
    echo [ERROR] CMake configuration failed.
    cd ..
    pause
    exit /b 1
)

echo.
echo [2/3] Compiling obs-live-captions.dll...
cmake --build . --config Release
if %errorlevel% neq 0 (
    echo [ERROR] Compilation failed.
    cd ..
    pause
    exit /b 1
)

echo.
echo [3/3] Locating compiled binary...
set "DLL_PATH="
if exist "Release\obs-live-captions.dll" (
    set "DLL_PATH=Release\obs-live-captions.dll"
) else if exist "obs-live-captions.dll" (
    set "DLL_PATH=obs-live-captions.dll"
)

if defined DLL_PATH (
    echo [SUCCESS] Compiled plugin successfully: build\!DLL_PATH!
    echo.
    set "OBS_DIR=C:\Program Files\obs-studio\obs-plugins\64bit"
    if exist "!OBS_DIR!" (
        echo Copying plugin to OBS plugins directory: !OBS_DIR!...
        copy /y "!DLL_PATH!" "!OBS_DIR!\obs-live-captions.dll" >nul 2>&1
        if %errorlevel% equ 0 (
            echo [SUCCESS] Installed to OBS Studio automatically!
        ) else (
            echo [NOTE] Could not copy automatically (requires Administrator permissions).
            echo Please manually copy 'build\!DLL_PATH!' to '!OBS_DIR!'.
        )
    ) else (
        echo [INFO] To install manually:
        echo Copy 'build\!DLL_PATH!' into 'C:\Program Files\obs-studio\obs-plugins\64bit\'.
    )
) else (
    echo [WARNING] Build completed but DLL could not be located automatically in build folder.
)

cd ..
echo.
echo =======================================================
echo   🎉 Native Plugin Build Finished!
echo =======================================================
pause
