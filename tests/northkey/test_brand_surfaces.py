"""What a Northkey user sees and gets: skin, name, command, identity and secure defaults."""

import re
import unicodedata
from pathlib import Path

import pytest

from hermes_cli import _launchers
from hermes_cli.config_defaults import DEFAULT_CONFIG
from hermes_cli.default_soul import DEFAULT_SOUL_MD, is_legacy_template_soul
from hermes_cli.skin_engine import _BUILTIN_SKINS, load_skin
from tests.northkey._brand import BRAND, ROOT, SLUG

import hermes_yaml

SKIN = BRAND["skin"]["default"]
_MARKUP = re.compile(r"\[/?[^\]]*\]")
HARDENED = [
    (("approvals", "mode"), "manual"),
    (("gateway", "strict"), True),
    (("web", "keyless_fallback"), False),
    (("web", "keyless_rescue"), False),
    (("auth", "adopt_external_logins"), False),
    (("skills", "guard_agent_created"), True),
]


def _get(config: dict, path: tuple):
    for key in path:
        config = config[key]
    return config


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


def test_hermes_seeded_soul_upgrades_to_northkey():
    """An untouched SOUL.md seeded by upstream Hermes counts as auto-seeded, so it upgrades."""
    hermes_default = DEFAULT_SOUL_MD.replace(
        f"You are {BRAND['name']}, built on Hermes Agent by Nous Research.",
        "You are Hermes Agent, built by Nous Research.")
    assert is_legacy_template_soul(hermes_default)
    assert is_legacy_template_soul(hermes_default.replace("—", "--"))
    assert not is_legacy_template_soul(hermes_default + " Always answer in haiku.")


def test_northkey_command_is_an_alias_of_hermes_on_posix(tmp_path):
    """One process identity: `northkey` runs the hermes launcher (as `hermes-agent` does)."""
    root, out = tmp_path / "install", tmp_path / "bin"
    (root / ".hermes" / "bin").mkdir(parents=True)
    assert BRAND["command"] not in _launchers.WINDOWS_BIN_LAUNCHERS  # no separate northkey.exe
    _launchers._publish_conveniences(root, out, (*_launchers.WINDOWS_BIN_LAUNCHERS, "hermes-agent", BRAND["command"]))
    alias = out / BRAND["command"]
    assert str(root / ".hermes" / "bin" / "hermes") in alias.read_text(encoding="utf-8")
    assert _launchers._owns_launcher(alias, root)  # later updates repair it, uninstall removes it


def test_northkey_command_is_an_alias_of_hermes_on_windows(tmp_path, monkeypatch):
    """Staging the Windows launchers also writes northkey.cmd, which only calls hermes."""
    root, out = tmp_path / "install", tmp_path / "bin"
    root.mkdir()
    monkeypatch.setattr(_launchers, "_is_windows", lambda: True)
    monkeypatch.setattr(_launchers, "stage_launcher",
                        lambda name, repo_root, out_dir: Path(out_dir) / f"{name}.cmd")
    staged = _launchers.ensure_install_launchers(root, out)
    assert len(staged) == len(_launchers.WINDOWS_BIN_LAUNCHERS)  # upstream's count is unchanged
    assert (out / f"{BRAND['command']}.cmd").read_bytes() == b'@call "%~dp0hermes" %*\r\n'


@pytest.mark.parametrize(("path", "expected"), HARDENED)
def test_secure_defaults(path, expected):
    assert _get(DEFAULT_CONFIG, path) == expected


def test_config_template_does_not_undo_the_northkey_defaults():
    """Installers copy cli-config.yaml.example verbatim; its live keys override DEFAULT_CONFIG."""
    template = hermes_yaml.safe_load((ROOT / "cli-config.yaml.example").read_text(encoding="utf-8"))
    checked = [*HARDENED, (("display", "skin"), SKIN), (("dashboard", "theme"), SKIN)]
    for path, expected in checked:
        try:
            value = _get(template, path)
        except (KeyError, TypeError):
            continue  # absent from the template: DEFAULT_CONFIG applies
        assert value == expected, f"cli-config.yaml.example sets {'.'.join(path)}={value!r}"


def test_gateway_media_is_strict_without_explicit_config(monkeypatch):
    """The gateway bridges only the user's config; strict must hold when gateway.strict is absent."""
    from gateway import media_policy

    monkeypatch.delenv("HERMES_MEDIA_DELIVERY_STRICT", raising=False)
    media_policy.apply_media_policy_env({"gateway": {}})
    assert media_policy.media_delivery_strict() is True


def test_whatsapp_reply_prefix_matches_on_both_sides():
    """The bridge detects its own echoes by this prefix; the two copies must stay identical."""
    bridge = re.search(r"DEFAULT_REPLY_PREFIX = '([^']*)'",
                       (ROOT / "scripts/whatsapp-bridge/bridge.js").read_text(encoding="utf-8")).group(1)
    common = re.search(r'DEFAULT_REPLY_PREFIX: str = "([^"]*)"',
                       (ROOT / "gateway/platforms/whatsapp_common.py").read_text(encoding="utf-8")).group(1)
    assert bridge == common and BRAND["name"] in common


def test_public_readme_matches_the_brand():
    """.github/README.md is hand-written; fail if it drifts from brand.yaml."""
    readme = (ROOT / ".github" / "README.md").read_text(encoding="utf-8")
    branch = BRAND["repo"]["branch"]
    for needle in (f"raw.githubusercontent.com/{SLUG}/{branch}/scripts/install.sh",
                   f"raw.githubusercontent.com/{SLUG}/{branch}/scripts/install.ps1", BRAND["tagline"]):
        assert needle in readme
    linked = set(re.findall(r"github(?:usercontent)?\.com/([\w-]+/[\w.-]+?)(?:\.git)?(?=[/\s)`])", readme))
    assert linked <= {SLUG, "NousResearch/hermes-agent"}, f"stale repository links: {linked}"
