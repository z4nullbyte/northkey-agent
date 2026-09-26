"""Upstream contracts re-asserted with Northkey's values.

Tests listed in northkey/expected-divergence.txt pin a Hermes default that
Northkey changes on purpose. Where the contract behind them still matters,
it is re-run here through upstream's own test code with Northkey's value.
"""

from pathlib import Path

from tests.northkey._brand import ROOT
from tests.tools.test_approval_mode_parity import (  # noqa: F401  (pytest fixtures)
    hermes_home,
    test_mode_and_timeout_parity_across_surfaces as upstream_parity,
    tui_server,
)


def test_manual_default_holds_on_every_approval_surface(hermes_home, tui_server):
    """With no config, core, TUI and Codex surfaces all resolve Northkey's `manual` default."""
    upstream_parity(hermes_home, tui_server, None, "manual", 300)


def test_every_expected_divergence_still_names_a_real_test():
    """A stale entry would silently deselect nothing; keep the list honest after syncs."""
    missing = []
    for line in (ROOT / "northkey" / "expected-divergence.txt").read_text(encoding="utf-8").splitlines():
        node = line.split("#", 1)[0].strip()
        if not node:
            continue
        path, _, name = node.partition("::")
        test_file = ROOT / Path(path)
        function = name.split("::")[-1].split("[")[0]
        if not test_file.is_file() or f"def {function}(" not in test_file.read_text(encoding="utf-8"):
            missing.append(node)
    assert not missing, "expected-divergence.txt lists tests that no longer exist:\n" + "\n".join(missing)
