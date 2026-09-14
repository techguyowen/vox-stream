@echo off
setlocal EnableDelayedExpansion

cd /d "%~dp0"
set "RULE_NAME=VoxStream Overlay TCP 8765"
set "PORT=8765"

echo =======================================================
echo   VoxStream: Windows Defender Firewall Configuration
echo =======================================================
echo.

:: Check for direct CLI arguments: /add, /remove, /check
if /i "%~1"=="/add" goto :do_add
if /i "%~1"=="/remove" goto :do_remove
if /i "%~1"=="/check" goto :do_check

:: ---------------------------------------------------------------------------
:: STEP 1: Verify Administrator Privileges
:: ---------------------------------------------------------------------------
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Administrator permissions required to configure Windows Defender Firewall.
    echo [INFO] Requesting elevation via User Account Control (UAC)...
    echo.
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process cmd.exe -ArgumentList '/c \"\"%~f0\" /elevated' -Verb RunAs"
    exit /b 0
)

:interactive_menu
echo Select an option:
echo.
echo   [1] Open Port 8765 in Windows Firewall (Recommended)
echo       Allows stage iPads, confidence monitors, and phones on Wi-Fi to connect.
echo.
echo   [2] Check Current Firewall Status
echo.
echo   [3] Remove VoxStream Firewall Rule
echo.
echo   [4] Exit
echo.
set /p "CHOICE=Enter choice [1-4]: "

if "%CHOICE%"=="1" goto :do_add
if "%CHOICE%"=="2" goto :do_check
if "%CHOICE%"=="3" goto :do_remove
if "%CHOICE%"=="4" exit /b 0
echo.
echo Invalid choice. Please select 1, 2, 3, or 4.
echo.
goto :interactive_menu

:: ---------------------------------------------------------------------------
:: ACTION: Add Rule
:: ---------------------------------------------------------------------------
:do_add
echo.
echo [INFO] Configuring Windows Defender Firewall for TCP port %PORT%...
netsh advfirewall firewall delete rule name="%RULE_NAME%" >nul 2>&1
netsh advfirewall firewall add rule name="%RULE_NAME%" dir=in action=allow protocol=TCP localport=%PORT% profile=any description="Allows inbound connections to VoxStream overlay, stage display, and control dashboard" >nul 2>&1

if %errorlevel% equ 0 (
    echo [SUCCESS] Port %PORT% successfully opened in Windows Defender Firewall!
    echo [SUCCESS] Local devices on your Wi-Fi network can now view stage displays and overlays.
) else (
    echo [ERROR] Failed to add firewall rule. Please ensure you have Administrator rights.
)
goto :finish

:: ---------------------------------------------------------------------------
:: ACTION: Check Rule
:: ---------------------------------------------------------------------------
:do_check
echo.
echo [INFO] Checking firewall rules for "%RULE_NAME%"...
netsh advfirewall firewall show rule name="%RULE_NAME%" >nul 2>&1
if %errorlevel% equ 0 (
    echo.
    echo =======================================================
    echo   STATUS: [OPEN] Firewall rule is ACTIVE.
    echo   Inbound traffic on TCP port %PORT% is allowed.
    echo =======================================================
) else (
    echo.
    echo =======================================================
    echo   STATUS: [BLOCKED/NOT CONFIGURED]
    echo   No firewall rule found. External devices on Wi-Fi
    echo   may be blocked from connecting to port %PORT%.
    echo =======================================================
)
goto :finish

:: ---------------------------------------------------------------------------
:: ACTION: Remove Rule
:: ---------------------------------------------------------------------------
:do_remove
echo.
echo [INFO] Removing firewall rule "%RULE_NAME%"...
netsh advfirewall firewall delete rule name="%RULE_NAME%" >nul 2>&1
if %errorlevel% equ 0 (
    echo [SUCCESS] Firewall rule removed.
) else (
    echo [INFO] No matching firewall rule was found to remove.
)
goto :finish

:finish
echo.
if "%~1"=="" (
    echo Press any key to exit...
    pause >nul
)
exit /b 0
