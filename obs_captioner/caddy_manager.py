"""Caddy Reverse Proxy & Local SSL Manager for VoxStream.

Handles binary discovery, automated download, process supervision (caddy start/stop),
local Root CA certificate trust installation, and telemetry status.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

logger = logging.getLogger("obs_captioner.caddy")


def get_project_root() -> Path:
    """Return the absolute path to the VoxStream project root directory."""
    return Path(__file__).resolve().parent.parent


def get_bin_dir() -> Path:
    """Return the path to the project's local bin directory."""
    bin_dir = get_project_root() / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    return bin_dir


def find_caddy_binary(custom_path: str = "") -> Optional[Path]:
    """Find the Caddy executable on the system.

    Checks:
    1. custom_path (if configured)
    2. Local project bin directory (bin/caddy.exe or bin/caddy)
    3. System PATH via shutil.which('caddy')
    """
    if custom_path:
        p = Path(custom_path)
        if not p.is_absolute():
            p = get_project_root() / p
        if p.is_file() and os.access(str(p), os.X_OK):
            return p

    exe_name = "caddy.exe" if sys.platform == "win32" else "caddy"
    local_bin = get_bin_dir() / exe_name
    if local_bin.is_file() and os.access(str(local_bin), os.X_OK):
        return local_bin

    which_path = shutil.which("caddy") or shutil.which("caddy.exe")
    if which_path:
        p = Path(which_path)
        if p.is_file() and os.access(str(p), os.X_OK):
            return p

    return None


def get_caddy_root_ca_path() -> Optional[Path]:
    """Locate Caddy's auto-generated local root CA certificate."""
    candidates = []
    if sys.platform == "win32":
        app_data = os.environ.get("APPDATA")
        if app_data:
            candidates.append(Path(app_data) / "Caddy" / "pki" / "authorities" / "local" / "root.crt")
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(Path(local_app_data) / "caddy" / "pki" / "authorities" / "local" / "root.crt")
    elif sys.platform == "darwin":
        candidates.append(Path.home() / "Library" / "Application Support" / "Caddy" / "pki" / "authorities" / "local" / "root.crt")
    else:  # Linux and other UNIX
        xdg_data = os.environ.get("XDG_DATA_HOME")
        if xdg_data:
            candidates.append(Path(xdg_data) / "caddy" / "pki" / "authorities" / "local" / "root.crt")
        candidates.append(Path.home() / ".local" / "share" / "caddy" / "pki" / "authorities" / "local" / "root.crt")

    for c in candidates:
        if c.is_file():
            return c
    return None


