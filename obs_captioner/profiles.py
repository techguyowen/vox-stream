"""Modular Organization Profiles Architecture.

Manages organization-specific vocabulary, leaders, boundary stitches,
and settings (e.g. Waypoint Church, Corporate Keynotes, Higher Education).
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("obs_captioner.profiles")


DEFAULT_PROFILE_DATA: Dict[str, Any] = {
    "name": "General Organization",
    "church_mode": False,
    "leaders": [],
    "programs": [],
    "venues_and_geography": [],
    "speech_biasing_words": [],
    "terms": {},
    "boundary_splits": [],
}


WAYPOINT_PROFILE_DATA: Dict[str, Any] = {
    "name": "Waypoint Church",
    "church_mode": True,
    "leaders": ["Pastor Ben Luthi", "Pastor Lawrence", "Peyton West", "Ben Luthi"],
    "programs": ["Waypoint Kids", "Story Power Party", "Chet the Chicken", "Bert the Blobfish"],
    "venues_and_geography": ["Gymkhana", "RTP", "Research Triangle Park", "Raleigh", "South Durham", "East Chapel Hill"],
    "speech_biasing_words": [
        "Pastor Ben Luthi",
        "Pastor Lawrence",
        "Peyton West",
        "Waypoint Kids",
        "Story Power Party",
        "Chet the Chicken",
        "Bert the Blobfish",
        "Gymkhana",
        "RTP",
        "Research Triangle Park",
    ],
    "terms": {
        "way point": "Waypoint",
        "way poi": "Waypoint",
        "wave once": "Waypoint",
        "way pope": "Waypoint",
        "waypoint kids": "Waypoint Kids",
        "way point kids": "Waypoint Kids",
        "way point church": "Waypoint Church",
        "waypoint church": "Waypoint Church",
        "pastor lawrence": "Pastor Lawrence",
        "pastor ben luthi": "Pastor Ben Luthi",
        "pastor ben": "Pastor Ben",
        "ben uthe": "Ben Luthi",
        "ben lut": "Ben Luthi",
        "ben luthi": "Ben Luthi",
        "ben luther": "Ben Luthi",
        "ben loothi": "Ben Luthi",
        "luthi": "Luthi",
        "loothi": "Luthi",
        "peyton west": "Peyton West",
        "the journey church": "The Journey Church",
        "journey church": "Journey Church",
        "church centre": "Church Center",
        "church center": "Church Center",
        "jim conna": "Gymkhana",
        "jim conner": "Gymkhana",
        "jim connaught": "Gymkhana",
        "gymkhana": "Gymkhana",
        "the triangle": "the Triangle",
        "rtp": "RTP",
        "research triangle park": "Research Triangle Park",
        "raleigh": "Raleigh",
        "raleigh and waypoint": "Raleigh and Waypoint",
        "raleigh-durham": "Raleigh-Durham",
        "south durham": "South Durham",
        "east chapel hill": "East Chapel Hill",
        "chapel hill": "Chapel Hill",
        "headphones and pigeons": "headphones and fidgets",
        "headphones and pigeon": "headphones and fidgets",
        "chatter the chicken": "Chet the Chicken",
        "chick-fil-a's chicken": "Chet the Chicken",
        "chetha chicken": "Chet the Chicken",
        "chet the chicken": "Chet the Chicken",
        "bert the blobfish": "Bert the Blobfish",
        "bertha the blowfish": "Bert the Blobfish",
        "bertha blawfish": "Bert the Blobfish",
        "story power party": "Story Power Party",
    },
    "boundary_splits": [
        ["waypoint", "point"],
        ["way point", "point"],
        ["way poi", "point"],
    ],
}


class ProfileManager:
    """Discovers, loads, caches, and persists modular organization profiles."""

    def __init__(self, profiles_dir: Optional[Path] = None):
        if profiles_dir is None:
            root = Path(__file__).resolve().parent.parent
            self.profiles_dir = root / "profiles"
        else:
            self.profiles_dir = Path(profiles_dir)

        self._ensure_default_profiles()

    def _ensure_default_profiles(self):
        """Ensure default.json and waypoint.json exist in profiles directory."""
        try:
            self.profiles_dir.mkdir(parents=True, exist_ok=True)
            default_path = self.profiles_dir / "default.json"
            if not default_path.exists():
                with open(default_path, "w", encoding="utf-8") as f:
                    json.dump(DEFAULT_PROFILE_DATA, f, indent=2)

            waypoint_path = self.profiles_dir / "waypoint.json"
            if not waypoint_path.exists():
                with open(waypoint_path, "w", encoding="utf-8") as f:
                    json.dump(WAYPOINT_PROFILE_DATA, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not initialize profiles directory {self.profiles_dir}: {e}")

    def list_profiles(self) -> List[str]:
        """Return list of discovered profile slugs (filename without .json)."""
        if not self.profiles_dir.exists():
            return ["default", "waypoint"]
        profiles = []
        for p in self.profiles_dir.glob("*.json"):
            profiles.append(p.stem)
        if "default" not in profiles:
            profiles.insert(0, "default")
        if "waypoint" not in profiles:
            profiles.append("waypoint")
        return sorted(list(set(profiles)))

    def list_profiles_metadata(self) -> List[Dict[str, Any]]:
        """Return metadata for all discovered profiles, ensuring built-ins are always present."""
        self._ensure_default_profiles()
        metadata_by_id: Dict[str, Dict[str, Any]] = {}

        # Default built-ins baseline
        metadata_by_id["default"] = {
            "id": "default",
            "name": DEFAULT_PROFILE_DATA.get("name", "General Organization"),
            "church_mode": bool(DEFAULT_PROFILE_DATA.get("church_mode", False)),
            "is_builtin": True,
        }
        metadata_by_id["waypoint"] = {
            "id": "waypoint",
            "name": WAYPOINT_PROFILE_DATA.get("name", "Waypoint Church"),
            "church_mode": bool(WAYPOINT_PROFILE_DATA.get("church_mode", True)),
            "is_builtin": True,
        }

        if self.profiles_dir.exists():
            for p in self.profiles_dir.glob("*.json"):
                slug = p.stem
                data = self._load_file(p.name) or {}
                metadata_by_id[slug] = {
                    "id": slug,
                    "name": data.get("name", slug),
                    "church_mode": bool(data.get("church_mode", False)),
                    "is_builtin": slug in ("default", "waypoint"),
                }

        return sorted(list(metadata_by_id.values()), key=lambda x: x["id"])

    def create_profile(
        self,
        name: str,
        church_mode: bool = True,
        copy_from: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """Create a new custom organization profile and persist to disk."""
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        raw_slug = re.sub(r"[^\w\-]+", "_", (name or "").strip().lower()).strip("_")
        if not raw_slug:
            raw_slug = "custom"
        if raw_slug in ("default", "waypoint"):
            slug = f"{raw_slug}_custom"
        else:
            slug = raw_slug

        base_slug = slug
        counter = 1
        while slug in ("default", "waypoint"):
            slug = f"{base_slug}_{counter}"
            counter += 1

        if data is not None:
            profile_dict = dict(data)
        elif copy_from:
            source = self.get_profile(copy_from)
            profile_dict = dict(source)
        else:
            profile_dict = dict(DEFAULT_PROFILE_DATA)

        profile_dict["name"] = name.strip() if name else "Custom"
        profile_dict["church_mode"] = bool(church_mode)
        profile_dict["speech_biasing_words"] = list(profile_dict.get("speech_biasing_words", []))

        target_path = self.profiles_dir / f"{slug}.json"
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(profile_dict, f, indent=2)
        logger.info(f"Created profile '{name}' (slug: {slug}) at {target_path}")
        return slug, profile_dict

    def delete_profile(self, slug: str) -> bool:
        """Delete a custom organization profile. Built-in profiles cannot be deleted."""
        clean_slug = (slug or "").strip().lower()
        if clean_slug in ("default", "waypoint"):
            return False

        target_path = self.profiles_dir / f"{clean_slug}.json"
        if target_path.exists():
            try:
                target_path.unlink()
                logger.info(f"Deleted profile '{clean_slug}' from {target_path}")
                return True
            except Exception as e:
                logger.error(f"Failed to delete profile {target_path}: {e}")
                return False
        return False

    def get_profile(self, name_or_church: str) -> Dict[str, Any]:
        """Retrieve profile data by profile name, slug, or church name.

        Auto-maps: if church_name.strip().lower().startswith("waypoint")
        or profile is "waypoint", loads profiles/waypoint.json.
        """
        raw = (name_or_church or "").strip()
        lower = raw.lower()

        # Auto-map Waypoint variations
        if lower.startswith("waypoint") or lower == "waypoint":
            loaded = self._load_file("waypoint.json")
            return loaded if loaded else dict(WAYPOINT_PROFILE_DATA)

        # Direct file check (slug or filename)
        slug = re.sub(r"[^\w\-]+", "_", lower).strip("_")
        if slug:
            candidate_file = self.profiles_dir / f"{slug}.json"
            if candidate_file.exists():
                loaded = self._load_file(candidate_file.name)
                if loaded:
                    return loaded

        # Search profiles by "name" property
        if self.profiles_dir.exists():
            for p_file in self.profiles_dir.glob("*.json"):
                data = self._load_file(p_file.name)
                if data and data.get("name", "").strip().lower() == lower:
                    return data

        # Default fallback
        loaded_default = self._load_file("default.json")
        if loaded_default:
            return loaded_default
        return dict(DEFAULT_PROFILE_DATA)

    def save_profile(self, name: str, data: Dict[str, Any]) -> str:
        """Persist profile data to profiles/{slug}.json."""
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^\w\-]+", "_", name.strip().lower()).strip("_")
        if not slug:
            slug = "custom"
        target_path = self.profiles_dir / f"{slug}.json"
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logger.info(f"Saved profile '{name}' to {target_path}")
        return slug

    def update_profile_words(
        self,
        slug_or_name: str,
        terms: Optional[Dict[str, str]] = None,
        speech_biasing_words: Optional[List[str]] = None,
    ) -> bool:
        """Update terms and/or speech biasing words in target profile on disk."""
        try:
            profile = self.get_profile(slug_or_name)
            if not profile:
                return False

            raw = (slug_or_name or "").strip()
            lower = raw.lower()
            slug = "default"
            if lower.startswith("waypoint") or lower == "waypoint":
                slug = "waypoint"
            else:
                candidate_slug = re.sub(r"[^\w\-]+", "_", lower).strip("_")
                if candidate_slug and (self.profiles_dir / f"{candidate_slug}.json").exists():
                    slug = candidate_slug
                elif self.profiles_dir.exists():
                    for p_file in self.profiles_dir.glob("*.json"):
                        data = self._load_file(p_file.name)
                        if data and data.get("name", "").strip().lower() == lower:
                            slug = p_file.stem
                            break
                    else:
                        slug = candidate_slug or "default"
                else:
                    slug = candidate_slug or "default"

            if terms is not None:
                profile["terms"] = dict(terms)
            if speech_biasing_words is not None:
                profile["speech_biasing_words"] = list(speech_biasing_words)

            target_path = self.profiles_dir / f"{slug}.json"
            self.profiles_dir.mkdir(parents=True, exist_ok=True)
            with open(target_path, "w", encoding="utf-8") as f:
                json.dump(profile, f, indent=2)
            logger.info(f"Updated profile words for '{slug}' at {target_path}")
            return True
        except Exception as e:
            logger.error(f"Error updating profile words for '{slug_or_name}': {e}")
            return False

    def _load_file(self, filename: str) -> Optional[Dict[str, Any]]:
        target_path = self.profiles_dir / filename
        if target_path.exists():
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading profile {target_path}: {e}")
        return None


_instance: Optional[ProfileManager] = None


def get_profile_manager() -> ProfileManager:
    """Singleton helper returning shared ProfileManager instance."""
    global _instance
    if _instance is None:
        _instance = ProfileManager()
    return _instance
