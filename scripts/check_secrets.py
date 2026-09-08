#!/usr/bin/env python3
"""
VoxStream Secret Scanner & Leak Prevention Tool

Audits the git working tree, staged files, and repository contents
to ensure no API keys, tokens, or credential files are ever committed or shared to GitHub.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# High-confidence patterns for secrets that must NEVER appear in tracked files
SECRET_PATTERNS = [
    (re.compile(r"AIza[0-9A-Za-z_-]{35}"), "Google / Gemini API Key"),
    (re.compile(r"sk-[0-9a-zA-Z]{20,}"), "OpenAI API Key"),
    (re.compile(r"ghp_[0-9a-zA-Z]{36}"), "GitHub Personal Access Token"),
    (re.compile(r"bwa_[0-9a-zA-Z]{20,}"), "Bandwidth API Key"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS Access Key ID"),
    (re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"), "Private Key"),
    (re.compile(r"oauth:[0-9a-zA-Z]{25,}"), "Live Twitch OAuth Token"),
]

# Sensitive file patterns that must NEVER be tracked by git
FORBIDDEN_TRACKED_PATTERNS = [
    re.compile(r"^config\.json$"),
    re.compile(r"^config\..*\.json$"),
    re.compile(r"\.local\.json$"),
    re.compile(r"^.*credentials.*\.json$"),
    re.compile(r"^\.env.*$"),
    re.compile(r"^.*\.key$"),
    re.compile(r"^.*\.pem$"),
]

# Files allowed to contain documentation placeholders or regex definitions
SAFE_FILE_EXCLUSIONS = {
    Path("scripts/check_secrets.py"),
    Path("obs_captioner/web/static/dashboard.html"),  # HTML placeholders
    Path("config.json.example"),                      # Example schema with empty strings
}


def get_tracked_files():
    """Retrieve all files tracked by git."""
    res = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True
    )
    return [Path(p.strip()) for p in res.stdout.splitlines() if p.strip()]


def audit_tracked_filenames(tracked_files):
    """Ensure no forbidden filenames are tracked."""
    violations = []
    for rel_path in tracked_files:
        filename = rel_path.name
        for pattern in FORBIDDEN_TRACKED_PATTERNS:
            if pattern.search(filename):
                violations.append((rel_path, f"Forbidden credential/config file tracked in git matching: {pattern.pattern}"))
    return violations


def audit_file_contents(tracked_files):
    """Scan tracked files for secret patterns."""
    violations = []
    for rel_path in tracked_files:
        if rel_path in SAFE_FILE_EXCLUSIONS:
            continue

        full_path = REPO_ROOT / rel_path
        if not full_path.is_file():
            continue

        # Skip binary files
        try:
            with open(full_path, "r", encoding="utf-8", errors="strict") as f:
                content = f.read()
        except (UnicodeDecodeError, PermissionError):
            continue

        for pattern, description in SECRET_PATTERNS:
            match = pattern.search(content)
            if match:
                snippet = match.group(0)[:8] + "..."
                violations.append((rel_path, f"Found {description} snippet: {snippet}"))
    return violations


def check_gitignore():
    """Verify that config.json and sensitive patterns are present in .gitignore."""
    gitignore_path = REPO_ROOT / ".gitignore"
    if not gitignore_path.is_file():
        return ["Missing .gitignore file!"]

    content = gitignore_path.read_text(encoding="utf-8")
    required = ["config.json", ".env", "*.key", "*.pem", "google_credentials.json"]
    missing = [req for req in required if req not in content]
    if missing:
        return [f".gitignore is missing required ignore patterns: {missing}"]
    return []


def main():
    print("🔒 VoxStream Security Audit: Scanning for exposed API keys and secrets...")
    errors = []

    # 1. Check .gitignore
    gi_errors = check_gitignore()
    if gi_errors:
        errors.extend(gi_errors)

    # 2. Check tracked filenames
    tracked_files = get_tracked_files()
    filename_violations = audit_tracked_filenames(tracked_files)
    if filename_violations:
        for f, reason in filename_violations:
            errors.append(f"❌ Tracked file forbidden: {f} ({reason})")

    # 3. Check tracked file contents
    content_violations = audit_file_contents(tracked_files)
    if content_violations:
        for f, reason in content_violations:
            errors.append(f"❌ Secret detected in {f}: {reason}")

    if errors:
        print("\n🚨 SECURITY AUDIT FAILED! Potential secret leak detected:")
        for err in errors:
            print(f"  {err}")
        print("\nPlease remove the secrets / untrack the sensitive files before pushing to GitHub.")
        sys.exit(1)

    print(f"✅ Security Audit Passed! Scanned {len(tracked_files)} tracked files. No API keys or secrets detected.")
    sys.exit(0)


if __name__ == "__main__":
    main()
