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
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from . import __version__

logger = logging.getLogger("obs_captioner.updater")

GITHUB_REPO = "techguyowen/vox-stream"
GITHUB_BRANCH = "main"
GITHUB_API_COMMITS = f"https://api.github.com/repos/{GITHUB_REPO}/commits/{GITHUB_BRANCH}"
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
        self._update_lock = asyncio.Lock()

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
        """Synchronous update check logic executed in worker thread."""
        local_short = self.get_local_commit()
        local_full = self.get_local_full_commit()
        is_git = self.is_git_repo()

        result: Dict[str, Any] = {
            "current_version": __version__,
            "current_commit": local_short,
            "current_commit_full": local_full,
            "is_git": is_git,
            "update_available": False,
            "latest_commit": local_short,
            "latest_commit_full": local_full,
            "commit_message": "",
            "commit_date": "",
            "commit_author": "",
            "error": None,
            "last_checked": time.time(),
        }

        # 1. Query remote commit via git ls-remote if git is available
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

        # 2. Query GitHub REST API (fetches commit metadata, message, date)
        api_data = None
        try:
            req = urllib.request.Request(
                GITHUB_API_COMMITS,
                headers={
                    "User-Agent": f"VoxStream-Captioner/{__version__}",
                    "Accept": "application/vnd.github.v3+json",
                },
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status == 200:
                    api_data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.debug(f"GitHub API query error: {e}")

        if api_data:
            latest_sha = api_data.get("sha", "")
            commit_info = api_data.get("commit", {})
            message = commit_info.get("message", "").split("\n")[0]
            author = commit_info.get("author", {}).get("name", "techguyowen")
            date_str = commit_info.get("author", {}).get("date", "")

            result["latest_commit_full"] = latest_sha
            result["latest_commit"] = latest_sha[:7] if latest_sha else "unknown"
            result["commit_message"] = message
            result["commit_date"] = date_str
            result["commit_author"] = author

            if local_full != "unknown" and latest_sha:
                result["update_available"] = (local_full.lower() != latest_sha.lower())
            elif local_short != "unknown" and latest_sha:
                result["update_available"] = not latest_sha.startswith(local_short)
        elif remote_full:
            result["latest_commit_full"] = remote_full
            result["latest_commit"] = remote_full[:7]
            result["update_available"] = (local_full.lower() != remote_full.lower())
            result["commit_message"] = "New update available on GitHub"

        return result

    async def apply_update(self, progress_cb: Optional[Callable[[str], None]] = None) -> Tuple[bool, str]:
        """Download latest updates, sync dependencies, and schedule restart."""
        async with self._update_lock:
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
            return self._apply_update_git(progress_cb)
        else:
            return self._apply_update_zip(progress_cb)

    def _apply_update_git(self, progress_cb: Optional[Callable[[str], None]] = None) -> Tuple[bool, str]:
        """Update via Git pull with safe stash and dependency check."""
        try:
            if progress_cb:
                progress_cb("⬇️ Downloading latest updates from GitHub (git pull)...")
            logger.info("Executing git pull origin main...")

            # 1. Stash any accidental local modifications to tracked files
            subprocess.run(
                ["git", "stash"],
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
                    ["git", "fetch", "origin", GITHUB_BRANCH],
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
                    headers={"User-Agent": f"VoxStream-Captioner/{__version__}"},
                )
                with urllib.request.urlopen(req, timeout=30) as resp, open(zip_path, "wb") as f:
                    shutil.copyfileobj(resp, f)

                if progress_cb:
                    progress_cb("📂 Extracting files...")
                with zipfile.ZipFile(zip_path, "r") as zf:
                    zf.extractall(extract_path)

                # GitHub zips have a top-level root folder e.g. vox-stream-main/
                subdirs = [d for d in extract_path.iterdir() if d.is_dir()]
                source_dir = subdirs[0] if subdirs else extract_path

                # Protected files that must NEVER be overwritten
                PROTECTED_NAMES = {
                    "config.json",
                    "google_credentials.json",
                    ".venv",
                    "venv",
                    ".git",
                    "logs",
                    "data",
                    ".env",
                }

                if progress_cb:
                    progress_cb("🔄 Updating application files...")
                for item in source_dir.iterdir():
                    if item.name in PROTECTED_NAMES:
                        continue
                    dest = self.app_root / item.name
                    if item.is_dir():
                        if dest.exists():
                            shutil.rmtree(dest, ignore_errors=True)
                        shutil.copytree(item, dest)
                    else:
                        shutil.copy2(item, dest)

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

            if progress_cb:
                progress_cb("🎉 Update applied successfully! Restarting VoxStream...")
            logger.info("Zip update applied successfully.")
            return True, "VoxStream updated successfully from GitHub archive!"

        except Exception as e:
            logger.error(f"Error applying Zip update: {e}", exc_info=True)
            return False, f"Failed to download or apply update: {e}"
