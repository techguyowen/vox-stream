#!/usr/bin/env python3
"""CLI utility to bump VoxStream version numbers across project files.

Usage:
    python scripts/bump_version.py [major | medium | minor | patch] [--dry-run]
    python scripts/bump_version.py --set 1.2.0 [--dry-run]
"""

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from obs_captioner.version import (
    VERSION,
    bump_version_string,
    parse_version,
)


def bump_version(bump_type: str = "patch", custom_version: str = None, dry_run: bool = False) -> str:
    current_v = VERSION
    if custom_version:
        new_v = re.sub(r"^[vV]", "", custom_version.strip())
        parse_version(new_v)  # validate format
    else:
        new_v = bump_version_string(current_v, bump_type)

    major, minor, patch = parse_version(new_v)
    today = datetime.date.today().isoformat()

    print(f"Bumping version: {current_v} -> {new_v} (Major: {major}, Minor: {minor}, Patch: {patch})")
    if dry_run:
        print("[DRY RUN] No files modified.")
        return new_v

    # 1. Update obs_captioner/version.py
    version_py_path = PROJECT_ROOT / "obs_captioner" / "version.py"
    if version_py_path.exists():
        content = version_py_path.read_text(encoding="utf-8")
        content = re.sub(r'VERSION\s*=\s*"[^"]+"', f'VERSION = "{new_v}"', content)
        content = re.sub(r'VERSION_INFO\s*=\s*\([^\)]+\)', f"VERSION_INFO = ({major}, {minor}, {patch})", content)
        content = re.sub(r'RELEASE_DATE\s*=\s*"[^"]+"', f'RELEASE_DATE = "{today}"', content)
        version_py_path.write_text(content, encoding="utf-8")
        print(f"  ✓ Updated {version_py_path.relative_to(PROJECT_ROOT)}")

    # 2. Update version.json
    version_json_path = PROJECT_ROOT / "version.json"
    data = {}
    if version_json_path.exists():
        try:
            data = json.loads(version_json_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["version"] = new_v
    data["major"] = major
    data["minor"] = minor
    data["patch"] = patch
    data["release_date"] = today
    version_json_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"  ✓ Updated {version_json_path.relative_to(PROJECT_ROOT)}")

    # 3. Update build_release.sh
    build_sh_path = PROJECT_ROOT / "build_release.sh"
    if build_sh_path.exists():
        sh_content = build_sh_path.read_text(encoding="utf-8")
        sh_content = re.sub(r'VERSION="[^"]+"', f'VERSION="{new_v}"', sh_content)
        build_sh_path.write_text(sh_content, encoding="utf-8")
        print(f"  ✓ Updated {build_sh_path.relative_to(PROJECT_ROOT)}")

    print(f"🎉 Successfully bumped to version {new_v}!")
    return new_v


def main():
    parser = argparse.ArgumentParser(description="Bump VoxStream version")
    parser.add_argument(
        "type",
        nargs="?",
        default="patch",
        choices=["major", "medium", "minor", "patch", "micro"],
        help="Type of version increment (default: patch)",
    )
    parser.add_argument("--set", type=str, default=None, help="Set explicit version string (e.g. 1.2.0)")
    parser.add_argument("--dry-run", action="store_true", help="Preview version changes without writing files")

    args = parser.parse_args()
    bump_version(bump_type=args.type, custom_version=args.set, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
