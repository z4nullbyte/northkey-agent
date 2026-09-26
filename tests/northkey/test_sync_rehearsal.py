"""End-to-end rehearsal: rebrand, upstream moves, the fork syncs, installs fast-forward.

Builds throwaway repos (upstream, the Northkey fork, a published origin and one
end-user install) and drives northkey/tools/nk.py exactly as CI does. It proves
the fork's two promises:

1. Upstream keeps merging after the rebrand: every sync re-derives the brand from
   fresh upstream text, so there are no merge conflicts, and a moved anchor fails
   loudly without touching the tree.
2. Installs keep updating normally: the fork only ever moves forward, so an install
   takes each sync as a plain fast-forward (what `hermes update` does on `main`).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.northkey._brand import ROOT

UPDATE_PY_V1 = 'BASE = "https://nous.example"\nREPO = "Nous/agent"\n\n\ndef version():\n    return "1.0"\n'
BRANDED_REPO = 'REPO = "acme/northkey-agent"  # northkey'


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


class Lab:
    """Three repos plus a bare origin, all under tmp_path, with isolated git config."""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        gitconfig = tmp_path / "gitconfig"
        _write(gitconfig, "[user]\n\tname = lab\n\temail = lab@example.invalid\n"
                          "[init]\n\tdefaultBranch = main\n[core]\n\tautocrlf = false\n")
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": str(gitconfig), "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_TERMINAL_PROMPT": "0"}
        self.up, self.fork, self.origin, self.install = (
            tmp_path / "upstream", tmp_path / "fork", tmp_path / "origin.git", tmp_path / "install")

    def git(self, cwd: Path, *args: str) -> str:
        result = subprocess.run(["git", *args], cwd=cwd, env=self.env, capture_output=True, text=True)
        assert result.returncode == 0, f"git {' '.join(args)}: {result.stderr}"
        return result.stdout.strip()

    def nk(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(self.fork / "northkey" / "tools" / "nk.py"), *args],
                              cwd=self.fork, env=self.env, capture_output=True, text=True, encoding="utf-8")

    def upstream_commit(self, files: dict[str, str | None], message: str) -> str:
        for rel, text in files.items():
            if text is None:
                self.git(self.up, "rm", "-q", rel)
            else:
                _write(self.up / rel, text)
                self.git(self.up, "add", rel)
        self.git(self.up, "commit", "-q", "-m", message)
        return self.git(self.up, "rev-parse", "HEAD")

    def fork_file(self, rel: str) -> str:
        return (self.fork / rel).read_text(encoding="utf-8")


@pytest.fixture
def lab(tmp_path):
    lab = Lab(tmp_path)
    lab.up.mkdir()
    lab.git(lab.up, "init", "-q")
    lab.upstream_commit({"app/update.py": UPDATE_PY_V1, "app/other.py": "X = 1\n",
                         "README.md": "upstream\n"}, "upstream: initial")

    # The fork: upstream history + the Northkey layer.
    lab.git(tmp_path, "clone", "-q", str(lab.up), str(lab.fork))
    lab.git(lab.fork, "remote", "rename", "origin", "upstream")
    upstream_url = lab.up.as_uri()
    lab.git(lab.fork, "remote", "set-url", "upstream", upstream_url)
    tools = lab.fork / "northkey" / "tools"
    tools.mkdir(parents=True)
    shutil.copy2(ROOT / "northkey" / "tools" / "nk.py", tools / "nk.py")
    _write(lab.fork / "northkey" / "brand.yaml", (
        "name: Northkey\nname_upper: NORTHKEY\ncommand: northkey\ntagline: Pointed north.\n"
        "tagline_short: North.\nglyph: \"✦\"\n"
        "repo: {owner: acme, name: northkey-agent, branch: main}\n"
        "channels: {base_url: https://archive.example.invalid/northkey}\n"
        "skin: {default: northkey}\n"
        f"upstream: {{name: Up, author: Up, url: \"{upstream_url}\", ref: main}}\n"
        "attribution: Built on Up.\n"))
    _write(lab.fork / "northkey" / "seams.yaml", (
        "version: 1\nowned: [\"northkey/**\"]\nseams:\n"
        "  - id: update-authority\n    file: app/update.py\n    why: test\n    edits:\n"
        "      - find: 'REPO = \"Nous/agent\"'\n"
        "        replace: 'REPO = \"⟦repo.slug⟧\"  # northkey'\n"
        "      - find: 'BASE = \"https://nous.example\"'\n"
        "        replace: 'BASE = \"⟦channels.base_url⟧\"'\n"))
    assert lab.nk("sync", "--init", "upstream/main").returncode == 0
    rendered = lab.nk("render")
    assert rendered.returncode == 0, rendered.stderr
    lab.git(lab.fork, "add", "-A")
    lab.git(lab.fork, "commit", "-q", "-m", "northkey: rebrand")

    # Publish, then one end user installs from the fork.
    lab.git(tmp_path, "init", "-q", "--bare", str(lab.origin))
    lab.git(lab.fork, "remote", "add", "origin", str(lab.origin))
    lab.git(lab.fork, "push", "-q", "origin", "main")
    lab.git(tmp_path, "clone", "-q", str(lab.origin), str(lab.install))
    return lab


def _user_update(lab: Lab) -> None:
    """The install side of `hermes update` on the main channel: fetch origin, fast-forward only."""
    lab.git(lab.install, "fetch", "-q", "origin", "main")
    lab.git(lab.install, "merge", "-q", "--ff-only", "origin/main")


def test_rebrand_is_what_installs_receive(lab):
    assert BRANDED_REPO in (lab.install / "app" / "update.py").read_text(encoding="utf-8")
    assert lab.nk("verify", "--rev", "HEAD").returncode == 0


def test_upstream_updates_keep_flowing_after_the_rebrand(lab):
    before = lab.git(lab.fork, "rev-parse", "HEAD")
    # Upstream edits right next to the seam, bumps the version, adds and deletes files.
    new = lab.upstream_commit({
        "app/update.py": UPDATE_PY_V1.replace('REPO = "Nous/agent"\n', 'REPO = "Nous/agent"\nTIMEOUT = 30\n')
                                     .replace('"1.0"', '"1.1"'),
        "app/new.py": "NEW = True\n",
        "README.md": None,
    }, "upstream: timeout, v1.1")

    result = lab.nk("sync")
    assert result.returncode == 0, result.stderr

    parents = lab.git(lab.fork, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:]
    assert parents == [before, new]  # a real merge: upstream history is kept, nothing rewritten
    update_py = lab.fork_file("app/update.py")
    assert BRANDED_REPO in update_py and "TIMEOUT = 30" in update_py and '"1.1"' in update_py
    assert (lab.fork / "app" / "new.py").is_file() and not (lab.fork / "README.md").exists()
    assert lab.nk("verify", "--rev", "HEAD").returncode == 0

    lab.git(lab.fork, "push", "-q", "origin", "main")  # plain push: the fork never force-pushes
    _user_update(lab)
    assert lab.git(lab.install, "rev-parse", "HEAD") == lab.git(lab.fork, "rev-parse", "HEAD")
    assert BRANDED_REPO in (lab.install / "app" / "update.py").read_text(encoding="utf-8")


def test_a_moved_anchor_fails_loudly_and_leaves_the_fork_untouched(lab):
    head = lab.git(lab.fork, "rev-parse", "HEAD")
    lab.upstream_commit({"app/update.py": UPDATE_PY_V1.replace('"Nous/agent"', '"Nous/agent-v2"')},
                        "upstream: rename repo constant")

    result = lab.nk("sync")
    assert result.returncode == 2
    assert "update-authority" in result.stderr
    assert lab.git(lab.fork, "rev-parse", "HEAD") == head
    assert lab.git(lab.fork, "status", "--porcelain") == ""


def test_fixing_the_anchor_rides_along_in_the_sync_commit(lab):
    lab.upstream_commit({"app/update.py": UPDATE_PY_V1.replace('"Nous/agent"', '"Nous/agent-v2"')},
                        "upstream: rename repo constant")
    seams = lab.fork / "northkey" / "seams.yaml"
    _write(seams, seams.read_text(encoding="utf-8").replace('"Nous/agent"', '"Nous/agent-v2"'))

    result = lab.nk("sync")
    assert result.returncode == 0, result.stderr
    assert lab.git(lab.fork, "status", "--porcelain") == ""
    assert BRANDED_REPO in lab.fork_file("app/update.py")
    assert lab.nk("verify", "--rev", "HEAD").returncode == 0

    lab.git(lab.fork, "push", "-q", "origin", "main")
    _user_update(lab)
    assert BRANDED_REPO in (lab.install / "app" / "update.py").read_text(encoding="utf-8")


def test_hand_edits_to_upstream_files_are_caught(lab):
    _write(lab.fork / "app" / "other.py", "X = 2  # sneaky\n")
    lab.git(lab.fork, "commit", "-q", "-am", "edit an upstream file by hand")
    result = lab.nk("verify", "--rev", "HEAD")
    assert result.returncode == 1
    assert "app/other.py" in result.stdout
