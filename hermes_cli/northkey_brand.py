"""Northkey brand bridge (fork-owned; see northkey/README.md).

Loads the skins bundled in the checkout's ``northkey/skins/`` directory so every
profile gets them as built-ins. Upstream code reaches this module only through
the ``brand-skin-registry`` seam in hermes_cli/skin_engine.py; everything else
Northkey changes is rendered from northkey/brand.yaml by northkey/tools/nk.py.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

BRAND_DIR = Path(__file__).resolve().parents[1] / "northkey"


def bundled_dashboard_theme_files() -> List[Path]:
    """Dashboard theme YAML files shipped in ``northkey/dashboard-themes/``."""
    themes_dir = BRAND_DIR / "dashboard-themes"
    return sorted(themes_dir.glob("*.yaml")) if themes_dir.is_dir() else []


def bundled_skins() -> Dict[str, Dict[str, Any]]:
    """Skin definitions from ``northkey/skins/*.yaml``, keyed by their ``name``."""
    skins_dir = BRAND_DIR / "skins"
    if not skins_dir.is_dir():
        return {}
    import hermes_yaml as yaml

    skins: Dict[str, Dict[str, Any]] = {}
    for path in sorted(skins_dir.glob("*.yaml")):
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                data = yaml.safe_load(fh)
        except Exception:
            logger.debug("Skipping unreadable Northkey skin %s", path, exc_info=True)
            continue
        if isinstance(data, dict) and isinstance(data.get("name"), str):
            skins[data["name"]] = data
    return skins
