"""Windows Defender Firewall Configuration and Verification Utility for VoxStream.

Allows inbound TCP traffic on port 8765 (or custom overlay port) across all network profiles
(Domain, Private, Public) so stage displays, confidence monitors, and mobile web clients can connect.
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
from typing import Optional, Tuple

logger = logging.getLogger("obs_captioner.firewall")

DEFAULT_RULE_NAME = "VoxStream Overlay TCP 8765"
DEFAULT_PORT = 8765


def is_windows() -> bool:
    """Check if host OS is Windows."""
    return sys.platform == "win32"


def is_admin() -> bool:
    """Check whether the current process possesses Windows Administrator privileges."""
    if not is_windows():
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def check_firewall_rule(rule_name: str = DEFAULT_RULE_NAME, port: int = DEFAULT_PORT) -> bool:
    """Query netsh advfirewall to verify if the inbound rule exists and is enabled."""
    if not is_windows():
        return False

    try:
        cmd = ["netsh", "advfirewall", "firewall", "show", "rule", f"name={rule_name}"]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            out = proc.stdout.lower()
            if "enabled:" in out and "yes" in out:
                return True
            # Even if different language/locale, returncode 0 means rule matched
            return "no rules match" not in out
        return False
    except Exception as e:
        logger.debug(f"Failed to check firewall rule: {e}")
        return False


def add_firewall_rule(
    rule_name: str = DEFAULT_RULE_NAME,
    port: int = DEFAULT_PORT,
    elevate_if_needed: bool = True,
) -> Tuple[bool, str]:
    """Add inbound TCP firewall allow rule for the specified port across all network profiles.
    
    Returns (success: bool, message: str).
    """
    if not is_windows():
        return False, "Firewall rules are only applicable on Windows operating systems."

    # Direct netsh command string
    netsh_cmd = (
        f'netsh advfirewall firewall add rule '
        f'name="{rule_name}" dir=in action=allow protocol=TCP localport={port} profile=any description="Allows inbound connections to VoxStream overlay, stage display, and control dashboard"'
    )

    if is_admin():
        try:
            res = subprocess.run(netsh_cmd, shell=True, capture_output=True, text=True)
            if res.returncode == 0 or "ok." in res.stdout.lower():
                return True, f"Successfully created firewall rule '{rule_name}' for TCP port {port}."
            return False, f"netsh returned code {res.returncode}: {res.stderr or res.stdout}"
        except Exception as e:
            return False, f"Failed executing netsh: {e}"

    if not elevate_if_needed:
        return False, "Administrator privileges required to add firewall rules."

    # Request UAC elevation via PowerShell Start-Process
    ps_cmd = (
        f"Start-Process cmd.exe -ArgumentList '/c \"{netsh_cmd}\"' -Verb RunAs -Wait"
    )
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True)
        if proc.returncode == 0:
            if check_firewall_rule(rule_name, port):
                return True, f"Elevated firewall configuration succeeded for TCP port {port}."
            return False, "Elevated command executed, but rule verification could not confirm rule creation."
        return False, f"Elevation request failed or was cancelled by user (code {proc.returncode})."
    except Exception as e:
        return False, f"Failed launching elevation prompt: {e}"


def remove_firewall_rule(
    rule_name: str = DEFAULT_RULE_NAME,
    elevate_if_needed: bool = True,
) -> Tuple[bool, str]:
    """Remove the VoxStream inbound firewall rule.
    
    Returns (success: bool, message: str).
    """
    if not is_windows():
        return False, "Firewall rules are only applicable on Windows."

    del_cmd = f'netsh advfirewall firewall delete rule name="{rule_name}"'

    if is_admin():
        try:
            res = subprocess.run(del_cmd, shell=True, capture_output=True, text=True)
            if res.returncode == 0:
                return True, f"Successfully removed firewall rule '{rule_name}'."
            return False, f"netsh delete returned code {res.returncode}: {res.stderr or res.stdout}"
        except Exception as e:
            return False, f"Failed deleting firewall rule: {e}"

    if not elevate_if_needed:
        return False, "Administrator privileges required to remove firewall rules."

    ps_cmd = f"Start-Process cmd.exe -ArgumentList '/c \"{del_cmd}\"' -Verb RunAs -Wait"
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True)
        if proc.returncode == 0:
            return True, f"Firewall rule '{rule_name}' deleted."
        return False, f"Elevation request cancelled or failed (code {proc.returncode})."
    except Exception as e:
        return False, f"Failed launching elevation prompt: {e}"


def main():
    import argparse
    parser = argparse.ArgumentParser(description="VoxStream Windows Defender Firewall Utility")
    parser.add_argument("--add", action="store_true", help="Add inbound TCP rule for VoxStream")
    parser.add_argument("--remove", action="store_true", help="Remove inbound TCP rule for VoxStream")
    parser.add_argument("--check", action="store_true", help="Check if firewall rule currently exists")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port number (default: {DEFAULT_PORT})")
    parser.add_argument("--name", type=str, default=DEFAULT_RULE_NAME, help="Firewall rule name")

    args = parser.parse_args()

    if not is_windows():
        print("Notice: Windows Defender Firewall configuration is only applicable on Windows.")
        sys.exit(0)

    if args.add:
        print(f"Configuring Windows Defender Firewall for TCP port {args.port}...")
        ok, msg = add_firewall_rule(args.name, args.port, elevate_if_needed=True)
        print(("SUCCESS: " if ok else "ERROR: ") + msg)
        sys.exit(0 if ok else 1)

    elif args.remove:
        print(f"Removing firewall rule '{args.name}'...")
        ok, msg = remove_firewall_rule(args.name, elevate_if_needed=True)
        print(("SUCCESS: " if ok else "ERROR: ") + msg)
        sys.exit(0 if ok else 1)

    else:
        # Default action: check status
        active = check_firewall_rule(args.name, args.port)
        if active:
            print(f"STATUS: [OPEN] Firewall rule '{args.name}' is ACTIVE for TCP port {args.port}.")
            print("Confidence monitors, stage iPads, and mobile phones can connect over Wi-Fi.")
            sys.exit(0)
        else:
            print(f"STATUS: [BLOCKED/MISSING] No active inbound firewall rule found for '{args.name}' (Port {args.port}).")
            print("To open this port for network devices, run: setup_firewall.bat or python -m obs_captioner.firewall --add")
            sys.exit(1 if args.check else 0)


if __name__ == "__main__":
    main()