def is_port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    """Test if a TCP port is currently open and listening."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError):
        return False


def is_caddy_running(port_http: int = 80, port_https: int = 443) -> bool:
    """Check if Caddy reverse proxy is actively listening."""
    return is_port_open(port_http) or is_port_open(port_https)


def download_caddy() -> Tuple[bool, str]:
    """Download the official Caddy binary for the current OS and CPU architecture into bin/."""
    bin_dir = get_bin_dir()
    exe_name = "caddy.exe" if sys.platform == "win32" else "caddy"
    target_path = bin_dir / exe_name

    system = platform.system().lower()
    machine = platform.machine().lower()

    # Map architecture to Caddy release asset naming
    if machine in ("x86_64", "amd64"):
        arch = "amd64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    elif machine.startswith("armv7"):
        arch = "armv7"
    else:
        return False, f"Unsupported CPU architecture: {machine}"

    if system == "windows":
        asset_name = f"caddy_windows_{arch}.zip"
    elif system == "darwin":
        asset_name = f"caddy_darwin_{arch}.tar.gz"
    elif system == "linux":
        asset_name = f"caddy_linux_{arch}.tar.gz"
    else:
        return False, f"Unsupported operating system: {system}"

    url = f"https://github.com/caddyserver/caddy/releases/latest/download/{asset_name}"
    archive_path = bin_dir / asset_name

    try:
        logger.info(f"Downloading Caddy from: {url}")
        headers = {"User-Agent": "VoxStream-Caddy-Installer"}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp, open(archive_path, "wb") as out_file:
            shutil.copyfileobj(resp, out_file)

        # Extract binary
        if asset_name.endswith(".zip"):
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extract(exe_name, path=bin_dir)
        elif asset_name.endswith(".tar.gz"):
            with tarfile.open(archive_path, "r:gz") as tf:
                member = tf.getmember(exe_name)
                tf.extract(member, path=bin_dir)

        # Ensure executable permissions
        target_path.chmod(0o755)

        # Clean up downloaded archive
        if archive_path.is_file():
            archive_path.unlink()

        logger.info(f"Caddy successfully installed to: {target_path}")
        return True, f"Caddy successfully downloaded to {target_path}"

    except Exception as e:
        logger.error(f"Failed to download Caddy: {e}")
        if archive_path.is_file():
            try:
                archive_path.unlink()
            except Exception:
                pass
        return False, f"Failed to download Caddy: {e}"


def trust_caddy_ca() -> Tuple[bool, str]:
    """Execute `caddy trust` to install the local Root CA into the operating system certificate store."""
    caddy_bin = find_caddy_binary()
    if not caddy_bin:
        return False, "Caddy binary not found"

    try:
        logger.info("Installing Caddy local Root CA into system trust store...")
        res = subprocess.run(
            [str(caddy_bin), "trust"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if res.returncode == 0:
            logger.info("Caddy local Root CA installed and trusted successfully.")
            return True, "Caddy Root CA trusted successfully"
        else:
            err = (res.stderr or res.stdout).strip()
            logger.warning(f"caddy trust returned code {res.returncode}: {err}")
            return False, f"caddy trust failed: {err}"
    except Exception as e:
        logger.warning(f"Error running caddy trust: {e}")
        return False, str(e)


def start_caddy(config_file: Optional[str] = None) -> Tuple[bool, str]:
    """Start Caddy reverse proxy in background using `caddy start`."""
    caddy_bin = find_caddy_binary()
    if not caddy_bin:
        return False, "Caddy binary not found. Please install or download Caddy first."

    caddyfile = Path(config_file) if config_file else get_project_root() / "Caddyfile"
    if not caddyfile.is_file():
        return False, f"Caddyfile not found at {caddyfile}"

    try:
        logger.info(f"Starting Caddy with configuration: {caddyfile}")
        cmd = [str(caddy_bin), "start", "--config", str(caddyfile)]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
        if res.returncode == 0:
            logger.info("Caddy reverse proxy started successfully.")
            return True, "Caddy started successfully"
        else:
            err = (res.stderr or res.stdout).strip()
            logger.error(f"Caddy start failed (code {res.returncode}): {err}")
            return False, f"Failed to start Caddy: {err}"
    except Exception as e:
        logger.error(f"Exception starting Caddy: {e}")
        return False, str(e)


def stop_caddy() -> Tuple[bool, str]:
    """Stop the running Caddy reverse proxy using `caddy stop`."""
    caddy_bin = find_caddy_binary()
    if not caddy_bin:
        return False, "Caddy binary not found"

    try:
        logger.info("Stopping Caddy reverse proxy...")
        cmd = [str(caddy_bin), "stop"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if res.returncode == 0:
            logger.info("Caddy stopped successfully.")
            return True, "Caddy stopped successfully"
        else:
            err = (res.stderr or res.stdout).strip()
            logger.warning(f"Caddy stop returned code {res.returncode}: {err}")
            return False, f"Caddy stop failed: {err}"
    except Exception as e:
        logger.warning(f"Exception stopping Caddy: {e}")
        return False, str(e)


def get_caddy_telemetry(
    target: Any = "127.0.0.1",
    port_http: int = 80,
    port_https: int = 443,
    ssl_enabled: bool = True,
) -> Dict[str, Any]:
    """Compile Caddy telemetry and active clean URLs. Target can be AppConfig or LAN IP string."""
    enabled = False
    lan_ip = "127.0.0.1"

    if hasattr(target, "caddy"):
        c_cfg = getattr(target, "caddy")
        enabled = getattr(c_cfg, "enabled", False)
        ssl_enabled = getattr(c_cfg, "ssl", True)
        port_http = getattr(c_cfg, "port_http", 80)
        port_https = getattr(c_cfg, "port_https", 443)
        from obs_captioner.hardware import get_local_ip
        lan_ip = get_local_ip()
    elif isinstance(target, str):
        lan_ip = target
        enabled = True

    bin_path = find_caddy_binary()
    running = is_caddy_running(port_http, port_https)
    ca_path = get_caddy_root_ca_path()

    clean_ip = lan_ip if lan_ip and lan_ip != "0.0.0.0" else "127.0.0.1"

    http_url = f"http://{clean_ip}" if port_http == 80 else f"http://{clean_ip}:{port_http}"
    https_url = f"https://{clean_ip}" if port_https == 443 else f"https://{clean_ip}:{port_https}"

    return {
        "enabled": enabled,
        "installed": bin_path is not None,
        "bin_path": str(bin_path) if bin_path else "",
        "running": running,
        "ssl": ssl_enabled,
        "ssl_enabled": ssl_enabled,
        "port_http": port_http,
        "port_https": port_https,
        "http_display_url": f"{http_url}/display",
        "https_display_url": f"{https_url}/display",
        "preferred_display_url": f"{https_url}/display" if (running and ssl_enabled) else (f"{http_url}/display" if running else f"http://{clean_ip}:8765/display"),
        "ca_installed": ca_path is not None,
        "ca_path": str(ca_path) if ca_path else "",
    }
