"""Northkey installs keep updating after the rebrand.

`hermes update` resolves its channel from `_PUBLIC_BASE` before any git work and
rejects a record whose repository is not the install's origin. These tests pin
the Northkey side of that contract: the fork publishes its own record, installs
resolve `main` to the fork's branch, and no update path points back at Nous.
"""

import io
import json
import re
import subprocess
from urllib.error import HTTPError

import pytest

from hermes_cli import release_channels, source_releases
from hermes_cli.update_cmd_git import OFFICIAL_REPO_URL, _is_fork
from tests.northkey._brand import BRAND, GIT_URL, RECORD, ROOT, SLUG


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
    """Serve channel objects from a dict; everything else is a 404. Records requested URLs."""
    served: dict[str, bytes] = {}
    requested: list[str] = []

    def open_(request, timeout=30):
        url = request.full_url
        requested.append(url)
        for key, body in served.items():
            if url == f"{source_releases._PUBLIC_BASE}/{key}":
                return _Response(url, body)
        raise HTTPError(url, 404, "Not Found", {}, None)

    class _Opener:
        open = staticmethod(open_)

    monkeypatch.setattr(release_channels, "build_opener", lambda *handlers: _Opener())
    return served, requested


def test_channel_archive_is_the_forks_own():
    assert source_releases._PUBLIC_BASE == BRAND["channels"]["base_url"]
    assert source_releases.OFFICIAL_REPOSITORY == SLUG
    release_channels.public_base(source_releases._PUBLIC_BASE)  # https, no query, no redirects


def test_published_main_record_is_valid_for_the_fork():
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    validated = release_channels.validate_record(record, name="main", repository=SLUG)
    assert validated["policy"] == "source-branch"
    assert validated["delivery"] == {"kind": "source-branch", "branch": BRAND["repo"]["branch"]}


def test_update_resolves_main_to_the_fork_branch(archive):
    served, requested = archive
    served["releases/channels/main.json"] = RECORD.read_bytes()
    target = source_releases.resolve_source_target("main", repository=SLUG)
    assert (target.channel, target.repository, target.branch, target.commit) == (
        "main", SLUG, BRAND["repo"]["branch"], None)
    assert requested == [f"{BRAND['channels']['base_url']}/releases/channels/main.json"]


def test_update_still_follows_main_while_no_record_is_published(archive):
    target = source_releases.resolve_source_target("main", repository=SLUG)
    assert (target.branch, target.repository) == ("main", SLUG)


def test_an_upstream_record_cannot_steer_a_northkey_install(archive):
    served, _ = archive
    upstream_record = json.loads(RECORD.read_text(encoding="utf-8"))
    upstream_record["repository"] = "NousResearch/hermes-agent"
    served["releases/channels/main.json"] = json.dumps(upstream_record).encode()
    with pytest.raises((release_channels.ChannelError, ValueError)):
        source_releases.resolve_source_target("main", repository=SLUG)


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


# Update-path files and the only Nous references allowed to remain in them.
_UPDATE_PATH = [
    "hermes_cli/source_releases.py", "hermes_cli/source_check.py", "hermes_cli/release_channels.py",
    "hermes_cli/update_channel.py", "hermes_cli/_launchers.py", "hermes_cli/_install_repair.py",
    "hermes_cli/uninstall.py", "hermes_cli/banner.py", "scripts/install.sh", "scripts/install.ps1",
    "scripts/install.cmd",
]
_NOUS_AUTHORITY = re.compile(r"hermes-assets\.nousresearch\.com(?!/upstream/sha256/)|NousResearch/hermes-agent"
                             r"|hermes-agent\.nousresearch\.com/install")
_ALLOWED = [
    # Nous URLs stay recognized as official too (a Hermes checkout is never called a fork).
    re.compile(r'^\s*"(https://github\.com/|git@github\.com:)NousResearch/hermes-agent(\.git)?",$'),
    # Docstring prose.
    re.compile(r"^\s*Release URL always points at the canonical NousResearch/hermes-agent repo"),
]


def test_no_update_path_points_back_at_nous():
    """Catches upstream refactors that re-introduce a Nous endpoint through a clean merge."""
    paths = sorted({*_UPDATE_PATH, *(p.relative_to(ROOT).as_posix()
                                     for p in (ROOT / "hermes_cli").glob("update_*.py"))})
    offenders = []
    for rel in paths:
        for number, line in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
            if _NOUS_AUTHORITY.search(line) and not any(rule.search(line) for rule in _ALLOWED):
                offenders.append(f"{rel}:{number}: {line.strip()}")
    assert not offenders, "update path points at Nous; add a seam:\n" + "\n".join(offenders)
