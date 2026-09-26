# Northkey maintainer guide

Northkey is a rebranded distribution of [Hermes Agent](https://github.com/NousResearch/hermes-agent) (MIT, Nous Research) that keeps receiving upstream updates for as long as upstream exists. This folder holds everything that makes Northkey *Northkey*.

## สรุปภาษาไทย (Quick guide)

- **อัปเดตจาก upstream (Hermes) ได้ตลอด:** รัน `python northkey/tools/nk.py sync`. คำสั่งนี้ merge โค้ดล่าสุดของ Hermes แล้วใส่แบรนด์ Northkey กลับเข้าไปใหม่อัตโนมัติ จึงไม่มี merge conflict. บน GitHub มี workflow `northkey-upstream-sync` รันให้ทุก 6 ชั่วโมง และ merge เองเมื่อ CI ผ่าน
- **ผู้ใช้อัปเดตได้ตามปกติ:** ผู้ใช้พิมพ์ `northkey update` (หรือ `hermes update`) แล้วจะ fast-forward ไปยัง `main` ของ Northkey เสมอ โดยไม่ต้องพึ่ง server ของ Nous (ช่อง main/stable/canary ชี้ไปที่ branch ของ fork โดยตรง) และ fork นี้ไม่เคย force-push
- **ตรวจความถูกต้อง:** `python northkey/tools/nk.py verify` ยืนยันว่าโค้ด = upstream + ไฟล์ของ Northkey + seams เท่านั้น (เทียบทุกไฟล์ ทุก mode)
- **ถ้า sync หยุด:** แปลว่า upstream แก้บรรทัดที่ seam แบบ *required* ใช้อยู่ ให้แก้ `find:` ใน `northkey/seams.yaml` แล้วรัน `sync` อีกครั้ง (การแก้จะรวมอยู่ใน sync commit เดียวกัน). Seam ด้านความสวยงาม (`required: false`) จะไม่หยุด sync แต่ถูกข้ามและแจ้งใน commit
- **เปลี่ยนชื่อ/สี/ที่อยู่ repo:** แก้ `northkey/brand.yaml` แล้วรัน `nk.py render`
- **ดีไซน์:** `northkey/skins/northkey.yaml` (terminal/TUI/desktop) และ `northkey/dashboard-themes/northkey.yaml` (web dashboard)

## The one rule: tree = upstream + owned paths + seams

```
upstream commit (northkey/upstream.lock.json)
  + owned paths   northkey/**, tests/northkey/**, hermes_cli/northkey_brand.py,
                  NOTICE, .github/README.md, .github/SECURITY.md, .github/workflows/northkey-*.yml
  + seams         literal find → replace edits in upstream files (northkey/seams.yaml)
```

`nk.py verify` compares the whole tree (every path, content and file mode) against that recipe; CI runs it on every push and it is a required check. Because seam files are always re-derived from **fresh upstream text**, syncing never produces merge conflicts.

Seams come in two kinds:

- **required** (update path, installers, security defaults, identity, attribution): a moved anchor stops the sync with the seam's id, so these can never silently regress.
- **optional** (`required: false`, cosmetic surfaces): a moved anchor skips that seam, the sync continues, and the commit message lists what was skipped. Cosmetics can never hold back an update.

Never rename Python modules, the `hermes` command, `HERMES_*` variables or `~/.hermes`: the updater's compatibility contract and existing installs depend on them. `northkey` is an alias that runs the same `hermes` launcher, so every process keeps one identity.

## Commands

```bash
python northkey/tools/nk.py status        # fetches upstream; base commit and how far upstream has moved
python northkey/tools/nk.py check-next    # would every required seam apply to upstream right now?
python northkey/tools/nk.py sync          # merge newest upstream, re-derive the brand, fast-forward main
python northkey/tools/nk.py verify        # prove the invariant (--rev <commit> checks a commit with its own manifest)
python northkey/tools/nk.py render        # re-apply seams after editing brand.yaml / seams.yaml
python northkey/tools/nk.py leaks         # upstream brand strings still visible on primary surfaces
python northkey/tools/nk.py pins          # Northkey workflow action pins upstream has moved past
```

How `sync` works: it fetches `upstream` (NousResearch/hermes-agent; upstream tags go to `refs/upstream-tags/`, never the fork's tags), builds the merge commit in a private index (upstream tree + owned files + rendered seams), verifies that commit, and only then fast-forwards `main`. Nothing in your working tree changes until a verified commit exists, and no merge state is ever left behind. It refuses to run with uncommitted edits outside owned paths or with committed drift (edits to upstream files outside the seams; `--discard-drift` drops them deliberately). Uncommitted edits to tracked owned files, typically a fixed anchor in `seams.yaml`, ride along in the sync commit; untracked files are never committed.

### When a sync stops

```
nk: seam update-channel-authority: expected 1 match(es) in hermes_cli/source_releases.py, found 0
```

1. See what upstream did: `git log -p upstream/main -- hermes_cli/source_releases.py`.
2. Update that seam's `find:` (and `count:`) in `northkey/seams.yaml`; a seam whose code moved to another file needs its `file:` changed.
3. Run `nk.py sync` again. The fix rides along in the sync commit.

`tests/northkey/test_update_delivery.py` additionally fails if an upstream refactor re-introduces a Nous update endpoint through a clean merge.

### Adding a seam

Prefer, in order: a value in `brand.yaml` rendered into an existing seam; a fork-owned file; a new seam. Keep anchors to one line where possible; multi-line anchors use double-quoted YAML with `\n` so indentation is exact. Mark cosmetic seams `required: false`. Then `nk.py render && nk.py verify` and add a test under `tests/northkey/`.

## How updates reach users

1. **Upstream → fork.** `northkey-upstream-sync` (every 6 h) runs `nk.py sync`, mirrors upstream release tags, and opens a PR. Northkey CI verifies and tests it; the PR auto-merges with a merge commit. Only a sync that *adds* upstream workflow files waits for a human (label `needs-review`), because a new upstream publisher could otherwise run in the fork.
2. **Fork → installs.** `northkey update` resolves the `main`, `stable` and `canary` channels to the fork's `main` branch directly (`update-channel-authority` seam): no release archive is consulted, so no Nous server and no rate-limited host can block an update. A `stable`/`canary` subscription carried over from Hermes is reported as retired and moved to `main` after the next successful update. The install then fast-forwards to `origin/main`.
3. **Installs never follow Nous.** The fork's URLs are the official origin (`update-official-origin`), `update --check` compares against origin rather than any `upstream` remote, and every recovery hint reinstalls Northkey.

Because `main` only moves forward through merges, installs always fast-forward. **Never force-push `main`**; `github-setup.sh` enforces this with branch protection. Never create bare `vX.Y.Z` tags in the fork (the version code takes the highest one); if you tag Northkey releases, use a prefix such as `northkey-2026.09.27`.

### Moving an existing Hermes install to Northkey

Re-run the Northkey installer. It recognises a checkout whose origin is NousResearch/hermes-agent, re-points it at Northkey and updates it (the `installer-origin-*` seams):

```bash
curl -fsSL https://raw.githubusercontent.com/z4nullbyte/northkey-agent/main/scripts/install.sh | bash
# Windows PowerShell:
iex (irm https://raw.githubusercontent.com/z4nullbyte/northkey-agent/main/scripts/install.ps1)
```

Manual alternative (the first update still runs Hermes' own updater, hence `--branch main --yes`):

```bash
cd ~/.hermes/hermes-agent        # Windows: %LOCALAPPDATA%\hermes\hermes-agent
git remote set-url origin https://github.com/z4nullbyte/northkey-agent.git
hermes update --branch main --yes
git remote remove upstream 2>/dev/null || true
```

An untouched SOUL.md seeded by Hermes is upgraded to the Northkey identity automatically; a customised one is left alone.

## Publishing checklist

1. Confirm `repo.owner` / `repo.name` in `brand.yaml` (currently `z4nullbyte/northkey-agent`), run `nk.py render`, and update the URLs in `.github/README.md` (a test checks they match).
2. Create the GitHub repository **public** (installs clone it anonymously), then push: `git push origin main` and mirror upstream tags: `git push origin $(python northkey/tools/nk.py tag-refspecs)`.
3. `bash northkey/tools/github-setup.sh z4nullbyte/northkey-agent`: disables upstream's publishers, creates the `upstream-sync` environment, makes the default workflow token read-only, protects `main` (both CI jobs required), allows merge commits only, enables private vulnerability reporting.
4. `gh secret set NORTHKEY_SYNC_TOKEN --env upstream-sync --repo z4nullbyte/northkey-agent` with a fine-grained token (Contents, Pull requests, Workflows, Actions = read/write).
5. Close Dependabot PRs: upstream's action bumps arrive through the sync, and `nk.py pins` (in the CI report) tells you when the fork's own workflows should follow.

The desktop app, Docker image, Nix flake and Termux packages keep upstream's identity and signing and are not published by Northkey yet. Shipping them needs Northkey's own signing identities (Apple Developer ID, Windows code-signing, Store identity) and release storage, plus seams for their download URLs (`nk.py leaks` and `test_update_delivery.py` list where).

## Upstream tests Northkey changes on purpose

`northkey/expected-divergence.txt` lists upstream test ids that pin a value Northkey changes deliberately (for example the `smart` approval default or Nous channel fixtures). CI deselects exactly those ids and re-asserts the contracts with Northkey's values in `tests/northkey/`. A test checks that every listed id still exists.

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
