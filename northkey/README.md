# Northkey maintainer guide

Northkey is a rebranded distribution of [Hermes Agent](https://github.com/NousResearch/hermes-agent) (MIT, Nous Research) that keeps receiving upstream updates for as long as upstream exists. This folder holds everything that makes Northkey *Northkey*.

## สรุปภาษาไทย (Quick guide)

- **อัปเดตจาก upstream (Hermes) ได้ตลอด:** รัน `python northkey/tools/nk.py sync`. คำสั่งนี้ merge โค้ดล่าสุดของ Hermes แล้วใส่แบรนด์ Northkey กลับเข้าไปใหม่อัตโนมัติ จึงไม่มี merge conflict. บน GitHub มี workflow `northkey-upstream-sync` รันให้ทุก 6 ชั่วโมง
- **ผู้ใช้อัปเดตได้ตามปกติ:** ผู้ใช้พิมพ์ `northkey update` (หรือ `hermes update`) แล้วจะ fast-forward ไปยัง `main` ของ Northkey ได้ทุกครั้ง เพราะ fork นี้ไม่เคย force-push และมี channel record ของตัวเอง
- **ตรวจความถูกต้อง:** `python northkey/tools/nk.py verify` ยืนยันว่าโค้ด = upstream + ไฟล์ของ Northkey + seams เท่านั้น
- **ถ้า sync ล้ม:** แปลว่า upstream แก้บรรทัดที่เราเคยแก้ ให้แก้ `find:` ใน `northkey/seams.yaml` ให้ตรงกับโค้ดใหม่ แล้วรัน `sync` อีกครั้ง (การแก้นี้จะรวมอยู่ใน sync commit เดียวกัน)
- **เปลี่ยนชื่อ/สี/ที่อยู่ repo:** แก้ `northkey/brand.yaml` แล้วรัน `nk.py render`
- **ดีไซน์:** `northkey/skins/northkey.yaml` (terminal/TUI/desktop) และ `northkey/dashboard-themes/northkey.yaml` (web dashboard)

## The one rule: tree = upstream + owned paths + seams

```
upstream commit (northkey/upstream.lock.json)
  + owned paths   northkey/**, tests/northkey/**, hermes_cli/northkey_brand.py,
                  NOTICE, .github/README.md, .github/SECURITY.md, .github/workflows/northkey-*.yml
  + seams         literal find → replace edits in upstream files (northkey/seams.yaml)
```

`nk.py verify` proves this holds; CI runs it on every push. Because seam files are always re-derived from **fresh upstream text**, syncing never produces merge conflicts. When upstream edits a line a seam depends on, the sync stops with the seam's id and leaves the tree untouched.

Never rename Python modules, the `hermes` command, `HERMES_*` variables or `~/.hermes`: the updater's compatibility contract and existing installs depend on them. Branding is user-visible surface only.

## Commands

```bash
python northkey/tools/nk.py status        # base commit and how far upstream has moved
python northkey/tools/nk.py check-next    # would every seam apply to upstream right now?
python northkey/tools/nk.py sync          # merge newest upstream, re-derive the brand, commit
python northkey/tools/nk.py verify        # prove the invariant (add --rev HEAD for a commit)
python northkey/tools/nk.py render        # re-apply seams after editing brand.yaml / seams.yaml
```

`sync` fetches `upstream` (NousResearch/hermes-agent), records a real merge commit (upstream history is kept, nothing is rewritten), rebuilds every non-owned path from the new upstream tree, re-renders the seams, verifies, and commits. It refuses to run with uncommitted edits outside owned paths, and carries uncommitted edits *inside* owned paths (typically a fixed anchor in `seams.yaml`) into the sync commit.

### When a sync fails

```
nk: seam brand-banner: expected 2 match(es) in hermes_cli/banner.py, found 1
```

1. Look at what upstream did: `git log -p upstream/main -- hermes_cli/banner.py`.
2. Update that seam's `find:` (and `count:`) in `northkey/seams.yaml`.
3. Run `nk.py sync` again. The fix rides along in the sync commit.

A seam whose code moved to another file needs its `file:` changed. `tests/northkey/test_update_delivery.py` additionally fails if an upstream refactor re-introduces a Nous update endpoint through a clean merge.

### Adding a seam

Prefer, in order: a setting in `brand.yaml` rendered into an existing seam; a fork-owned file; a new seam. Keep anchors to one line where possible; for multi-line anchors use double-quoted YAML with `\n` so indentation is exact. Then `nk.py render && nk.py verify` and add a test under `tests/northkey/`.

## How updates reach users

1. Upstream → fork: `northkey-upstream-sync` (every 6 h) runs `nk.py sync` and opens a PR. PR CI (`northkey-ci`) verifies and tests it. The PR auto-merges unless upstream touched CI, the updater or the installers (labelled `needs-review`). Always merge with **Create a merge commit**.
2. Fork → installs: `northkey update` resolves the `main` channel from `northkey/release-archive/releases/channels/main.json` (served from this repo; see `channels.base_url` in `brand.yaml`). The record names this repository, so the updater accepts it and fast-forwards the install to `origin/main`. Upstream's own records name NousResearch and are rejected by design, which is why the channel base is a seam.
3. Installs never add Nous as a remote: the fork's URLs are the official origin (`update-official-origin` seam).

Because `main` only ever moves forward through merges, installs always fast-forward. **Never force-push `main`**; `github-setup.sh` enforces this with branch protection.

Tags: `hermes update` derives versions from upstream tags, so mirror them (`git push origin --tags` after a sync). Never create bare `vX.Y.Z` tags in the fork; if you tag Northkey releases, use a different prefix such as `northkey-2026.09.27`.

### Moving an existing Hermes install to Northkey

The first update runs the *old* Hermes updater, which still asks Nous's channel archive, so cross over once with `--branch`:

```bash
cd ~/.hermes/hermes-agent        # Windows: %LOCALAPPDATA%\hermes\hermes-agent
git remote set-url origin https://github.com/zerosec-ai/northkey-agent.git
hermes update --branch main
```

Re-running the Northkey installer (which sets `origin`) works too.

## Publishing checklist

1. Confirm `repo.owner` / `repo.name` in `brand.yaml` (currently `zerosec-ai/northkey-agent`), run `nk.py render`, and update the URLs in `.github/README.md` (a test checks they match).
2. Create the GitHub repository **public** (installs and the channel record are fetched anonymously), then push: `git push origin main && git push origin --tags`.
3. `bash northkey/tools/github-setup.sh`: disables upstream's release/publish workflows, enables private vulnerability reporting, protects `main`, and allows merge commits only.
4. Add the `NORTHKEY_SYNC_TOKEN` secret (fine-grained token: Contents, Pull requests, Workflows = read/write).
5. Close Dependabot PRs: upstream's action bumps arrive through the sync, and `verify` rejects edits outside seams anyway.

The desktop app, Docker image, Nix flake and Termux packages keep upstream's identity and signing and are not published by Northkey yet. Shipping them needs Northkey's own signing identities (Apple Developer ID, Windows code-signing, Store identity) and release storage.

## Design system

| Token | Dark "Nocturne" | Light "Cloud" |
|---|---|---|
| Surface | ink `#0B0D10` | cloud `#F2F1EC` |
| Text | platinum `#E6E8EB` | ink `#14171C` |
| Secondary | pewter `#A3AAB5` | slate `#4A5260` |
| Accent (the one accent) | glacier `#8CCFE0` | deep glacier `#0A6A80` |
| Metallic (mark and titles only) | champagne `#D9C29C` | bronze `#7F6230` |
| OK / warn / error | `#7CC6A0` / `#E3B66B` / `#F0877E` | `#1D7A52` / `#8A5A00` / `#B0342A` |

Rules: one accent, metallic reserved for the ✦ mark; body text stays platinum/ink; no emoji, no exclamation marks; glyphs must be East-Asian-Width *Neutral* (✦ ✧ ⟡ › ╎ ∙ and braille) so they are one cell wide in every locale; motion is slow and stops under reduced-motion. Upstream's palette audit (`tests/hermes_cli/test_skin_palettes.py`) checks the skin's contrast in both polarities, and `tests/northkey/test_brand_surfaces.py` checks glyph widths.

The dot-matrix wordmark and compass hero are generated: `python northkey/design/marks.py` prints the Rich markup, and `--audit` prints every color's contrast ratio.

## Testing on Windows

Use the repo's runner with an explicit interpreter so it never runs `./activate` against a live Hermes home:

```bash
uv sync --frozen --python 3.14 --group dev --group test
HERMES_PYTHON="$PWD/.venv/Scripts/python.exe" bash scripts/run_tests.sh tests/northkey -q
```

Avoid the `*_live`, e2e, install and desktop suites on a workstation; they spawn real processes.
