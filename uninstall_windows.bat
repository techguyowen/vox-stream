@echo off
setlocal EnableDelayedExpansion

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

cd /d %~dp0
set ROOT_DIR=%~dp0
if %ROOT_DIR:~-1%==" set ROOT_DIR=%ROOT_DIR:~0,-1%

echo =======================================================
echo VoxStream: Live Captioner Suite - Uninstaller
echo =======================================================
echo.

set AUTO_YES=0
set CLEAN_MODELS=0
set CLEAN_CONFIG=0

:parse_args
if %~1== goto args_done
if /i %~1==-y set AUTO_YES=1
if /i %~1==--yes set AUTO_YES=1
if /i %~1==/y set AUTO_YES=1
if /i %~1==--clean-models set CLEAN_MODELS=1
if /i %~1==--clean-config set CLEAN_CONFIG=1
if /i %~1==--all (
 set AUTO_YES=1
 set CLEAN_MODELS=1
 set CLEAN_CONFIG=1
)
shift
goto parse_args
:args_done

if !AUTO_YES!==0 (
 echo This wizard will remove VoxStream from your system:
 echo - Stop running VoxStream background services
 echo - Remove the Desktop shortcut
 echo - Remove Windows Defender Firewall rule [port 8765]
 echo - Delete the Python virtual environment [.venv] (~2-4 GB)
 echo - Remove temporary cache files
 echo.
 set /p CONFIRM=Are you sure you want to proceed? (Y/N): 
 if /i not !CONFIRM!==y (
 echo [INFO] Uninstallation canceled by user.
 exit /b 0
 )
 echo.
)

:: ---------------------------------------------------------------------------
:: STEP 1: Stop Running Processes
:: ---------------------------------------------------------------------------
echo [1/5] Checking for running VoxStream processes...
powershell -NoProfile -Command Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*obs_captioner*' -or $_.CommandLine -like '*run_captioner*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } >nul 2>&1
echo [SUCCESS] Running processes stopped.
echo.

:: ---------------------------------------------------------------------------
:: STEP 2: Remove Desktop Shortcut
:: ---------------------------------------------------------------------------
echo [2/5] Removing Desktop shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -Command $d = [Environment]::GetFolderPath('Desktop'); $lnk = Join-Path $d 'VoxStream Live Captioner.lnk'; if (Test-Path $lnk) { Remove-Item -Force $lnk } >nul 2>&1
if exist %USERPROFILE%\Desktop\VoxStream Live Captioner.lnk (
 del /f /q %USERPROFILE%\Desktop\VoxStream Live Captioner.lnk >nul 2>&1
)
if exist %PUBLIC%\Desktop\VoxStream Live Captioner.lnk (
 del /f /q %PUBLIC%\Desktop\VoxStream Live Captioner.lnk >nul 2>&1
)
echo [SUCCESS] Desktop shortcut removed.
echo.

:: ---------------------------------------------------------------------------
:: STEP 3: Remove Firewall Rule
:: ---------------------------------------------------------------------------
echo [3/5] Removing Windows Defender Firewall rule for port 8765...
netsh advfirewall firewall show rule name=VoxStream Overlay TCP 8765 >nul 2>&1
if !errorlevel! equ 0 (
 netsh advfirewall firewall delete rule name=VoxStream Overlay TCP 8765 >nul 2>&1
 if !errorlevel! equ 0 (
 echo [SUCCESS] Firewall rule removed.
 ) else (
 echo [INFO] Requesting administrator permission to remove firewall rule...
 powershell -NoProfile -ExecutionPolicy Bypass -Command Start-Process netsh.exe -ArgumentList 'advfirewall firewall delete rule name="VoxStream Overlay TCP 8765"' -Verb RunAs -Wait >nul 2>&1
 echo [SUCCESS] Firewall rule cleanup requested.
 )
) else (
 echo [INFO] No firewall rule active.
)
echo.

:: ---------------------------------------------------------------------------
:: STEP 4: Remove Python Virtual Environment
:: ---------------------------------------------------------------------------
echo [4/5] Removing Python virtual environment [.venv]...
if exist %ROOT_DIR%\.venv (
 rmdir /s /q %ROOT_DIR%\.venv >nul 2>&1
 if exist %ROOT_DIR%\.venv (
 timeout /t 1 /nobreak >nul
 rmdir /s /q %ROOT_DIR%\.venv >nul 2>&1
 )
 if exist %ROOT_DIR%\.venv (
 echo [WARNING] Some files in .venv were locked and could not be removed immediately.
 ) else (
 echo [SUCCESS] Virtual environment removed (disk space reclaimed).
 )
) else (
 echo [INFO] Virtual environment [.venv] does not exist.
)
echo.

:: ---------------------------------------------------------------------------
:: STEP 5: Cache and Model Clean-up
:: ---------------------------------------------------------------------------
echo [5/5] Cleaning up temporary build and bytecode caches...
for /d /r %ROOT_DIR% %%d in (__pycache__) do (
 if exist %%d rmdir /s /q %%d >nul 2>&1
)
if exist %ROOT_DIR%\.pytest_cache rmdir /s /q %ROOT_DIR%\.pytest_cache >nul 2>&1

if !AUTO_YES!==0 (
 if !CLEAN_MODELS!==0 (
 echo.
 echo Do you also want to remove pre-cached offline AI models?
 echo (Vosk models in %LOCALAPPDATA%\vosk and Faster-Whisper models)
 set /p DEL_MODELS=Delete cached speech models? (Y/N): 
 if /i !DEL_MODELS!==y set CLEAN_MODELS=1
 )
 if !CLEAN_CONFIG!==0 (
 set /p DEL_CFG=Delete your personal config.json? (Y/N): 
 if /i !DEL_CFG!==y set CLEAN_CONFIG=1
 )
)

if !CLEAN_MODELS!==1 (
 echo [INFO] Removing cached Vosk models...
 if exist %LOCALAPPDATA%\vosk rmdir /s /q %LOCALAPPDATA%\vosk >nul 2>&1
 if exist %USERPROFILE%\.cache\vosk rmdir /s /q %USERPROFILE%\.cache\vosk >nul 2>&1
 
 echo [INFO] Removing cached Faster-Whisper / Hugging Face models...
 powershell -NoProfile -Command Get-ChildItem -Path (Join-Path $env:USERPROFILE '.cache\huggingface\hub') -Directory -Filter '*Systran*' -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force >nul 2>&1
 echo [SUCCESS] Offline AI speech models removed.
)

if !CLEAN_CONFIG!==1 (
 if exist %ROOT_DIR%\config.json (
 del /f /q %ROOT_DIR%\config.json >nul 2>&1
 echo [SUCCESS] config.json removed.
 )
)

echo.
echo =======================================================
echo [SUCCESS] VoxStream has been successfully uninstalled!
echo.
echo You can now safely delete the folder:
echo %ROOT_DIR%
echo =======================================================
echo.
if !AUTO_YES!==0 (
 echo Press any key to close this window...
 pause >nul
)
exit /b 0
