"""Standalone Native GUI Controller & Launcher for VoxStream.

Provides a desktop control window and taskbar application for Windows, macOS, and Linux:
- Live backend status monitoring (Engine, Emergency Fallback, Audio VU meter, Web ports).
- Direct Start / Stop / Restart / Pause controls.
- 1-Click quick launchers for Web Dashboard, Stage Display, OBS Overlay, and Scripture Projector.
- Real-time backend activity log console (eliminating the need for a black CMD window).
- System tray minimization and Windows AppUserModelID taskbar grouping.
- One-click Windows Desktop & Start Menu / Taskbar shortcut installer.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Set up launcher logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("obs_captioner.launcher")

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
    HAS_TKINTER = True
except ImportError:
    HAS_TKINTER = False

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import pystray
    HAS_PYSTRAY = True
except ImportError:
    HAS_PYSTRAY = False


def apply_windows_dark_titlebar(window: Any) -> bool:
    """Enable native Windows 10/11 immersive dark mode title bar for a Tkinter window.

    On sys.platform == 'win32', after window.update_idletasks(), gets the top-level HWND via
    ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id() and calls
    ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ...) with fallback to attribute 19.
    Wrapped in try/except so it never errors on headless/mocked windows.
    """
    if sys.platform != "win32":
        return False
    try:
        if hasattr(window, "update_idletasks"):
            window.update_idletasks()
        if not hasattr(window, "winfo_id"):
            return False
        wid = window.winfo_id()
        hwnd = wid
        windll = getattr(ctypes, "windll", None)
        if windll:
            user32 = getattr(windll, "user32", None)
            if user32 and hasattr(user32, "GetParent"):
                parent = user32.GetParent(wid)
                hwnd = parent or wid
            dwmapi = getattr(windll, "dwmapi", None)
            if dwmapi and hasattr(dwmapi, "DwmSetWindowAttribute"):
                DWMWA_USE_IMMERSIVE_DARK_MODE = 20
                DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19
                val = ctypes.c_int(1)
                hr = dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    DWMWA_USE_IMMERSIVE_DARK_MODE,
                    ctypes.byref(val),
                    ctypes.sizeof(val),
                )
                if hr != 0:
                    dwmapi.DwmSetWindowAttribute(
                        hwnd,
                        DWMWA_USE_IMMERSIVE_DARK_MODE_OLD,
                        ctypes.byref(val),
                        ctypes.sizeof(val),
                    )
                return True
    except Exception as e:
        logger.debug(f"apply_windows_dark_titlebar error: {e}")
        return False
    return False


def get_platform_fonts() -> Tuple[str, str]:
    """Return platform-appropriate primary UI font family and monospace font family.

    Returns:
        ("Segoe UI", "Consolas") on Windows
        ("Helvetica Neue", "Menlo") on macOS
        ("DejaVu Sans", "Monospace") on Linux
    """
    if sys.platform == "win32":
        return ("Segoe UI", "Consolas")
    elif sys.platform == "darwin":
        return ("Helvetica Neue", "Menlo")
    else:
        return ("DejaVu Sans", "Monospace")


def get_project_root() -> Path:
    """Resolve the absolute root directory of the VoxStream project."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # This file is located at <ROOT>/obs_captioner/launcher.py
    return Path(__file__).resolve().parent.parent


def find_python_executable(root_dir: Optional[Path] = None) -> str:
    """Find the best Python executable to run the VoxStream backend."""
    if root_dir is None:
        root_dir = get_project_root()

    if sys.platform == "win32":
        venv_py = root_dir / ".venv" / "Scripts" / "python.exe"
        if venv_py.is_file():
            return str(venv_py)
    else:
        venv_py = root_dir / ".venv" / "bin" / "python"
        if venv_py.is_file():
            return str(venv_py)

    # When running as a frozen PyInstaller exe, sys.executable is the launcher exe.
    # Look for system Python rather than re-executing our own binary.
    if getattr(sys, "frozen", False):
        import shutil
        for cand in ("python", "python3", "py"):
            found = shutil.which(cand)
            if found:
                return found

        if sys.platform == "win32":
            local_app_data = os.environ.get("LOCALAPPDATA")
            if local_app_data:
                for py_cand in (
                    Path(local_app_data) / "Programs" / "Python" / "Python311" / "python.exe",
                    Path(local_app_data) / "Programs" / "Python" / "Python312" / "python.exe",
                    Path(local_app_data) / "Programs" / "Python" / "Python310" / "python.exe",
                ):
                    if py_cand.is_file():
                        return str(py_cand)

    # Fallback to current running interpreter
    return sys.executable


def create_windows_shortcuts(root_dir: Optional[Path] = None, include_startup: bool = True) -> Dict[str, Any]:
    """Create Windows Desktop, Start Menu, and optionally Startup shortcuts.
    
    This enables zero-console execution, allows pinning to the Windows Taskbar,
    and supports morning auto-boot on user logon.
    """
    if sys.platform != "win32":
        return {"success": False, "error": "Shortcuts can only be generated on Windows."}

    if root_dir is None:
        root_dir = get_project_root()

    exe_path = root_dir / "VoxStream.exe"
    vbs_path = root_dir / "VoxStream.vbs"
    ico_path = root_dir / "obs_captioner" / "web" / "static" / "favicon.ico"

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass and not ico_path.is_file():
        cand = Path(meipass) / "obs_captioner" / "web" / "static" / "favicon.ico"
        if cand.is_file():
            ico_path = cand

    if getattr(sys, "frozen", False) and Path(sys.executable).is_file():
        target_path = str(Path(sys.executable).resolve())
        target_args = ""
        icon_loc = f"{target_path}, 0"
    elif exe_path.is_file():
        target_path = str(exe_path.resolve())
        target_args = ""
        icon_loc = f"{target_path}, 0"
    elif vbs_path.is_file():
        target_path = "wscript.exe"
        target_args = f'"{str(vbs_path)}"'
        icon_loc = f"{str(ico_path)}, 0" if ico_path.is_file() else "wscript.exe, 0"
    else:
        return {"success": False, "error": f"Neither VoxStream.exe nor VoxStream.vbs found in {root_dir}"}

    startup_block = ""
    if include_startup:
        startup_block = f"""
# 3. Startup Folder Shortcut (Morning Auto-Boot)
$Startup = [System.Environment]::GetFolderPath('Startup')
if (!(Test-Path $Startup)) {{
    New-Item -ItemType Directory -Path $Startup -Force | Out-Null
}}
$StartupShortcut = $WshShell.CreateShortcut("$Startup\\VoxStream.lnk")
$StartupShortcut.TargetPath = "{target_path}"
$StartupShortcut.Arguments = '{target_args}'
$StartupShortcut.WorkingDirectory = "{str(root_dir)}"
$StartupShortcut.IconLocation = "{icon_loc}"
$StartupShortcut.Description = "VoxStream Live Captioner Control Center"
$StartupShortcut.Save()
"""

    ps_script = f"""
$WshShell = New-Object -ComObject WScript.Shell

# 1. Desktop Shortcut
$Desktop = [System.Environment]::GetFolderPath('Desktop')
$DesktopShortcut = $WshShell.CreateShortcut("$Desktop\\VoxStream.lnk")
$DesktopShortcut.TargetPath = "{target_path}"
$DesktopShortcut.Arguments = '{target_args}'
$DesktopShortcut.WorkingDirectory = "{str(root_dir)}"
$DesktopShortcut.IconLocation = "{icon_loc}"
$DesktopShortcut.Description = "VoxStream Live Captioner Control Center"
$DesktopShortcut.Save()

# 2. Start Menu Shortcut (Enables Start Search & Pin to Taskbar)
$Programs = [System.Environment]::GetFolderPath('Programs')
$StartDir = "$Programs\\VoxStream"
if (!(Test-Path $StartDir)) {{
    New-Item -ItemType Directory -Path $StartDir -Force | Out-Null
}}
$StartShortcut = $WshShell.CreateShortcut("$StartDir\\VoxStream.lnk")
$StartShortcut.TargetPath = "{target_path}"
$StartShortcut.Arguments = '{target_args}'
$StartShortcut.WorkingDirectory = "{str(root_dir)}"
$StartShortcut.IconLocation = "{icon_loc}"
$StartShortcut.Description = "VoxStream Live Captioner Control Center"
$StartShortcut.Save()
{startup_block}
"""
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=creationflags,
        )
        if res.returncode == 0:
            msg = "Shortcuts created on Desktop, in Start Menu, and Startup folder!" if include_startup else "Shortcuts created on Desktop and in Start Menu!"
            return {"success": True, "message": msg}
        return {"success": False, "error": res.stderr or res.stdout}
    except Exception as e:
        return {"success": False, "error": str(e)}


