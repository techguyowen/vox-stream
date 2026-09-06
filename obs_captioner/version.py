"""Version definition and semantic version utilities for VoxStream."""

from __future__ import annotations

import re
from typing import Tuple

# Current application version
VERSION = "1.1.0"
VERSION_INFO = (1, 1, 0)
RELEASE_DATE = "2026-09-06"
CODENAME = "DirectML & Lexicon"


def parse_version(v_str: str) -> Tuple[int, int, int]:
    """Parse a version string into a 3-integer tuple (major, minor, patch).

    Handles strings like '1.1.0', 'v1.1.0', '1.1.0-beta.1', '1.2', 'v2'.
    Returns (0, 0, 0) if parsing fails completely.
    """
    if not v_str:
        return (0, 0, 0)

    # Strip leading 'v' or 'V' and any whitespace
    cleaned = re.sub(r"^[vV]", "", str(v_str).strip())

    # Extract digits from the primary version prefix (ignore pre-release tags like -alpha)
    match = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", cleaned)
    if not match:
        return (0, 0, 0)

    major = int(match.group(1) or 0)
    minor = int(match.group(2) or 0)
    patch = int(match.group(3) or 0)
    return (major, minor, patch)


def compare_versions(v1: str, v2: str) -> int:
    """Compare two semantic version strings.

    Returns:
         1 if v1 > v2
        -1 if v1 < v2
         0 if v1 == v2
    """
    t1 = parse_version(v1)
    t2 = parse_version(v2)

    if t1 > t2:
        return 1
    elif t1 < t2:
        return -1
    else:
        return 0


def is_version_newer(candidate: str, baseline: str) -> bool:
    """Return True if candidate version is strictly newer than baseline version."""
    return compare_versions(candidate, baseline) > 0


def get_version_bump_type(candidate: str, baseline: str) -> str:
    """Determine whether an update is 'major', 'minor' (medium), 'patch' (minor), or 'none'."""
    c_maj, c_min, c_pat = parse_version(candidate)
    b_maj, b_min, b_pat = parse_version(baseline)

    if c_maj > b_maj:
        return "major"
    elif c_min > b_min:
        return "minor"
    elif c_pat > b_pat:
        return "patch"
    return "none"


def bump_version_string(current: str, bump_type: str = "patch") -> str:
    """Increment a version string according to bump_type.

    Supported bump_type:
    - 'major': 1.1.0 -> 2.0.0
    - 'medium' or 'minor': 1.1.0 -> 1.2.0
    - 'patch': 1.1.0 -> 1.1.1
    """
    major, minor, patch = parse_version(current)
    b_type = bump_type.strip().lower()

    if b_type == "major":
        return f"{major + 1}.0.0"
    elif b_type in ("medium", "minor"):
        return f"{major}.{minor + 1}.0"
    elif b_type in ("patch", "micro"):
        return f"{major}.{minor}.{patch + 1}"
    else:
        raise ValueError(f"Unknown bump type: {bump_type}. Expected 'major', 'medium'/'minor', or 'patch'.")
