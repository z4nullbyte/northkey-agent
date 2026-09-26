"""What a Northkey user sees: skin, name, command, identity and secure defaults."""

import re
import unicodedata

import pytest

from hermes_cli import _install_repair, _launchers
from hermes_cli.config_defaults import DEFAULT_CONFIG
from hermes_cli.default_soul import DEFAULT_SOUL_MD
from hermes_cli.skin_engine import _BUILTIN_SKINS, load_skin
from tests.northkey._brand import BRAND

SKIN = BRAND["skin"]["default"]
_MARKUP = re.compile(r"\[/?[^\]]*\]")


def test_northkey_skin_is_a_builtin_and_the_default():
    assert SKIN in _BUILTIN_SKINS
    assert DEFAULT_CONFIG["display"]["skin"] == SKIN
    skin = load_skin(SKIN)
    assert skin.get_branding("agent_name") == BRAND["name"]
    assert skin.banner_logo and skin.banner_hero


def test_dashboard_opens_in_the_northkey_theme():
    from hermes_cli.web_server_dashboard import _discover_user_themes

    assert DEFAULT_CONFIG["dashboard"]["theme"] == SKIN
    theme = next(t for t in _discover_user_themes() if t["name"] == SKIN)
    assert theme["palette"]["background"]["hex"] == load_skin(SKIN).colors["background"]
    assert "fontUrl" not in theme["typography"]  # nothing fetched from a font CDN


def _visible_text(skin) -> str:
    parts = [*skin.branding.values(), skin.tool_prefix,
             _MARKUP.sub("", skin.banner_logo), _MARKUP.sub("", skin.banner_hero)]
    for key in ("waiting_faces", "thinking_faces"):
        parts.extend(skin.spinner.get(key, []))
    return "".join(parts)


def test_every_glyph_is_one_cell_wide_in_every_locale():
    """Ambiguous-width glyphs (─ │ · • ◆) render two cells wide in CJK terminals."""
    wide = {ch for ch in _visible_text(load_skin(SKIN))
            if unicodedata.east_asian_width(ch) in ("A", "W", "F")}
    assert not wide, f"ambiguous/wide glyphs in the Northkey skin: {sorted(wide)}"


def test_agent_identity_is_northkey_and_credits_upstream():
    assert DEFAULT_SOUL_MD.startswith(f"You are {BRAND['name']}, built on Hermes Agent by Nous Research.")
    from agent.prompt_builder import DEFAULT_AGENT_IDENTITY
    assert DEFAULT_AGENT_IDENTITY.startswith(f"You are {BRAND['name']},")


def test_public_readme_matches_the_brand():
    """.github/README.md is hand-written; fail if it drifts from brand.yaml."""
    from tests.northkey._brand import GIT_URL, ROOT, SLUG

    readme = (ROOT / ".github" / "README.md").read_text(encoding="utf-8")
    branch = BRAND["repo"]["branch"]
    for needle in (GIT_URL, f"raw.githubusercontent.com/{SLUG}/{branch}/scripts/install.sh",
                   f"raw.githubusercontent.com/{SLUG}/{branch}/scripts/install.ps1", BRAND["tagline"]):
        assert needle in readme
    linked = set(re.findall(r"github(?:usercontent)?\.com/([\w-]+/[\w.-]+?)(?:\.git)?(?=[/\s)`])", readme))
    assert linked <= {SLUG, "NousResearch/hermes-agent"}, f"stale repository links: {linked}"


def test_northkey_command_is_minted_with_hermes():
    command = BRAND["command"]
    assert {"hermes", command} <= set(_launchers.WINDOWS_BIN_LAUNCHERS)
    assert _launchers.ENTRY_POINTS[command] == _launchers.ENTRY_POINTS["hermes"]
    assert _install_repair._WINDOWS_BIN_LAUNCHERS == _launchers.WINDOWS_BIN_LAUNCHERS


@pytest.mark.parametrize(("path", "expected"), [
    (("approvals", "mode"), "manual"),
    (("gateway", "strict"), True),
    (("web", "keyless_fallback"), False),
    (("web", "keyless_rescue"), False),
    (("auth", "adopt_external_logins"), False),
    (("skills", "guard_agent_created"), True),
])
def test_secure_defaults(path, expected):
    value = DEFAULT_CONFIG
    for key in path:
        value = value[key]
    assert value == expected