def remove_windows_shortcuts(root_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Remove Windows Desktop, Start Menu, and Startup shortcuts created for VoxStream."""
    if sys.platform != "win32":
        return {"success": False, "error": "Shortcuts can only be removed on Windows."}

    ps_script = """
$Desktop = [System.Environment]::GetFolderPath('Desktop')
$lnk1 = Join-Path $Desktop 'VoxStream.lnk'
$lnk2 = Join-Path $Desktop 'VoxStream Live Captioner.lnk'
if (Test-Path $lnk1) { Remove-Item -Force $lnk1 }
if (Test-Path $lnk2) { Remove-Item -Force $lnk2 }

$Programs = [System.Environment]::GetFolderPath('Programs')
$StartDir = Join-Path $Programs 'VoxStream'
if (Test-Path $StartDir) { Remove-Item -Recurse -Force $StartDir }

$Startup = [System.Environment]::GetFolderPath('Startup')
$s1 = Join-Path $Startup 'VoxStream.lnk'
$s2 = Join-Path $Startup 'VoxStream Live Captioner.lnk'
if (Test-Path $s1) { Remove-Item -Force $s1 }
if (Test-Path $s2) { Remove-Item -Force $s2 }
"""
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=creationflags,
        )
        if res.returncode == 0:
            return {"success": True, "message": "Shortcuts removed from Desktop, Start Menu, and Startup."}
        return {"success": False, "error": res.stderr or res.stdout}
    except Exception as e:
        return {"success": False, "error": str(e)}


def create_macos_shortcuts(root_dir: Optional[Path] = None, include_startup: bool = True) -> Dict[str, Any]:
    """Create macOS Desktop launcher (.command) and optionally configure LaunchAgent.

    Creates a double-clickable ~/Desktop/VoxStream.command with executable permissions (chmod +x).
    """
    if sys.platform != "darwin":
        return {"success": False, "error": "macOS shortcuts can only be created on macOS."}

    if root_dir is None:
        root_dir = get_project_root()

    desktop_dir = Path.home() / "Desktop"
    cmd_file = desktop_dir / "VoxStream.command"
    run_script = root_dir / "run_captioner.sh"

    script_content = f"""#!/bin/bash
cd "{str(root_dir)}" && /bin/bash "{str(run_script)}"
"""
    try:
        desktop_dir.mkdir(parents=True, exist_ok=True)
        with open(cmd_file, "w", encoding="utf-8") as f:
            f.write(script_content)
        try:
            os.chmod(cmd_file, 0o755)
        except Exception:
            pass

        if include_startup:
            set_startup_enabled(True, root_dir)

        return {
            "success": True,
            "message": f"VoxStream.command created on Desktop{' and LaunchAgent registered' if include_startup else ''}.",
            "path": str(cmd_file),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def create_os_shortcuts(root_dir: Optional[Path] = None, include_startup: bool = True) -> Dict[str, Any]:
    """Cross-platform shortcut installer for Windows and macOS."""
    if sys.platform == "win32":
        return create_windows_shortcuts(root_dir, include_startup=include_startup)
    elif sys.platform == "darwin":
        return create_macos_shortcuts(root_dir, include_startup=include_startup)
    else:
        return {"success": False, "error": f"Shortcuts creation not supported on {sys.platform}."}


def is_startup_enabled() -> bool:
    """Check if VoxStream startup launcher is enabled on the current operating system."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            p1 = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "VoxStream.lnk"
            p2 = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "VoxStream Live Captioner.lnk"
            if p1.is_file() or p2.is_file():
                return True
        return False
    elif sys.platform == "darwin":
        plist_path = Path.home() / "Library" / "LaunchAgents" / "com.voxstream.captioner.plist"
        return plist_path.is_file()
    return False

is_windows_startup_enabled = is_startup_enabled


def _set_macos_startup(enabled: bool, root_dir: Path) -> Dict[str, Any]:
    """Configure or remove macOS LaunchAgent in ~/Library/LaunchAgents/com.voxstream.captioner.plist."""
    agents_dir = Path.home() / "Library" / "LaunchAgents"
    plist_path = agents_dir / "com.voxstream.captioner.plist"

    if not enabled:
        try:
            if plist_path.is_file() or plist_path.exists():
                plist_path.unlink()
            return {"success": True, "enabled": False, "message": "macOS LaunchAgent removed."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    run_script = root_dir / "run_captioner.sh"
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.voxstream.captioner</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>{str(run_script)}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{str(root_dir)}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
"""
    try:
        agents_dir.mkdir(parents=True, exist_ok=True)
        with open(plist_path, "w", encoding="utf-8") as f:
            f.write(plist_content)
        return {"success": True, "enabled": True, "message": "macOS LaunchAgent created in ~/Library/LaunchAgents."}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _set_windows_startup(enabled: bool, root_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Enable or disable auto-start of VoxStream upon Windows user login."""
    if sys.platform != "win32":
        return {"success": False, "error": "Windows startup can only be configured on Windows."}

    if root_dir is None:
        root_dir = get_project_root()

    if not enabled:
        ps_script = """
$Startup = [System.Environment]::GetFolderPath('Startup')
$s1 = Join-Path $Startup 'VoxStream.lnk'
$s2 = Join-Path $Startup 'VoxStream Live Captioner.lnk'
if (Test-Path $s1) { Remove-Item -Force $s1 }
if (Test-Path $s2) { Remove-Item -Force $s2 }
"""
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            res = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=creationflags,
            )
            if res.returncode == 0:
                return {"success": True, "enabled": False, "message": "Startup shortcut removed."}
            return {"success": False, "error": res.stderr or res.stdout}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # Enabled:
    exe_path = root_dir / "VoxStream.exe"
    vbs_path = root_dir / "VoxStream.vbs"
    ico_path = root_dir / "obs_captioner" / "web" / "static" / "favicon.ico"

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass and not ico_path.is_file():
        cand = Path(meipass) / "obs_captioner" / "web" / "static" / "favicon.ico"
        if cand.is_file():
            ico_path = cand

    if getattr(sys, "frozen", False) and Path(sys.executable).is_file():
        target_path = str(Path(sys.executable).resolve())
        target_args = ""
        icon_loc = f"{target_path}, 0"
    elif exe_path.is_file():
        target_path = str(exe_path.resolve())
        target_args = ""
        icon_loc = f"{target_path}, 0"
    elif vbs_path.is_file():
        target_path = "wscript.exe"
        target_args = f'"{str(vbs_path)}"'
        icon_loc = f"{str(ico_path)}, 0" if ico_path.is_file() else "wscript.exe, 0"
    else:
        return {"success": False, "error": f"Neither VoxStream.exe nor VoxStream.vbs found in {root_dir}"}

    ps_script = f"""
$WshShell = New-Object -ComObject WScript.Shell
$Startup = [System.Environment]::GetFolderPath('Startup')
if (!(Test-Path $Startup)) {{
    New-Item -ItemType Directory -Path $Startup -Force | Out-Null
}}
$StartupShortcut = $WshShell.CreateShortcut("$Startup\\VoxStream.lnk")
$StartupShortcut.TargetPath = "{target_path}"
$StartupShortcut.Arguments = '{target_args}'
$StartupShortcut.WorkingDirectory = "{str(root_dir)}"
$StartupShortcut.IconLocation = "{icon_loc}"
$StartupShortcut.Description = "VoxStream Live Captioner Control Center"
$StartupShortcut.Save()
"""
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=creationflags,
        )
        if res.returncode == 0:
            return {"success": True, "enabled": True, "message": "Startup shortcut created in Windows Startup folder."}
        return {"success": False, "error": res.stderr or res.stdout}
    except Exception as e:
        return {"success": False, "error": str(e)}


