"""GitHub Auto-Update and In-Place Version Manager for VoxStream."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import re
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from .version import (
    VERSION,
    compare_versions,
    get_version_bump_type,
    is_version_newer,
    parse_version,
)

logger = logging.getLogger("obs_captioner.updater")

GITHUB_REPO = "techguyowen/vox-stream"
GITHUB_BRANCH = "main"
GITHUB_RAW_VERSION = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/version.json"
GITHUB_RAW_VERSION_PY = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/obs_captioner/version.py"
GITHUB_API_COMMITS = f"https://api.github.com/repos/{GITHUB_REPO}/commits/{GITHUB_BRANCH}"
GITHUB_API_RELEASES = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_API_TAGS = f"https://api.github.com/repos/{GITHUB_REPO}/tags"
GITHUB_ZIP_URL = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/{GITHUB_BRANCH}.zip"

APP_ROOT = Path(__file__).resolve().parent.parent


class UpdateManager:
    """Manages update checking against GitHub, in-place upgrades, and restart triggering."""

    def __init__(self, app_root: Optional[Path] = None, on_restart_requested: Optional[Callable[[], None]] = None):
        self.app_root = app_root or APP_ROOT
        self.on_restart_requested = on_restart_requested
        self._cache_ttl = 600.0  # 10 minutes cache to avoid GitHub API rate limits
        self._last_checked_time = 0.0
        self._cached_status: Optional[Dict[str, Any]] = None
        self._is_updating = False
        self._update_lock: Optional[asyncio.Lock] = None

    @property
    def update_lock(self) -> asyncio.Lock:
        if self._update_lock is None:
            self._update_lock = asyncio.Lock()
        return self._update_lock

    def is_git_repo(self) -> bool:
        """Check whether current installation is a Git clone and git binary is present."""
        git_dir = self.app_root / ".git"
        if not git_dir.exists():
            return False
        return shutil.which("git") is not None

    def get_local_commit(self) -> str:
        """Return the current local Git commit SHA or a fallback placeholder."""
        if self.is_git_repo():
            try:
                res = subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"],
                    cwd=str(self.app_root),
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if res.returncode == 0 and res.stdout.strip():
                    return res.stdout.strip()
            except Exception as e:
                logger.debug(f"Failed to read git commit: {e}")
        return "unknown"

    def get_local_full_commit(self) -> str:
        """Return full 40-character local commit SHA."""
        if self.is_git_repo():
            try:
                res = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=str(self.app_root),
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if res.returncode == 0 and res.stdout.strip():
                    return res.stdout.strip()
            except Exception:
                pass
        return "unknown"

    async def check_update(self, force: bool = False) -> Dict[str, Any]:
        """Check GitHub for newer commits or releases. Cached for 10 minutes unless force=True."""
        now = time.time()
        if not force and self._cached_status and (now - self._last_checked_time < self._cache_ttl):
            return self._cached_status

        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(None, self._sync_check_update)
        self._cached_status = res
        self._last_checked_time = now
        return res

    def _sync_check_update(self) -> Dict[str, Any]:
        """Synchronous update check logic executed in worker thread.

        Performs semantic version comparison against GitHub raw repo / releases,
        plus Git commit comparison when git is active.
        """
        local_short = self.get_local_commit()
        local_full = self.get_local_full_commit()
        is_git = self.is_git_repo()
        local_version = VERSION

        # 1. Look up remote semantic version from GitHub
        remote_version = None
        # 1a. Try raw version.json (fast, light, not rate-limited)
        try:
            req = urllib.request.Request(
                GITHUB_RAW_VERSION,
                headers={"User-Agent": f"VoxStream-Captioner/{local_version}"},
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                if resp.status == 200:
                    v_data = json.loads(resp.read().decode("utf-8"))
                    remote_version = str(v_data.get("version", "")).strip() or None
        except Exception as e:
            logger.debug(f"GitHub raw version.json query failed: {e}")

        # 1b. Fallback to raw obs_captioner/version.py
        if not remote_version:
            try:
                req = urllib.request.Request(
                    GITHUB_RAW_VERSION_PY,
                    headers={"User-Agent": f"VoxStream-Captioner/{local_version}"},
                )
                with urllib.request.urlopen(req, timeout=6) as resp:
                    if resp.status == 200:
                        text = resp.read().decode("utf-8")
                        m = re.search(r'VERSION\s*=\s*"([^"]+)"', text)
                        if m:
                            remote_version = m.group(1).strip()
            except Exception as e:
                logger.debug(f"GitHub raw version.py query failed: {e}")

        # 1c. Fallback to GitHub Releases / Tags API
        if not remote_version:
            try:
                req = urllib.request.Request(
                    GITHUB_API_RELEASES,
                    headers={
                        "User-Agent": f"VoxStream-Captioner/{local_version}",
                        "Accept": "application/vnd.github.v3+json",
                    },
                )
                with urllib.request.urlopen(req, timeout=6) as resp:
                    if resp.status == 200:
                        rel_data = json.loads(resp.read().decode("utf-8"))
                        tag = str(rel_data.get("tag_name", "")).strip()
                        if tag and tag.lower() != "latest":
                            remote_version = tag.lstrip("vV")
            except Exception as e:
                logger.debug(f"GitHub Releases API query failed: {e}")

        # 2. Query remote commit via git ls-remote if git is available
        remote_full = None
        if is_git:
            try:
                res = subprocess.run(
                    ["git", "ls-remote", "origin", f"refs/heads/{GITHUB_BRANCH}"],
                    cwd=str(self.app_root),
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
                if res.returncode == 0 and res.stdout.strip():
                    remote_full = res.stdout.strip().split()[0]
            except Exception as e:
                logger.debug(f"git ls-remote check failed ({e}). Falling back to GitHub REST API...")

        # 3. Query GitHub Commits REST API (fetches commit metadata, message, date)
        api_data = None
        try:
            req = urllib.request.Request(
                GITHUB_API_COMMITS,
                headers={
                    "User-Agent": f"VoxStream-Captioner/{local_version}",
                    "Accept": "application/vnd.github.v3+json",
                },
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status == 200:
                    api_data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.debug(f"GitHub API commits query error: {e}")

        latest_sha = ""
        message = ""
        author = ""
        date_str = ""
        if api_data:
            latest_sha = api_data.get("sha", "")
            commit_info = api_data.get("commit", {})
            message = commit_info.get("message", "").split("\n")[0]
            author = commit_info.get("author", {}).get("name", "techguyowen")
            date_str = commit_info.get("author", {}).get("date", "")
        elif remote_full:
            latest_sha = remote_full
            message = "New update available on GitHub"

        # 4. Evaluate whether update is available
        latest_version = remote_version or local_version
        update_available = False
        update_type = "none"

        # Check semantic version precedence first (Major / Medium / Minor)
        if remote_version and is_version_newer(remote_version, local_version):
            update_available = True
            update_type = get_version_bump_type(remote_version, local_version)
        elif is_git and latest_sha:
            # Even if semantic version is identical (e.g. unreleased commits on main)
            if local_full != "unknown" and local_full.lower() != latest_sha.lower():
                update_available = True
                update_type = "commit"
            elif local_short != "unknown" and not latest_sha.startswith(local_short):
                update_available = True
                update_type = "commit"

        result: Dict[str, Any] = {
            "current_version": local_version,
            "latest_version": latest_version,
            "current_commit": local_short,
            "current_commit_full": local_full,
            "is_git": is_git,
            "update_available": update_available,
            "update_type": update_type,
            "latest_commit": latest_sha[:7] if latest_sha else local_short,
            "latest_commit_full": latest_sha or local_full,
            "commit_message": message or ("New update available on GitHub" if update_available else ""),
            "commit_date": date_str,
            "commit_author": author,
            "error": None,
            "last_checked": time.time(),
        }

        return result

    async def apply_update(self, progress_cb: Optional[Callable[[str], None]] = None) -> Tuple[bool, str]:
        """Download latest updates, sync dependencies, and schedule restart."""
        async with self.update_lock:
            if self._is_updating:
                return False, "An update is already in progress."

            self._is_updating = True
            loop = asyncio.get_event_loop()
            try:
                success, msg = await loop.run_in_executor(None, self._sync_apply_update, progress_cb)
                if success:
                    # Invalidate cache
                    self._cached_status = None
                    self._last_checked_time = 0.0

                    # Trigger restart handover
                    if self.on_restart_requested:
                        logger.info("Triggering application restart to apply update...")
                        # Run callback after brief pause so API response completes
                        loop.call_later(0.5, self.on_restart_requested)
                return success, msg
            finally:
                self._is_updating = False

    def _sync_apply_update(self, progress_cb: Optional[Callable[[str], None]] = None) -> Tuple[bool, str]:
        """Perform the file updates synchronously."""
        is_git = self.is_git_repo()

        if is_git:
            success, msg = self._apply_update_git(progress_cb)
            if success:
                return True, msg
            logger.warning(f"Git update failed ({msg}). Falling back to clean archive download...")
            if progress_cb:
                progress_cb("⚠️ Git update failed. Falling back to clean GitHub archive download...")
            return self._apply_update_zip(progress_cb)
        else:
            return self._apply_update_zip(progress_cb)

    def _apply_update_git(self, progress_cb: Optional[Callable[[str], None]] = None) -> Tuple[bool, str]:
        """Update via Git pull with safe stash and dependency check."""
        try:
            if progress_cb:
                progress_cb("⬇️ Downloading latest updates from GitHub (git pull)...")
            logger.info("Executing git pull origin main...")

            # 1. Stash any accidental local modifications and untracked files
            subprocess.run(
                ["git", "stash", "--include-untracked"],
                cwd=str(self.app_root),
                capture_output=True,
                text=True,
                timeout=15,
            )

            # 2. Pull from origin main
            pull_res = subprocess.run(
                ["git", "pull", "--ff-only", "origin", GITHUB_BRANCH],
                cwd=str(self.app_root),
                capture_output=True,
                text=True,
                timeout=30,
            )

            # If fast-forward failed, perform fetch and hard reset to match origin
            if pull_res.returncode != 0:
                logger.warning(f"git pull --ff-only failed ({pull_res.stderr.strip()}). Attempting clean fetch and reset...")
                subprocess.run(
                    ["git", "fetch", "--all"],
                    cwd=str(self.app_root),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                reset_res = subprocess.run(
                    ["git", "reset", "--hard", f"origin/{GITHUB_BRANCH}"],
                    cwd=str(self.app_root),
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if reset_res.returncode != 0:
                    return False, f"Git update failed: {reset_res.stderr.strip()}"

            # 3. Update Python dependencies
            req_file = self.app_root / "requirements.txt"
            if req_file.exists():
                if progress_cb:
                    progress_cb("📦 Checking and updating Python dependencies...")
                logger.info("Checking Python package dependencies...")
                pip_res = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-r", str(req_file)],
                    cwd=str(self.app_root),
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
                if pip_res.returncode != 0:
                    logger.warning(f"pip install completed with warnings: {pip_res.stderr.strip()[:200]}")

            # Ensure Windows batch files maintain CRLF line endings
            self._ensure_windows_batch_crlf()

            if progress_cb:
                progress_cb("🎉 Update applied successfully! Restarting VoxStream...")
            logger.info("Git update applied successfully.")
            return True, "VoxStream updated successfully to the latest version!"

        except Exception as e:
            logger.error(f"Error applying Git update: {e}", exc_info=True)
            return False, f"Failed to apply update: {e}"

    def _apply_update_zip(self, progress_cb: Optional[Callable[[str], None]] = None) -> Tuple[bool, str]:
        """Update via GitHub Zipball download (fallback for non-git environments)."""
        import tempfile
        import zipfile

        try:
            if progress_cb:
                progress_cb("⬇️ Downloading latest release archive from GitHub...")
            logger.info(f"Downloading release archive from {GITHUB_ZIP_URL}...")

            with tempfile.TemporaryDirectory() as tmp_dir:
                zip_path = Path(tmp_dir) / "update.zip"
                extract_path = Path(tmp_dir) / "extracted"

                # Download zip
                req = urllib.request.Request(
                    GITHUB_ZIP_URL,
                    headers={"User-Agent": f"VoxStream-Captioner/{VERSION}"},
                )
                with urllib.request.urlopen(req, timeout=60) as resp, open(zip_path, "wb") as f:
                    shutil.copyfileobj(resp, f)

                if progress_cb:
                    progress_cb("📂 Extracting files...")
                with zipfile.ZipFile(zip_path, "r") as zf:
                    zf.extractall(extract_path)

                # GitHub zips have a top-level root folder e.g. vox-stream-main/
                subdirs = [d for d in extract_path.iterdir() if d.is_dir()]
                source_dir = subdirs[0] if subdirs else extract_path

                # Protected files and directories that must NEVER be overwritten
                PROTECTED_NAMES = {
                    "config.json",
                    "google_credentials.json",
                    ".venv",
                    "venv",
                    ".git",
                    "logs",
                    "data",
                    ".env",
                    "models",
                    "custom_models",
                    ".user_uploaded",
                }

                def is_protected_item(name: str) -> bool:
                    if name in PROTECTED_NAMES:
                        return True
                    lower = name.lower()
                    if lower.startswith(".env") or lower.endswith(".key") or lower.endswith(".pem"):
                        return True
                    if lower.startswith("config.") and lower.endswith(".json"):
                        return True
                    if "credentials" in lower and lower.endswith(".json"):
                        return True
                    return False

                if progress_cb:
                    progress_cb("🔄 Updating application files...")
                for item in source_dir.iterdir():
                    if is_protected_item(item.name):
                        continue
                    dest = self.app_root / item.name
                    if item.is_dir():
                        shutil.copytree(
                            item,
                            dest,
                            dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.tmp"),
                        )
                    else:
                        try:
                            shutil.copy2(item, dest)
                        except Exception as copy_err:
                            logger.warning(f"Could not overwrite file {item.name}: {copy_err}")

                # Update dependencies
                req_file = self.app_root / "requirements.txt"
                if req_file.exists():
                    if progress_cb:
                        progress_cb("📦 Verifying Python dependencies...")
                    subprocess.run(
                        [sys.executable, "-m", "pip", "install", "-r", str(req_file)],
                        cwd=str(self.app_root),
                        capture_output=True,
                        text=True,
                        timeout=90,
                    )

            # Ensure Windows batch files maintain CRLF line endings
            self._ensure_windows_batch_crlf()

            if progress_cb:
                progress_cb("🎉 Update applied successfully! Restarting VoxStream...")
            logger.info("Zip update applied successfully.")
            return True, "VoxStream updated successfully from GitHub archive!"

        except Exception as e:
            logger.error(f"Error applying Zip update: {e}", exc_info=True)
            return False, f"Failed to download or apply update: {e}"

    def _ensure_windows_batch_crlf(self) -> None:
        """Normalize line endings of all .bat and .cmd files to CRLF for Windows cmd.exe compatibility."""
        try:
            for ext in ("*.bat", "*.cmd"):
                for script in self.app_root.glob(ext):
                    try:
                        raw = script.read_bytes()
                        normalized = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
                        if normalized != raw:
                            script.write_bytes(normalized)
                    except Exception as e:
                        logger.debug(f"Failed to normalize CRLF for {script.name}: {e}")
        except Exception as e:
            logger.debug(f"Batch script CRLF normalization skipped: {e}")

