"""The fork's tree is exactly upstream + Northkey-owned paths + seams (northkey/seams.yaml).

If this fails after an upstream sync, a seam anchor moved or someone edited an
upstream file by hand. Fix it in northkey/seams.yaml, then `nk.py render`.
"""

from tests.northkey._brand import load_nk


def test_tree_is_upstream_plus_owned_paths_plus_seams():
    nk = load_nk()
    problems, _skipped = nk.verify(None)
    assert problems == []


def test_every_required_seam_matches_the_pinned_upstream_text():
    nk = load_nk()
    src = nk.Source()
    manifest = nk.load_manifest(src)
    # Raises NkError naming the seam when a required anchor moved.
    _, skipped = nk.render_tree(nk.read_lock(src)["commit"], manifest,
                                nk.brand_context(nk.load_yaml(src.read(nk.BRAND_PATH))))
    optional = {seam.id for seam in manifest.seams if not seam.required}
    assert set(skipped) <= optional