def set_startup_enabled(enabled: bool, root_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Enable or disable auto-start of VoxStream upon user logon on Windows and macOS."""
    if root_dir is None:
        root_dir = get_project_root()

    if sys.platform == "win32":
        return _set_windows_startup(enabled, root_dir)
    elif sys.platform == "darwin":
        return _set_macos_startup(enabled, root_dir)
    else:
        return {"success": False, "error": f"Startup configuration not supported on {sys.platform}."}

set_windows_startup = set_startup_enabled


_SINGLE_INSTANCE_MUTEX = None
_SINGLE_INSTANCE_SOCKET = None


def acquire_single_instance_lock() -> bool:
    """Ensure only a single instance of the VoxStream Launcher runs at a time.

    Uses Win32 CreateMutexW on Windows, checking GetLastError() == 183
    (ERROR_ALREADY_EXISTS), falling back to a local loopback socket bind
    on POSIX platforms. Returns True if this instance successfully acquired
    the lock, False if another instance is already running.
    """
    global _SINGLE_INSTANCE_MUTEX, _SINGLE_INSTANCE_SOCKET
    if sys.platform == "win32":
        try:
            windll = getattr(ctypes, "windll", None)
            if windll and hasattr(windll, "kernel32"):
                kernel32 = windll.kernel32
                ERROR_ALREADY_EXISTS = 183
                mutex_name = "Local\\VoxStreamLauncherMutex"
                handle = kernel32.CreateMutexW(None, False, mutex_name)
                last_error = kernel32.GetLastError()
                if last_error == ERROR_ALREADY_EXISTS or not handle:
                    if handle and hasattr(kernel32, "CloseHandle"):
                        kernel32.CloseHandle(handle)
                    return False
                _SINGLE_INSTANCE_MUTEX = handle
                return True
        except Exception as e:
            logger.debug(f"Win32 CreateMutex failed: {e}")
            # Fall through to socket fallback

    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 48921))
        s.listen(1)
        _SINGLE_INSTANCE_SOCKET = s
        return True
    except Exception as e:
        logger.debug(f"Socket single instance lock failed: {e}")
        return False


def bring_existing_launcher_to_front(window_title: str = "VoxStream Control Center") -> bool:
    """Find and bring an existing VoxStream launcher window to the foreground.

    On Windows, uses FindWindowW, ShowWindow(SW_RESTORE=9), and SetForegroundWindow.
    Returns True if an existing window was found and restored, False otherwise.
    """
    if sys.platform == "win32":
        try:
            windll = getattr(ctypes, "windll", None)
            if windll and hasattr(windll, "user32"):
                user32 = windll.user32
                hwnd = user32.FindWindowW(None, window_title)
                if hwnd:
                    SW_RESTORE = 9
                    user32.ShowWindow(hwnd, SW_RESTORE)
                    user32.SetForegroundWindow(hwnd)
                    return True
        except Exception as e:
            logger.debug(f"bring_existing_launcher_to_front failed: {e}")
    return False


def release_single_instance_lock():
    """Release single instance mutex and socket lock (for testing and clean exit)."""
    global _SINGLE_INSTANCE_MUTEX, _SINGLE_INSTANCE_SOCKET
    if sys.platform == "win32" and _SINGLE_INSTANCE_MUTEX:
        try:
            windll = getattr(ctypes, "windll", None)
            if windll and hasattr(windll, "kernel32"):
                windll.kernel32.CloseHandle(_SINGLE_INSTANCE_MUTEX)
        except Exception:
            pass
        _SINGLE_INSTANCE_MUTEX = None
    if _SINGLE_INSTANCE_SOCKET:
        try:
            _SINGLE_INSTANCE_SOCKET.close()
        except Exception:
            pass
        _SINGLE_INSTANCE_SOCKET = None


class BackendProcessManager:
    """Manages the lifecycle of the VoxStream backend background process."""

    def __init__(self, root_dir: Path, on_log_line=None, on_status_change=None):
        self.root_dir = root_dir
        self.on_log_line = on_log_line
        self.on_status_change = on_status_change

        self.process: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_requested = False
        self.is_starting = False
        self.attached_to_existing = False

    @property
    def is_running(self) -> bool:
        if self.attached_to_existing:
            return True
        return self.process is not None and self.process.poll() is None

    def start(self, extra_args: Optional[list] = None) -> bool:
        """Start the VoxStream backend process."""
        if self.is_running or self.is_starting:
            return True

        self.is_starting = True
        self._stop_requested = False

        # First check if backend is already running on port 8765
        try:
            req = urllib.request.Request("http://127.0.0.1:8765/api/status")
            with urllib.request.urlopen(req, timeout=0.8) as resp:
                if resp.status == 200:
                    self.attached_to_existing = True
                    self.is_starting = False
                    if self.on_log_line:
                        self.on_log_line("[VoxStream] Attached to existing active backend instance on port 8765.")
                    if self.on_status_change:
                        self.on_status_change("RUNNING")
                    return True
        except Exception:
            pass

        py_exe = find_python_executable(self.root_dir)

        # Prepare environment variables (same as run_captioner.bat)
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["VOXSTREAM_RUNNER"] = "launcher_gui"

        if sys.platform == "win32":
            venv_dir = self.root_dir / ".venv"
            # CUDA DLLs
            nvidia_dir = venv_dir / "Lib" / "site-packages" / "nvidia"
            if nvidia_dir.is_dir():
                for sub in nvidia_dir.iterdir():
                    bin_dir = sub / "bin"
                    if bin_dir.is_dir():
                        env["PATH"] = f"{bin_dir};" + env.get("PATH", "")
            # DirectML / ONNX
            dml_dir = venv_dir / "Lib" / "site-packages" / "torch_directml"
            if dml_dir.is_dir():
                env["PATH"] = f"{dml_dir};" + env.get("PATH", "")
            onnx_dir = venv_dir / "Lib" / "site-packages" / "onnxruntime" / "capi"
            if onnx_dir.is_dir():
                env["PATH"] = f"{onnx_dir};" + env.get("PATH", "")

        cmd = [py_exe, "-m", "obs_captioner.main"]
        if extra_args:
            cmd.extend(extra_args)

        try:
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW

            self.process = subprocess.Popen(
                cmd,
                cwd=str(self.root_dir),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )

            self.is_starting = False
            if self.on_status_change:
                self.on_status_change("RUNNING")

            # Start background stdout reader
            self._reader_thread = threading.Thread(
                target=self._read_output_loop,
                daemon=True,
                name="BackendLogReader",
            )
            self._reader_thread.start()

            # Start process exit monitor
            self._monitor_thread = threading.Thread(
                target=self._monitor_loop,
                daemon=True,
                name="BackendProcessMonitor",
            )
            self._monitor_thread.start()

            return True
        except Exception as e:
            self.is_starting = False
            logger.error(f"Failed to start backend process: {e}")
            if self.on_log_line:
                self.on_log_line(f"[LAUNCHER ERROR] Failed to start backend: {e}")
            if self.on_status_change:
                self.on_status_change("ERROR")
            return False

    def stop(self, timeout_sec: float = 3.5):
        """Stop the backend process cleanly."""
        self._stop_requested = True
        was_attached = self.attached_to_existing
        self.attached_to_existing = False

        if not self.is_running and not was_attached:
            if self.on_status_change:
                self.on_status_change("STOPPED")
            return

        # Attempt graceful shutdown via HTTP API first
        try:
            req = urllib.request.Request("http://127.0.0.1:8765/api/control/shutdown", method="POST")
            urllib.request.urlopen(req, timeout=1.5)
        except Exception:
            pass

        # Wait for exit
        t_start = time.time()
        if was_attached:
            while time.time() - t_start < timeout_sec:
                try:
                    req = urllib.request.Request("http://127.0.0.1:8765/api/status")
                    with urllib.request.urlopen(req, timeout=0.5) as resp:
                        if resp.status == 200:
                            time.sleep(0.2)
                            continue
                except Exception:
                    break
        else:
            while self.is_running and (time.time() - t_start < timeout_sec):
                time.sleep(0.2)

        # Force terminate if still running
        if self.is_running and self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=2.0)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass

        self.process = None
        if self.on_status_change:
            self.on_status_change("STOPPED")

    def restart(self):
        """Trigger backend restart."""
        # Attempt graceful restart via HTTP API
        try:
            req = urllib.request.Request("http://127.0.0.1:8765/api/control/restart", method="POST")
            urllib.request.urlopen(req, timeout=1.5)
            return
        except Exception:
            pass

        self.stop()
        time.sleep(1.0)
        self.start()

    def _read_output_loop(self):
        """Worker thread that streams stdout/stderr lines to listener."""
        if not self.process or not self.process.stdout:
            return
        try:
            for line in iter(self.process.stdout.readline, ""):
                if not line:
                    break
                stripped = line.rstrip()
                if stripped and self.on_log_line:
                    self.on_log_line(stripped)
        except Exception:
            pass

    def _monitor_loop(self):
        """Worker thread to monitor process exit and handle exit code 42 (reload)."""
        proc = self.process
        if not proc:
            return

        exit_code = proc.wait()
        logger.info(f"Backend process terminated with exit code: {exit_code}")

        # Exit code 42 indicates an intentional application restart
        if exit_code == 42 and not self._stop_requested:
            if self.on_log_line:
                self.on_log_line("[VoxStream] Restart requested by application (exit code 42). Reloading in 1s...")
            if self.on_status_change:
                self.on_status_change("STARTING")
            time.sleep(1.0)
            self.process = None
            self.start()
            return

        self.process = None
        if not self._stop_requested:
            status = "STOPPED" if exit_code == 0 else "ERROR"
            if self.on_status_change:
                self.on_status_change(status)
            if self.on_log_line and exit_code != 0:
                self.on_log_line(f"[VoxStream] Backend process stopped with exit code: {exit_code}")


class VoxStreamLauncherGUI:
    """Tkinter-based sleek dark-mode desktop launcher and controller for VoxStream."""

    def __init__(self, root: tk.Tk, autostart: bool = True, start_minimized: bool = False):
        self.root = root
        self.root_dir = get_project_root()
        self.autostart = autostart
        self.start_minimized = start_minimized

        self.backend = BackendProcessManager(
            root_dir=self.root_dir,
            on_log_line=self._on_backend_log,
            on_status_change=self._on_backend_status_change,
        )

        self.log_queue = queue.Queue()
        self._status_queue: queue.Queue = queue.Queue()
        self._lifecycle_queue: queue.Queue = queue.Queue()
        self._status_fetch_inflight = False
        self.last_status_data: Dict[str, Any] = {}
        self.is_paused = False
        self._tray_icon: Optional[Any] = None
        self._tray_thread: Optional[threading.Thread] = None

        # App State
        self.status_state = "STOPPED"  # STOPPED, STARTING, RUNNING, PAUSED, ERROR

        # Determine initial LAN Stage Display URL
        try:
            from .hardware import get_local_ip
            lan_ip = get_local_ip()
            if not lan_ip or lan_ip in ("127.0.0.1", "0.0.0.0"):
                lan_ip = "localhost"
        except Exception:
            lan_ip = "localhost"
        self.current_display_url = f"http://{lan_ip}:8765/display"
        self._is_url_copied_flashing = False

        self._init_window()
        self._init_styles()
        self._build_ui()
        self._init_system_tray()

        # Start periodic log drain and status polling
        self.root.after(100, self._drain_logs)
        self.root.after(1000, self._poll_backend_status)

        if self.autostart:
            self.root.after(400, self.start_backend)

        if self.start_minimized:
            self.root.after(600, self.minimize_to_tray)

    def _init_window(self):
        """Configure native window properties, dimensions, and Windows AppUserModelID."""
        self.root.title("VoxStream Control Center")
        self.root.geometry("780x620")
        self.root.minsize(680, 520)
        self.root.configure(bg="#0f172a")  # Deep Slate 900

        # High-DPI and Taskbar Grouping on Windows
        if sys.platform == "win32":
            try:
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "VoxStream.LiveCaptioner.Launcher.1.1"
                )
            except Exception:
                pass
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass
            apply_windows_dark_titlebar(self.root)

        # Window Icon
        ico_file = self.root_dir / "obs_captioner" / "web" / "static" / "favicon.ico"
        png_file = self.root_dir / "obs_captioner" / "web" / "static" / "favicon-32x32.png"

        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            cand_ico = Path(meipass) / "obs_captioner" / "web" / "static" / "favicon.ico"
            if cand_ico.is_file():
                ico_file = cand_ico
            cand_png = Path(meipass) / "obs_captioner" / "web" / "static" / "favicon-32x32.png"
            if cand_png.is_file():
                png_file = cand_png

        if sys.platform == "win32" and ico_file.is_file():
            try:
                self.root.iconbitmap(str(ico_file))
            except Exception as e:
                logger.debug(f"Could not load iconbitmap: {e}")
        elif HAS_PIL and png_file.is_file():
            try:
                im = Image.open(png_file)
                self._icon_img = ImageTk.PhotoImage(im)
                self.root.iconphoto(True, self._icon_img)
            except Exception as e:
                logger.debug(f"Could not load iconphoto: {e}")

        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)

    def _init_styles(self):
        """Set up custom dark-mode ttk styles."""
        sans_font, mono_font = get_platform_fonts()
        self.sans_font = sans_font
        self.mono_font = mono_font

        self.style = ttk.Style()
        try:
            self.style.theme_use("clam")
        except Exception:
            pass

        # Card & frame colors
        self.style.configure("TFrame", background="#0f172a")
        self.style.configure("Card.TFrame", background="#1e293b", relief="flat")
        self.style.configure("InnerCard.TFrame", background="#0f172a", relief="flat")

        # Label styling
        self.style.configure("Title.TLabel", background="#0f172a", foreground="#f8fafc", font=(sans_font, 16, "bold"))
        self.style.configure("Subtitle.TLabel", background="#0f172a", foreground="#94a3b8", font=(sans_font, 9))
        self.style.configure("CardHeader.TLabel", background="#1e293b", foreground="#38bdf8", font=(sans_font, 10, "bold"))
        self.style.configure("CardText.TLabel", background="#1e293b", foreground="#f1f5f9", font=(sans_font, 9))
        self.style.configure("CardMuted.TLabel", background="#1e293b", foreground="#94a3b8", font=(sans_font, 8))

        # Progress bar
        self.style.configure("Audio.Horizontal.TProgressbar", troughcolor="#0f172a", background="#10b981", thickness=8)

    def _build_ui(self):
        """Assemble header, telemetry cards, primary controls, and console log area."""
        main_container = ttk.Frame(self.root, padding=16)
        main_container.pack(fill=tk.BOTH, expand=True)

        # 1. Header Bar
        header_frame = ttk.Frame(main_container)
        header_frame.pack(fill=tk.X, pady=(0, 12))

        title_sub_frame = ttk.Frame(header_frame)
        title_sub_frame.pack(side=tk.LEFT)

        title_label = ttk.Label(title_sub_frame, text="🎙️ VOXSTREAM", style="Title.TLabel")
        title_label.pack(anchor="w")

        from .version import VERSION
        subtitle_label = ttk.Label(
            title_sub_frame,
            text=f"Live Speech Captioning & Stage Display • v{VERSION}",
            style="Subtitle.TLabel",
        )
        subtitle_label.pack(anchor="w")

        # Status Badge Pill
        self.status_badge = tk.Label(
            header_frame,
            text="● STOPPED",
            font=(self.sans_font, 10, "bold"),
            bg="#334155",
            fg="#f8fafc",
            padx=12,
            pady=4,
            relief="flat",
        )
        self.status_badge.pack(side=tk.RIGHT)

        # 2. Telemetry Cards (3-column layout)
        cards_frame = ttk.Frame(main_container)
        cards_frame.pack(fill=tk.X, pady=(0, 12))
        cards_frame.columnconfigure(0, weight=1)
        cards_frame.columnconfigure(1, weight=1)
        cards_frame.columnconfigure(2, weight=1)

        # Card 1: Engine & Model
        c1 = ttk.Frame(cards_frame, style="Card.TFrame", padding=10)
        c1.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        ttk.Label(c1, text="SPEECH ENGINE", style="CardHeader.TLabel").pack(anchor="w")
        self.lbl_engine = ttk.Label(c1, text="Gemini Live", style="CardText.TLabel", font=(self.sans_font, 10, "bold"))
        self.lbl_engine.pack(anchor="w", pady=(2, 0))
        self.lbl_fallback = ttk.Label(c1, text="Fallback: Vosk Ready", style="CardMuted.TLabel")
        self.lbl_fallback.pack(anchor="w")

        # Card 2: Audio Input & VU Meter
        c2 = ttk.Frame(cards_frame, style="Card.TFrame", padding=10)
        c2.grid(row=0, column=1, sticky="nsew", padx=3)
        ttk.Label(c2, text="AUDIO INPUT", style="CardHeader.TLabel").pack(anchor="w")
        self.lbl_audio_device = ttk.Label(c2, text="Default Microphone", style="CardText.TLabel")
        self.lbl_audio_device.pack(anchor="w", pady=(2, 2))
        self.vu_bar = ttk.Progressbar(c2, style="Audio.Horizontal.TProgressbar", orient="horizontal", maximum=100)
        self.vu_bar.pack(fill=tk.X, pady=(2, 0))

        # Card 3: Network & Web Interface
        c3 = ttk.Frame(cards_frame, style="Card.TFrame", padding=10)
        c3.grid(row=0, column=2, sticky="nsew", padx=(6, 0))
        ttk.Label(c3, text="STAGE DISPLAY (LAN)", style="CardHeader.TLabel").pack(anchor="w")

        url_row = tk.Frame(c3, bg="#1e293b")
        url_row.pack(fill=tk.X, pady=(2, 0))

        self.lbl_url = tk.Label(
            url_row,
            text=self.current_display_url,
            font=(self.sans_font, 9),
            bg="#1e293b",
            fg="#38bdf8",
            activebackground="#1e293b",
            activeforeground="#7dd3fc",
            cursor="hand2",
            anchor="w",
        )
        self.lbl_url.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.lbl_url.bind("<Button-1>", self.copy_display_url)

        self.btn_copy_url = tk.Button(
            url_row,
            text="📋 Copy Link",
            font=(self.sans_font, 7, "bold"),
            bg="#334155",
            fg="#38bdf8",
            activebackground="#475569",
            activeforeground="#ffffff",
            relief="flat",
            padx=4,
            pady=1,
            cursor="hand2",
            command=self.copy_display_url,
        )
        self.btn_copy_url.pack(side=tk.RIGHT, padx=(4, 0))

        self.lbl_clients = ttk.Label(c3, text="0 Clients Connected", style="CardMuted.TLabel")
        self.lbl_clients.pack(anchor="w")

        # 3. Action Control Bar
        controls_frame = ttk.Frame(main_container)
        controls_frame.pack(fill=tk.X, pady=(0, 10))

        self.btn_start_stop = tk.Button(
            controls_frame,
            text="▶ Start Backend",
            font=(self.sans_font, 10, "bold"),
            bg="#059669",  # Emerald Green
            fg="#ffffff",
            activebackground="#10b981",
            activeforeground="#ffffff",
            relief="flat",
            padx=16,
            pady=6,
            command=self._toggle_start_stop,
            cursor="hand2",
        )
        self.btn_start_stop.pack(side=tk.LEFT, padx=(0, 8))

        self.btn_pause = tk.Button(
            controls_frame,
            text="⏸ Pause Captions",
            font=(self.sans_font, 9),
            bg="#334155",
            fg="#f8fafc",
            activebackground="#475569",
            activeforeground="#ffffff",
            relief="flat",
            padx=12,
            pady=6,
            command=self._toggle_pause,
            cursor="hand2",
            state=tk.DISABLED,
        )
        self.btn_pause.pack(side=tk.LEFT, padx=(0, 8))

        self.btn_restart = tk.Button(
            controls_frame,
            text="🔄 Restart Engine",
            font=(self.sans_font, 9),
            bg="#334155",
            fg="#f8fafc",
            activebackground="#475569",
            activeforeground="#ffffff",
            relief="flat",
            padx=12,
            pady=6,
            command=self._restart_engine,
            cursor="hand2",
            state=tk.DISABLED,
        )
        self.btn_restart.pack(side=tk.LEFT, padx=(0, 8))

        # Quick Open in Browser Dropdown / Buttons
        quick_frame = ttk.Frame(controls_frame)
        quick_frame.pack(side=tk.RIGHT)

        for title, path in [
            ("🎙️ Dashboard", "/dashboard"),
            ("📺 Stage Monitor", "/display"),
            ("🎬 OBS Overlay", "/overlay"),
            ("📖 Scripture", "/bible"),
        ]:
            b = tk.Button(
                quick_frame,
                text=title,
                font=(self.sans_font, 8),
                bg="#1e293b",
                fg="#38bdf8",
                activebackground="#334155",
                activeforeground="#ffffff",
                relief="flat",
                padx=8,
                pady=4,
                cursor="hand2",
                command=lambda p=path: self._open_browser(p),
            )
            b.pack(side=tk.LEFT, padx=2)

        # 4. Console Log Terminal
        console_frame = ttk.Frame(main_container, style="Card.TFrame", padding=8)
        console_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        console_header = ttk.Frame(console_frame, style="Card.TFrame")
        console_header.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(console_header, text="LIVE BACKEND LOGS", style="CardHeader.TLabel").pack(side=tk.LEFT)

        self.auto_scroll_var = tk.BooleanVar(value=True)
        chk_scroll = tk.Checkbutton(
            console_header,
            text="Auto-scroll",
            variable=self.auto_scroll_var,
            bg="#1e293b",
            fg="#94a3b8",
            selectcolor="#0f172a",
            activebackground="#1e293b",
            activeforeground="#f8fafc",
            font=(self.sans_font, 8),
        )
        chk_scroll.pack(side=tk.RIGHT, padx=(4, 0))

        btn_clear = tk.Button(
            console_header,
            text="Clear",
            font=(self.sans_font, 8),
            bg="#0f172a",
            fg="#94a3b8",
            relief="flat",
            padx=6,
            pady=1,
            cursor="hand2",
            command=self._clear_logs,
        )
        btn_clear.pack(side=tk.RIGHT)

        # Scrolling Text Box
        text_container = ttk.Frame(console_frame)
        text_container.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(
            text_container,
            bg="#0b0f19",
            fg="#cbd5e1",
            insertbackground="#38bdf8",
            font=(self.mono_font, 9),
            wrap=tk.WORD,
            relief="flat",
            padx=8,
            pady=6,
        )
        scrollbar = ttk.Scrollbar(text_container, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)

        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 5. Footer Bar (Utilities & Shortcuts)
        footer_frame = ttk.Frame(main_container)
        footer_frame.pack(fill=tk.X)

        if sys.platform in ("win32", "darwin"):
            btn_text = "📌 Add to Desktop & Taskbar" if sys.platform == "win32" else "📌 Add to Desktop"
            btn_shortcuts = tk.Button(
                footer_frame,
                text=btn_text,
                font=(self.sans_font, 8),
                bg="#1e293b",
                fg="#f8fafc",
                activebackground="#334155",
                relief="flat",
                padx=8,
                pady=3,
                cursor="hand2",
                command=self._create_shortcuts_action,
            )
            btn_shortcuts.pack(side=tk.LEFT, padx=(0, 6))

            self.startup_var = tk.BooleanVar(value=is_startup_enabled())
            chk_startup = tk.Checkbutton(
                footer_frame,
                text="🚀 Run at Startup",
                variable=self.startup_var,
                bg="#0f172a",
                fg="#94a3b8",
                selectcolor="#1e293b",
                activebackground="#0f172a",
                activeforeground="#f8fafc",
                font=(self.sans_font, 8),
                command=self._toggle_startup,
            )
            chk_startup.pack(side=tk.LEFT, padx=(0, 6))

        self.minimize_on_close_var = tk.BooleanVar(value=True)
        chk_close_tray = tk.Checkbutton(
            footer_frame,
            text="Minimize to System Tray on close",
            variable=self.minimize_on_close_var,
            bg="#0f172a",
            fg="#94a3b8",
            selectcolor="#1e293b",
            activebackground="#0f172a",
            activeforeground="#f8fafc",
            font=(self.sans_font, 8),
        )
        chk_close_tray.pack(side=tk.LEFT)

        btn_min_tray = tk.Button(
            footer_frame,
            text="⬇️ Minimize to Tray",
            font=(self.sans_font, 8),
            bg="#1e293b",
            fg="#94a3b8",
            activebackground="#334155",
            relief="flat",
            padx=8,
            pady=3,
            cursor="hand2",
            command=self.minimize_to_tray,
        )
        btn_min_tray.pack(side=tk.RIGHT)

        lbl_github = tk.Label(
            footer_frame,
            text="GitHub",
            font=(self.sans_font, 8),
            bg="#0f172a",
            fg="#475569",
            cursor="hand2",
        )
        lbl_github.pack(side=tk.RIGHT, padx=(0, 10))
        lbl_github.bind("<Button-1>", lambda e: webbrowser.open("https://github.com/techguyowen/vox-stream"))
        lbl_github.bind("<Enter>", lambda e: lbl_github.config(fg="#94a3b8"))
        lbl_github.bind("<Leave>", lambda e: lbl_github.config(fg="#475569"))

    def copy_display_url(self, event=None):
        """Copy the LAN Stage Display URL to system clipboard and flash confirmation."""
        url = getattr(self, "current_display_url", "http://localhost:8765/display")
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(url)
            self._is_url_copied_flashing = True
            if hasattr(self, "lbl_url"):
                self.lbl_url.config(text="✓ Copied Display URL!", fg="#10b981")
            if hasattr(self, "btn_copy_url"):
                self.btn_copy_url.config(text="✓ Copied!", fg="#10b981")
            self.root.after(1500, self._restore_display_url)
        except Exception as e:
            logger.debug(f"Failed to copy display URL to clipboard: {e}")

    def _restore_display_url(self):
        """Restore display URL label and copy button text after confirmation flash."""
        self._is_url_copied_flashing = False
        if hasattr(self, "lbl_url"):
            self.lbl_url.config(text=self.current_display_url, fg="#38bdf8")
        if hasattr(self, "btn_copy_url"):
            self.btn_copy_url.config(text="📋 Copy Link", fg="#38bdf8")

    def _init_system_tray(self):
        """Set up system tray icon via pystray if available."""
        if not HAS_PYSTRAY:
            return

        from .tray import get_tray_icon_image
        img = get_tray_icon_image(status=self.status_state)
        if not img:
            return

        def _open_dash(icon, item):
            self._open_browser("/dashboard")

        def _restore(icon, item):
            self.root.after(0, self.restore_from_tray)

        def _toggle_p(icon, item):
            self.root.after(0, self._toggle_pause)

        def _quit_app(icon, item):
            self.root.after(0, self._exit_application)

        menu = pystray.Menu(
            pystray.MenuItem("🎙️ Open VoxStream Controller", _restore, default=True),
            pystray.MenuItem("🌐 Open Dashboard", _open_dash),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda text: "▶️ Resume Captions" if self.is_paused else "⏸️ Pause Captions", _toggle_p),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🚪 Exit Completely", _quit_app),
        )

        try:
            self._tray_icon = pystray.Icon("VoxStreamLauncher", img, "VoxStream Control Center", menu)
            self._tray_thread = threading.Thread(target=self._tray_icon.run, daemon=True)
            self._tray_thread.start()
        except Exception as e:
            logger.debug(f"Could not initialize system tray: {e}")

    def _create_shortcuts_action(self):
        """Create OS Desktop, Start Menu / LaunchAgent, and Startup shortcuts."""
        res = create_os_shortcuts(self.root_dir, include_startup=True)
        if hasattr(self, "startup_var"):
            self.startup_var.set(is_startup_enabled())
        if res.get("success"):
            if sys.platform == "win32":
                messagebox.showinfo(
                    "VoxStream Shortcuts Created",
                    "VoxStream shortcuts have been created on your Desktop, Start Menu, and Startup folder!\n\n"
                    "📌 To Pin to Taskbar:\n"
                    "1. Click the Windows Start button\n"
                    "2. Type 'VoxStream'\n"
                    "3. Right-click 'VoxStream' and select 'Pin to taskbar'!",
                )
            else:
                messagebox.showinfo(
                    "VoxStream Shortcuts Created",
                    "VoxStream desktop launcher (VoxStream.command) has been created on your Desktop!",
                )
        else:
            messagebox.showerror(
                "Shortcut Creation Error",
                f"Failed to create shortcuts:\n{res.get('error')}",
            )

    def _toggle_startup(self):
        """Toggle system Startup configuration (Windows Startup folder or macOS LaunchAgent)."""
        enabled = self.startup_var.get()
        res = set_startup_enabled(enabled, self.root_dir)
        if not res.get("success"):
            self.startup_var.set(not enabled)
            messagebox.showerror(
                "Startup Error",
                f"Failed to update startup configuration:\n{res.get('error')}",
            )
        else:
            status_msg = "enabled" if enabled else "disabled"
            platform_name = "Windows login" if sys.platform == "win32" else "macOS user login"
            messagebox.showinfo(
                "Startup Updated",
                f"VoxStream auto-boot at {platform_name} has been {status_msg}.",
            )

    _toggle_windows_startup = _toggle_startup

    def start_backend(self):
        """Start the backend process."""
        self._on_backend_status_change("STARTING")
        self.btn_start_stop.config(text="⏳ Starting...", state=tk.DISABLED)
        # Pass --no-tray to child so the launcher handles the tray
        t = threading.Thread(target=lambda: self.backend.start(extra_args=["--no-tray"]), daemon=True)
        t.start()

    def stop_backend(self):
        """Stop the backend process."""
        self.btn_start_stop.config(text="⏳ Stopping...", state=tk.DISABLED)
        t = threading.Thread(target=self.backend.stop, daemon=True)
        t.start()

    def _toggle_start_stop(self):
        if self.backend.is_running:
            self.stop_backend()
        else:
            self.start_backend()

    def _toggle_pause(self):
        """Toggle transcription pause/resume via HTTP API."""
        def _call():
            try:
                req = urllib.request.Request("http://127.0.0.1:8765/api/control/toggle", method="POST")
                urllib.request.urlopen(req, timeout=1.5)
            except Exception as e:
                logger.debug(f"Toggle pause request error: {e}")
        threading.Thread(target=_call, daemon=True).start()

    def _restart_engine(self):
        """Restart engine via HTTP API or process recycle."""
        self.btn_restart.config(state=tk.DISABLED)
        threading.Thread(target=self.backend.restart, daemon=True).start()

    def _open_browser(self, path: str):
        """Open web URL in default browser."""
        url = f"http://127.0.0.1:8765{path}"
        try:
            webbrowser.open(url)
        except Exception as e:
            logger.error(f"Failed to open browser URL: {e}")

    def _clear_logs(self):
        self.log_text.delete("1.0", tk.END)

    def _on_backend_log(self, line: str):
        self.log_queue.put(line)

    def _drain_logs(self):
        """Drain queued lifecycle updates and log lines on the UI thread."""
        try:
            while not self._lifecycle_queue.empty():
                state = self._lifecycle_queue.get_nowait()
                self._update_ui_state(state)
        except Exception:
            pass

        lines = []
        try:
            while not self.log_queue.empty():
                lines.append(self.log_queue.get_nowait())
        except Exception:
            pass

        if lines:
            self.log_text.insert(tk.END, "\n".join(lines) + "\n")
            if self.auto_scroll_var.get():
                self.log_text.see(tk.END)

        self.root.after(100, self._drain_logs)

    def _on_backend_status_change(self, state: str):
        """Callback from backend manager when process lifecycle changes.
        
        Enqueues state for thread-safe UI update on the Tk main thread.
        """
        self._lifecycle_queue.put(state)

    def _update_ui_state(self, state: str):
        """Update buttons, badges, and tray icon based on process state."""
        self.status_state = state
        if getattr(self, "_tray_icon", None):
            try:
                from .tray import get_tray_icon_image
                new_img = get_tray_icon_image(status=state)
                if new_img:
                    self._tray_icon.icon = new_img
            except Exception as e:
                logger.debug(f"Failed to update launcher tray icon status dot: {e}")

        if state == "RUNNING":
            self.status_badge.config(text="● RUNNING", bg="#059669", fg="#ffffff")
            self.btn_start_stop.config(text="⏹ Stop Backend", bg="#e11d48", activebackground="#f43f5e", state=tk.NORMAL)
            self.btn_pause.config(state=tk.NORMAL, text="⏸ Pause Captions" if not self.is_paused else "▶️ Resume Captions")
            self.btn_restart.config(state=tk.NORMAL)
        elif state == "STARTING":
            self.status_badge.config(text="● STARTING", bg="#d97706", fg="#ffffff")
            self.btn_start_stop.config(text="⏳ Starting...", state=tk.DISABLED)
            self.btn_pause.config(state=tk.DISABLED)
            self.btn_restart.config(state=tk.DISABLED)
        elif state == "PAUSED":
            self.status_badge.config(text="● PAUSED", bg="#ea580c", fg="#ffffff")
            self.btn_start_stop.config(text="⏹ Stop Backend", bg="#e11d48", activebackground="#f43f5e", state=tk.NORMAL)
            self.btn_pause.config(state=tk.NORMAL, text="▶️ Resume Captions")
            self.btn_restart.config(state=tk.NORMAL)
        elif state == "ERROR":
            self.status_badge.config(text="● ERROR", bg="#e11d48", fg="#ffffff")
            self.btn_start_stop.config(text="▶ Start Backend", bg="#059669", activebackground="#10b981", state=tk.NORMAL)
            self.btn_pause.config(state=tk.DISABLED)
            self.btn_restart.config(state=tk.DISABLED)
        else:  # STOPPED
            self.status_badge.config(text="● STOPPED", bg="#334155", fg="#f8fafc")
            self.btn_start_stop.config(text="▶ Start Backend", bg="#059669", activebackground="#10b981", state=tk.NORMAL)
            self.btn_pause.config(state=tk.DISABLED)
            self.btn_restart.config(state=tk.DISABLED)
            self.vu_bar["value"] = 0

    def _poll_backend_status(self):
        """Poll backend /api/status endpoint to update telemetry cards.

        The blocking HTTP fetch runs on a daemon worker thread and hands its
        result back through a queue. All Tk widget updates happen here on the
        UI thread, so the GUI never blocks on network I/O and no Tk calls are
        made from background threads (Tkinter is not thread-safe).
        """
        def _fetch():
            if not self.backend.is_running:
                return None
            try:
                req = urllib.request.Request("http://127.0.0.1:8765/api/status")
                with urllib.request.urlopen(req, timeout=1.2) as resp:
                    if resp.status == 200:
                        return json.loads(resp.read().decode("utf-8"))
            except Exception:
                return None
            return None

        def _worker():
            try:
                self._status_queue.put(_fetch())
            finally:
                self._status_fetch_inflight = False

        # Dispatch one fetch per tick; skip while a previous fetch is in flight.
        if self.backend.is_running and not self._status_fetch_inflight:
            self._status_fetch_inflight = True
            threading.Thread(target=_worker, daemon=True, name="BackendStatusPoller").start()

        # Drain completed fetch results (latest wins) on the UI thread.
        data: Optional[Dict[str, Any]] = None
        has_data = False
        try:
            while True:
                data = self._status_queue.get_nowait()
                has_data = True
        except queue.Empty:
            pass

        try:
            if has_data and data:
                self.last_status_data = data
                is_running = data.get("is_running", True)
                self.is_paused = not is_running

                # Engine
                eng_name = data.get("engine_name") or data.get("engine") or "Active"
                mod_detail = data.get("model_detail", "")
                self.lbl_engine.config(text=f"{eng_name}" if not mod_detail else f"{eng_name} ({mod_detail})")

                # Fallback alert
                fb_active = bool(data.get("fallback_active", False))
                fb_eng = data.get("fallback_engine", "vosk")
                if fb_active:
                    self.lbl_fallback.config(text=f"⚠️ Offline Fallback Active ({fb_eng})", foreground="#f59e0b")
                else:
                    self.lbl_fallback.config(text=f"Standby: {fb_eng.upper()} Offline Fallback Ready", foreground="#94a3b8")

                prev_fb_active = getattr(self, "_last_fallback_active", None)
                if prev_fb_active is not None and fb_active != prev_fb_active:
                    try:
                        from .hardware import send_os_notification
                        if fb_active:
                            send_os_notification(
                                "VoxStream Emergency Failover",
                                f"Primary engine offline. Operating on fallback ({fb_eng.upper()}).",
                                tray_icon=self._tray_icon,
                            )
                        else:
                            send_os_notification(
                                "VoxStream Engine Restored",
                                "Primary engine restored from fallback.",
                                tray_icon=self._tray_icon,
                            )
                    except Exception as ne:
                        logger.debug(f"Launcher failover notification error: {ne}")
                self._last_fallback_active = fb_active

                # Audio
                dev_name = data.get("active_device_name", "Default Microphone")
                self.lbl_audio_device.config(text=dev_name[:28] + ("..." if len(dev_name) > 28 else ""))

                # VU Level dB (-100 to 0) -> 0 to 100%
                db = float(data.get("audio_level_db", -100.0))
                meter_pct = max(0.0, min(100.0, (db + 60.0) * (100.0 / 60.0))) if db > -60.0 else 0.0
                self.vu_bar["value"] = meter_pct

                # LAN Stage Display URL
                status_display_url = data.get("display_url")
                if not status_display_url and data.get("lan_ip"):
                    backend_ip = data.get("lan_ip")
                    if backend_ip in ("127.0.0.1", "0.0.0.0"):
                        backend_ip = "localhost"
                    status_display_url = f"http://{backend_ip}:{data.get('port', 8765)}/display"
                if status_display_url:
                    self.current_display_url = status_display_url
                    if not getattr(self, "_is_url_copied_flashing", False) and hasattr(self, "lbl_url"):
                        self.lbl_url.config(text=self.current_display_url)

                # Clients
                client_count = data.get("caption_clients", 0)
                self.lbl_clients.config(text=f"{client_count} Web Overlay / Stage Clients")

                if self.status_state not in ("STARTING", "ERROR"):
                    self._update_ui_state("PAUSED" if self.is_paused else "RUNNING")
        except Exception as e:
            logger.debug(f"Backend status UI update error: {e}")
        finally:
            self.root.after(1500, self._poll_backend_status)

    def minimize_to_tray(self):
        """Hide window into system tray."""
        self.root.withdraw()
        if self._tray_icon:
            try:
                self._tray_icon.title = f"VoxStream - {'Live' if self.backend.is_running else 'Stopped'}"
            except Exception:
                pass

    def restore_from_tray(self):
        """Restore window from system tray."""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _on_window_close(self):
        """Handle window close event."""
        if self.minimize_on_close_var.get() and HAS_PYSTRAY:
            self.minimize_to_tray()
        else:
            self._exit_application()

    def _exit_application(self):
        """Completely shut down backend and exit launcher."""
        if self.backend.is_running:
            self.backend.stop()

        if self._tray_icon:
            try:
                self._tray_icon.stop()
            except Exception:
                pass

        release_single_instance_lock()
        self.root.destroy()
        sys.exit(0)


def main():
    """CLI entrypoint for standalone launcher."""
    parser = argparse.ArgumentParser(description="VoxStream Standalone Launcher & Control Center")
    parser.add_argument("--no-autostart", action="store_true", help="Do not start backend automatically on launcher open")
    parser.add_argument("--minimized", action="store_true", help="Start launcher minimized to system tray")
    parser.add_argument("--create-shortcuts", action="store_true", help="Generate Desktop, Start Menu / LaunchAgent, and Startup shortcuts and exit")
    parser.add_argument("--remove-shortcuts", action="store_true", help="Remove Desktop, Start Menu, and Startup shortcuts and exit")
    parser.add_argument("--enable-startup", action="store_true", help="Enable auto-boot at user logon and exit")
    parser.add_argument("--disable-startup", action="store_true", help="Disable auto-boot at user logon and exit")
    args = parser.parse_args()

    if args.create_shortcuts:
        res = create_os_shortcuts()
        if res.get("success"):
            print("✅ Shortcuts successfully created!")
            sys.exit(0)
        else:
            print(f"❌ Error creating shortcuts: {res.get('error')}")
            sys.exit(1)

    if args.remove_shortcuts:
        res = remove_windows_shortcuts()
        if res.get("success"):
            print("✅ Shortcuts successfully removed!")
            sys.exit(0)
        else:
            print(f"❌ Error removing shortcuts: {res.get('error')}")
            sys.exit(1)

    if args.enable_startup:
        res = set_startup_enabled(True)
        if res.get("success"):
            print("✅ Startup shortcut successfully enabled!")
            sys.exit(0)
        else:
            print(f"❌ Error enabling startup shortcut: {res.get('error')}")
            sys.exit(1)

    if args.disable_startup:
        res = set_startup_enabled(False)
        if res.get("success"):
            print("✅ Windows startup shortcut successfully disabled!")
            sys.exit(0)
        else:
            print(f"❌ Error disabling startup shortcut: {res.get('error')}")
            sys.exit(1)

    if not HAS_TKINTER:
        print("ERROR: Tkinter is not installed on this Python environment.")
        print("Please install python3-tk or run run_captioner.bat instead.")
        sys.exit(1)

    if not acquire_single_instance_lock():
        print("VoxStream Launcher is already running. Focusing existing window...")
        bring_existing_launcher_to_front()
        sys.exit(0)

    root = tk.Tk()
    app = VoxStreamLauncherGUI(
        root=root,
        autostart=not args.no_autostart,
        start_minimized=args.minimized,
    )
    root.mainloop()


if __name__ == "__main__":
    main()
