"""Shared helpers for the Northkey fork tests (fork-owned)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import hermes_yaml

ROOT = Path(__file__).resolve().parents[2]
BRAND = hermes_yaml.safe_load((ROOT / "northkey" / "brand.yaml").read_text(encoding="utf-8"))
SLUG = f"{BRAND['repo']['owner']}/{BRAND['repo']['name']}"
GIT_URL = f"https://github.com/{SLUG}.git"
RECORD = ROOT / "northkey" / "release-archive" / "releases" / "channels" / "main.json"


def load_nk():
    """Import northkey/tools/nk.py (it is a script, not a package module)."""
    if "northkey_nk" in sys.modules:
        return sys.modules["northkey_nk"]
    spec = importlib.util.spec_from_file_location("northkey_nk", ROOT / "northkey" / "tools" / "nk.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module
