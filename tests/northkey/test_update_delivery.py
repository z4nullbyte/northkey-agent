"""Northkey installs keep updating after the rebrand, only ever from the fork.

`hermes update` resolves its channel before any git work. Northkey resolves the
built-in channels (main, stable, canary) to the fork's branch without a network
round-trip, so installs update even when no channel record is reachable, and
stable/canary subscriptions carried over from Hermes migrate to `main` by
themselves. No update path may point back at Nous.
"""

import io
import json
import re
import subprocess
from urllib.error import HTTPError

import pytest

from hermes_cli import release_channels, source_releases
from hermes_cli.update_cmd_git import OFFICIAL_REPO_URL, _is_fork
from tests.northkey._brand import BRAND, GIT_URL, ROOT, SLUG


class _Response(io.BytesIO):
    def __init__(self, url: str, body: bytes):
        super().__init__(body)
        self._url = url

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def archive(monkeypatch):
    """A fake channel archive: serves `served` objects, 404 for the rest (or `status`)."""
    served: dict[str, bytes] = {}
    requested: list[str] = []
    state = {"status": 404}

    def open_(request, timeout=30):
        url = request.full_url
        requested.append(url)
        for key, body in served.items():
            if url == f"{source_releases._PUBLIC_BASE}/{key}":
                return _Response(url, body)
        raise HTTPError(url, state["status"], "archive says no", {}, None)

    class _Opener:
        open = staticmethod(open_)

    monkeypatch.setattr(release_channels, "build_opener", lambda *handlers: _Opener())
    return served, requested, state


def _record(name: str, repository: str) -> bytes:
    return json.dumps({
        "schema": 1, "name": name, "repository": repository, "policy": "source-branch", "state": "active",
        "revision": 1, "nextSequence": 1, "identity": None, "head": None,
        "delivery": {"kind": "source-branch", "branch": "main"},
    }).encode()


def test_channel_archive_is_the_forks_own():
    assert source_releases._PUBLIC_BASE == BRAND["channels"]["base_url"]
    assert source_releases.OFFICIAL_REPOSITORY == SLUG
    release_channels.public_base(source_releases._PUBLIC_BASE)  # https, no query, no redirects


@pytest.mark.parametrize("channel", ["main", "stable", "canary"])
def test_builtin_channels_follow_the_fork_branch_offline(archive, channel):
    _, requested, state = archive
    state["status"] = 429  # even a rate-limited or unreachable archive cannot block an update
    target = source_releases.resolve_source_target(channel, repository=SLUG)
    assert (target.channel, target.branch, target.commit, target.repository) == (
        "main", BRAND["repo"]["branch"], None, SLUG)
    assert target.retired is (channel != "main")  # stable/canary subscriptions migrate to main
    assert requested == []


def test_an_upstream_record_cannot_steer_a_northkey_install(archive):
    served, requested, _ = archive
    served["releases/channels/beta.json"] = _record("beta", "NousResearch/hermes-agent")
    with pytest.raises((release_channels.ChannelError, ValueError)):
        source_releases.resolve_source_target("beta", repository=SLUG)
    assert requested == [f"{BRAND['channels']['base_url']}/releases/channels/beta.json"]


@pytest.mark.parametrize("url", [GIT_URL, GIT_URL[:-4], f"git@github.com:{SLUG}.git", f"git@github.com:{SLUG}"])
def test_the_fork_is_the_official_origin(url):
    assert _is_fork(url) is False
    assert OFFICIAL_REPO_URL == GIT_URL


def test_other_forks_are_still_detected_as_forks():
    assert _is_fork("https://github.com/someone-else/northkey-agent.git") is True


def test_source_repository_follows_a_northkey_origin(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", GIT_URL], check=True)
    assert source_releases.source_repository(["git"], tmp_path) == SLUG


# Update and install paths, and the only Nous references allowed to remain in them.
_UPDATE_PATH = [
    "hermes_cli/source_releases.py", "hermes_cli/source_check.py", "hermes_cli/release_channels.py",
    "hermes_cli/update_channel.py", "hermes_cli/_launchers.py", "hermes_cli/_install_repair.py",
    "hermes_cli/uninstall.py", "hermes_cli/banner.py", "hermes_cli/debug.py", "gateway/slash_commands.py",
    "scripts/install.sh", "scripts/install.ps1", "scripts/install.cmd",
]
_NOUS_AUTHORITY = re.compile(
    r"hermes-assets\.nousresearch\.com(?!/upstream/sha256/)"  # content-addressed mirrors are fine
    r"|NousResearch/hermes-agent"
    r"|hermes-agent\.nousresearch\.com(?!/docs)")  # upstream docs stay the feature reference
_ALLOWED = [
    # Nous URLs stay recognized as official too (a Hermes checkout is never called a fork).
    re.compile(r'^\s*"(https://github\.com/|git@github\.com:)NousResearch/hermes-agent(\.git)?",$'),
    # Docstring prose.
    re.compile(r"^\s*Release URL always points at the canonical NousResearch/hermes-agent repo"),
    # The installers move Hermes checkouts to the fork by recognising their Nous origin.
    re.compile(r"northkey: move Hermes checkouts to the fork"),
]


def test_no_update_path_points_back_at_nous():
    """Catches upstream refactors that re-introduce a Nous endpoint through a clean merge."""
    paths = sorted({*_UPDATE_PATH, *(p.relative_to(ROOT).as_posix()
                                     for pattern in ("hermes_cli/update_*.py", "pm/*.py")
                                     for p in ROOT.glob(pattern))})
    offenders = []
    for rel in paths:
        for number, line in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
            if _NOUS_AUTHORITY.search(line) and not any(rule.search(line) for rule in _ALLOWED):
                offenders.append(f"{rel}:{number}: {line.strip()}")
    assert not offenders, "update path points at Nous; add a seam:\n" + "\n".join(offenders)


@pytest.mark.parametrize("script", ["scripts/install.sh", "scripts/install.ps1"])
def test_installer_rerun_moves_hermes_checkouts_to_the_fork(script):
    """Re-running the Northkey installer over a Hermes checkout re-points origin to the fork."""
    text = (ROOT / script).read_text(encoding="utf-8")
    assert "northkey: move Hermes checkouts to the fork" in text
    assert GIT_URL in text
