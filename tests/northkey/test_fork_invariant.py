"""The fork's tree is exactly upstream + Northkey-owned paths + seams (northkey/seams.yaml).

If this fails after an upstream sync, a seam anchor moved or someone edited an
upstream file by hand. Fix it in northkey/seams.yaml, then `nk.py render`.
"""

from tests.northkey._brand import load_nk


def test_tree_is_upstream_plus_owned_paths_plus_seams():
    nk = load_nk()
    assert nk.verify(None) == []


def test_every_seam_still_matches_the_pinned_upstream_text():
    nk = load_nk()
    base = nk.read_lock()["commit"]
    nk.render_check(base)  # raises NkError naming the seam whose anchor moved
