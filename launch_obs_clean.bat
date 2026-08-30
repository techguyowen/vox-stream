@echo off
title OBS Clean Launcher (No Safe-Mode Prompts)
echo ========================================================
echo 🎬 OBS Studio Clean Launcher
echo ========================================================
echo.
echo Clearing OBS Sentinel and Safe Mode flags...

:: Remove the crash detection directories/files to prevent the Safe Mode prompt
rmdir /S /Q "%appdata%\obs-studio\.sentinel" 2>nul
del "%appdata%\obs-studio\safe_mode" /f /q 2>nul

echo Launching OBS Studio with shutdown checks disabled...
echo.

:: Launch OBS Studio
:: Assumes default 64-bit installation path
if exist "C:\Program Files\obs-studio\bin\64bit\obs64.exe" (
    start "" "C:\Program Files\obs-studio\bin\64bit\obs64.exe" --disable-shutdown-check
    echo OBS Studio started successfully.
) else (
    echo [ERROR] OBS Studio not found at C:\Program Files\obs-studio\bin\64bit\obs64.exe
    echo Please launch OBS manually or update this script with your custom installation path.
    pause
)

:: Wait 2 seconds before closing the terminal
ping 127.0.0.1 -n 3 > nul
exit
