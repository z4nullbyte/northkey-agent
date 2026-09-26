#!/usr/bin/env python3
"""nk: Northkey fork tooling.

The fork's tree is always ``upstream tree + Northkey-owned paths + seams``
(see northkey/seams.yaml). This tool keeps it that way:

  nk.py render           re-apply every seam to the pristine upstream text
  nk.py verify [--rev]   prove the invariant holds (CI runs this on every push)
  nk.py sync             merge the newest upstream and re-derive the rebrand
  nk.py check-next       dry run: would the seams still apply to upstream now?
  nk.py status           base commit, how far upstream has moved, seam counts

Only the standard library plus a YAML parser (ruamel.yaml or PyYAML) is needed,
so it runs in CI without the project's dependencies.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NK_DIR = ROOT / "northkey"
BRAND_FILE = NK_DIR / "brand.yaml"
SEAMS_FILE = NK_DIR / "seams.yaml"
LOCK_FILE = NK_DIR / "upstream.lock.json"
RECORD_DIR = "northkey/release-archive/releases/channels"
UPSTREAM_REMOTE = "upstream"
PLACEHOLDER = re.compile(r"⟦([A-Za-z0-9_.]+)⟧")
# Brand values are rendered into Python/TS/shell string literals: refuse anything
# that could terminate or escape a literal.
UNSAFE_VALUE = re.compile(r"[\"'`\\\r\n$⟦⟧]")


class NkError(RuntimeError):
    """A failure the maintainer must act on; printed without a traceback."""


# ── plumbing ──────────────────────────────────────────────────────────────────

def git(*args: str, input: bytes | None = None, check: bool = True) -> bytes:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    for attempt in range(40):
        result = subprocess.run(["git", *args], cwd=ROOT, input=input, capture_output=True, env=env)
        # Editors and test runners refresh the index concurrently; wait out a brief index.lock.
        if result.returncode == 0 or b"index.lock" not in result.stderr:
            break
        time.sleep(0.25)
    if check and result.returncode != 0:
        raise NkError(f"git {' '.join(args)} failed:\n{result.stderr.decode('utf-8', 'replace').strip()}")
    return result.stdout


def git_text(*args: str, check: bool = True) -> str:
    return git(*args, check=check).decode("utf-8", "replace").strip()


def load_yaml(path: Path):
    text = path.read_text(encoding="utf-8")
    try:
        from ruamel.yaml import YAML
    except ImportError:
        try:
            import yaml  # PyYAML
        except ImportError as exc:  # pragma: no cover - environment problem
            raise NkError("nk.py needs ruamel.yaml or PyYAML (pip install ruamel.yaml)") from exc
        return yaml.safe_load(text)
    return YAML(typ="safe", pure=True).load(text)


def read_lock() -> dict:
    if not LOCK_FILE.is_file():
        raise NkError(f"{LOCK_FILE.relative_to(ROOT)} is missing; run `nk.py sync --init <commit>` first")
    return json.loads(LOCK_FILE.read_text(encoding="utf-8"))


def write_lock(lock: dict) -> None:
    LOCK_FILE.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8", newline="\n")


# ── brand + manifest ──────────────────────────────────────────────────────────

def _flatten(prefix: str, value, out: dict) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _flatten(f"{prefix}{key}.", item, out)
    else:
        out[prefix[:-1]] = value


def brand_context(brand: dict | None = None) -> dict:
    """Placeholder values: brand.yaml flattened to dotted keys, plus derived URLs."""
    brand = brand if brand is not None else load_yaml(BRAND_FILE)
    ctx: dict = {}
    _flatten("", brand, ctx)
    owner, name, branch = ctx["repo.owner"], ctx["repo.name"], ctx["repo.branch"]
    for key, part in (("repo.owner", owner), ("repo.name", name)):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", str(part)) or part in (".", ".."):
            raise NkError(f"brand.yaml {key}={part!r} is not a valid GitHub name")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]*", str(branch)) or ".." in str(branch):
        raise NkError(f"brand.yaml repo.branch={branch!r} is not a valid branch")
    slug = f"{owner}/{name}"
    raw = f"https://raw.githubusercontent.com/{slug}/{branch}"
    ctx.update({
        "repo.slug": slug,
        "repo.git_url": f"https://github.com/{slug}.git",
        "install.sh_url": f"{raw}/scripts/install.sh",
        "install.ps1_url": f"{raw}/scripts/install.ps1",
        "install.cmd_url": f"{raw}/scripts/install.cmd",
    })
    if not str(ctx["channels.base_url"]).startswith("https://"):
        raise NkError("brand.yaml channels.base_url must be https://")
    for key, value in ctx.items():
        if isinstance(value, str) and UNSAFE_VALUE.search(value):
            raise NkError(f"brand.yaml {key}={value!r} contains a character that is unsafe inside code literals")
    return ctx


def expand(template: str, ctx: dict, where: str) -> str:
    def sub(match: re.Match) -> str:
        key = match.group(1)
        if key not in ctx:
            raise NkError(f"{where}: unknown placeholder ⟦{key}⟧")
        return str(ctx[key])
    return PLACEHOLDER.sub(sub, template)


@dataclass(frozen=True)
class Edit:
    seam: str
    find: str
    replace: str
    count: int


def load_manifest() -> tuple[list[str], dict[str, list[Edit]], dict[str, str]]:
    """(owned globs, {file: edits in manifest order}, {seam id: file})."""
    manifest = load_yaml(SEAMS_FILE)
    if manifest.get("version") != 1:
        raise NkError("seams.yaml: unsupported version")
    owned = list(manifest.get("owned") or [])
    by_file: dict[str, list[Edit]] = {}
    ids: dict[str, str] = {}
    for seam in manifest.get("seams") or []:
        sid, path = seam["id"], seam["file"]
        if sid in ids:
            raise NkError(f"seams.yaml: duplicate seam id {sid}")
        if is_owned(path, owned):
            raise NkError(f"seams.yaml: {sid} targets an owned path {path}")
        ids[sid] = path
        for edit in seam["edits"]:
            by_file.setdefault(path, []).append(
                Edit(sid, edit["find"], edit["replace"], int(edit.get("count", 1))))
    return owned, by_file, ids


def is_owned(path: str, owned: list[str]) -> bool:
    for pattern in owned:
        if pattern.endswith("/**"):
            if path.startswith(pattern[:-2]):
                return True
        elif fnmatch.fnmatchcase(path, pattern):
            return True
    return False


# ── render ────────────────────────────────────────────────────────────────────

def render_text(path: str, pristine: bytes, edits: list[Edit], ctx: dict) -> bytes:
    text = pristine.decode("utf-8")
    for edit in edits:
        found = text.count(edit.find)
        if found != edit.count:
            raise NkError(
                f"seam {edit.seam}: expected {edit.count} match(es) in {path}, found {found}:\n"
                f"    {edit.find!r}\n"
                "  Upstream changed this spot. Update the anchor in northkey/seams.yaml.")
        text = text.replace(edit.find, expand(edit.replace, ctx, f"seam {edit.seam}"))
    return text.encode("utf-8")


def pristine_blob(base: str, path: str) -> tuple[str, bytes]:
    """(mode, bytes) of ``path`` at the upstream base commit."""
    entry = git_text("ls-tree", base, "--", path)
    if not entry:
        raise NkError(f"{path} does not exist in upstream {base[:12]}; a seam needs a new home")
    mode = entry.split()[0]
    return mode, git("cat-file", "blob", f"{base}:{path}")


def channel_records(ctx: dict) -> dict[str, bytes]:
    """Channel records the fork publishes (path -> bytes). Deterministic."""
    record = {
        "schema": 1,
        "name": "main",
        "repository": ctx["repo.slug"],
        "policy": "source-branch",
        "state": "active",
        "revision": 1,
        "nextSequence": 1,
        "identity": None,
        "head": None,
        "delivery": {"kind": "source-branch", "branch": ctx["repo.branch"]},
    }
    body = (json.dumps(record, indent=2) + "\n").encode("utf-8")
    return {f"{RECORD_DIR}/main.json": body}


def render(base: str | None = None, *, write: bool = True) -> dict[str, bytes]:
    """Render every seam file from pristine upstream; stage + check out when ``write``."""
    base = base or read_lock()["commit"]
    ctx = brand_context()
    _, by_file, _ = load_manifest()
    rendered: dict[str, bytes] = {}
    modes: dict[str, str] = {}
    for path, edits in by_file.items():
        mode, pristine = pristine_blob(base, path)
        rendered[path] = render_text(path, pristine, edits, ctx)
        modes[path] = mode
    records = channel_records(ctx)
    if write:
        index_info = []
        for path, body in rendered.items():
            sha = git("hash-object", "-w", "--no-filters", "--stdin", input=body).decode().strip()
            index_info.append(f"{modes[path]} {sha}\t{path}\n")
        if rendered:
            git("update-index", "--add", "--index-info", input="".join(index_info).encode("utf-8"))
            # Materialize from the index so each file gets its .gitattributes line endings.
            git("checkout", "--", *rendered.keys())
        for path, body in records.items():
            target = ROOT / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            git("add", "--", path)
    return {**rendered, **records}


# ── verify ────────────────────────────────────────────────────────────────────

def _blob_at(rev: str | None, path: str) -> bytes | None:
    spec = f":{path}" if rev is None else f"{rev}:{path}"
    result = subprocess.run(["git", "cat-file", "blob", spec], cwd=ROOT, capture_output=True)
    return result.stdout if result.returncode == 0 else None


def verify(rev: str | None = None) -> list[str]:
    """Problems with the invariant at ``rev`` (None = the index). Empty list = healthy."""
    problems: list[str] = []
    lock = read_lock()
    base = lock["commit"]
    owned, by_file, _ = load_manifest()
    ctx = brand_context()

    if subprocess.run(["git", "cat-file", "-e", f"{base}^{{commit}}"], cwd=ROOT).returncode != 0:
        return [f"upstream base {base} is not present locally; fetch upstream"]
    if rev and subprocess.run(["git", "merge-base", "--is-ancestor", base, rev], cwd=ROOT).returncode != 0:
        problems.append(f"{rev} does not contain upstream base {base[:12]} (history must merge, never replace)")

    diff_args = ["diff", "--name-status", "--no-renames", base]
    diff_args += [rev] if rev else ["--cached"]
    for line in git_text(*diff_args).splitlines():
        status, path = line.split("\t", 1)
        if is_owned(path, owned):
            if _blob_at(base, path) is not None:
                problems.append(f"{path}: upstream now ships an owned path; move the Northkey file")
            continue
        if path not in by_file:
            problems.append(f"{path}: edited outside the seam manifest ({status}); make it a seam or revert it")

    for path, edits in by_file.items():
        try:
            _, pristine = pristine_blob(base, path)
            expected = render_text(path, pristine, edits, ctx)
        except NkError as exc:
            problems.append(str(exc))
            continue
        actual = _blob_at(rev, path)
        if actual != expected:
            problems.append(f"{path}: differs from its rendered seams; run `nk.py render`")

    for path, body in channel_records(ctx).items():
        if _blob_at(rev, path) != body:
            problems.append(f"{path}: channel record is stale; run `nk.py render`")
    return problems


# ── sync ──────────────────────────────────────────────────────────────────────

def pending_owned_changes(owned: list[str]) -> list[str]:
    """Uncommitted paths, all inside Northkey-owned paths (they ride along in the sync commit).

    Editing northkey/seams.yaml and running `sync` in one go is how a moved anchor gets
    fixed without an intermediate commit that fails `verify`.
    """
    if (ROOT / ".git" / "MERGE_HEAD").exists():
        raise NkError("a merge is already in progress")
    entries = git("status", "--porcelain", "-z", "--untracked-files=all").decode("utf-8").split("\0")
    dirty: list[str] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        dirty.append(entry[3:])
        if entry[0] in "RC":  # rename/copy: the next field is the source path
            dirty.append(entries[index])
            index += 1
    foreign = [path for path in dirty if not is_owned(path, owned)]
    if foreign:
        raise NkError("uncommitted changes outside Northkey-owned paths; commit or stash them first:\n  "
                      + "\n  ".join(foreign[:20]))
    return dirty


def _set_aside(paths: list[str]) -> dict[str, bytes | None]:
    """Remember pending owned files and reset them to HEAD so git will merge."""
    saved = {path: (ROOT / path).read_bytes() if (ROOT / path).is_file() else None for path in paths}
    for path in paths:
        if _blob_at("HEAD", path) is not None:
            git("checkout", "HEAD", "--", path)
        else:
            git("rm", "-q", "--cached", "--ignore-unmatch", "--", path)
            (ROOT / path).unlink(missing_ok=True)
    return saved


def _restore(saved: dict[str, bytes | None], *, stage: bool) -> None:
    for path, data in saved.items():
        target = ROOT / path
        if data is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        if stage:
            git("add", "-A", "--", path)


def ensure_upstream_remote(url: str) -> None:
    current = git_text("remote", "get-url", UPSTREAM_REMOTE, check=False)
    if not current:
        git("remote", "add", UPSTREAM_REMOTE, url)
        git("remote", "set-url", "--push", UPSTREAM_REMOTE, "DISABLED-northkey-never-pushes-upstream")
    elif current.rstrip("/").removesuffix(".git") != url.rstrip("/").removesuffix(".git"):
        raise NkError(f"remote {UPSTREAM_REMOTE} points at {current}, expected {url}")


def fetch_upstream(ref: str) -> str:
    brand = load_yaml(BRAND_FILE)
    ensure_upstream_remote(brand["upstream"]["url"])
    git("fetch", "--quiet", "--tags", UPSTREAM_REMOTE, f"+refs/heads/{ref}:refs/remotes/{UPSTREAM_REMOTE}/{ref}")
    return git_text("rev-parse", f"refs/remotes/{UPSTREAM_REMOTE}/{ref}")


def rebuild_tree(new: str, owned: list[str]) -> None:
    """Index + worktree := upstream ``new`` for every non-owned path."""
    upstream_paths = set(git_text("ls-tree", "-r", "--name-only", new).splitlines())
    collisions = sorted(p for p in upstream_paths if is_owned(p, owned))
    if collisions:
        raise NkError("upstream now ships Northkey-owned paths:\n  " + "\n  ".join(collisions[:20]))
    stale = [p for p in git_text("ls-files").splitlines()
             if not is_owned(p, owned) and p not in upstream_paths]
    for start in range(0, len(stale), 200):
        git("rm", "-q", "-f", "--", *stale[start:start + 200])
    git("checkout", new, "--", ".")


def sync(ref: str, commit: str | None, *, fetch: bool, allow_rewrite: bool) -> int:
    owned, _, _ = load_manifest()
    pending = pending_owned_changes(owned)
    lock = read_lock()
    new = fetch_upstream(ref) if fetch else git_text("rev-parse", f"refs/remotes/{UPSTREAM_REMOTE}/{ref}")
    if commit:
        new = git_text("rev-parse", f"{commit}^{{commit}}")
    old = lock["commit"]
    if new == old:
        print(f"Already on upstream {ref} @ {new[:12]}.")
        return 0
    if subprocess.run(["git", "merge-base", "--is-ancestor", old, new], cwd=ROOT).returncode != 0:
        if not allow_rewrite:
            raise NkError(f"upstream {new[:12]} does not descend from {old[:12]} (history rewritten?); "
                          "re-run with --allow-rewrite after review")

    # Dry-render first (with any pending seams.yaml edit): a moved anchor aborts
    # before the tree is touched.
    render_check(new)

    head = git_text("rev-parse", "HEAD")
    count = git_text("rev-list", "--count", f"{old}..{new}")
    message = (f"northkey: sync upstream {ref} @ {new[:12]}\n\n"
               f"Merges {count} upstream commit(s) {old[:12]}..{new[:12]} and re-derives the rebrand\n"
               "from northkey/seams.yaml (tree = upstream + owned paths + seams).")
    saved = _set_aside(pending)
    try:
        git("merge", "--no-ff", "--no-commit", "-s", "ours", new)
        rebuild_tree(new, owned)
        _restore(saved, stage=True)
        write_lock({**lock, "ref": ref, "commit": new, "previous": old})
        git("add", "--", LOCK_FILE.relative_to(ROOT).as_posix())
        render(new)
        problems = verify(None)
        if problems:
            raise NkError("invariant failed after sync:\n  " + "\n  ".join(problems))
        git("commit", "--quiet", "--no-verify", "-m", message)
    except BaseException:
        git("merge", "--abort", check=False)
        git("reset", "--quiet", "--hard", head, check=False)
        _restore(saved, stage=False)  # never lose the maintainer's pending edits
        raise
    print(f"Synced upstream {ref}: {old[:12]} -> {new[:12]} ({count} commits). "
          f"HEAD {git_text('rev-parse', 'HEAD')[:12]}")
    return 0


def render_check(commit: str) -> None:
    ctx = brand_context()
    _, by_file, _ = load_manifest()
    for path, edits in by_file.items():
        _, pristine = pristine_blob(commit, path)
        render_text(path, pristine, edits, ctx)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # ✓/✗ on legacy Windows consoles
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(prog="nk.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("render", help="re-apply all seams to the pristine upstream text")
    p_verify = sub.add_parser("verify", help="check the fork invariant")
    p_verify.add_argument("--rev", help="commit to verify (default: the index)")
    p_sync = sub.add_parser("sync", help="merge the newest upstream and re-derive the rebrand")
    p_sync.add_argument("--ref", default=None, help="upstream branch (default: brand.yaml upstream.ref)")
    p_sync.add_argument("--commit", help="sync to this upstream commit instead of the branch tip")
    p_sync.add_argument("--no-fetch", action="store_true")
    p_sync.add_argument("--allow-rewrite", action="store_true")
    p_sync.add_argument("--init", metavar="COMMIT", help="bootstrap the lock on this upstream commit")
    p_next = sub.add_parser("check-next", help="would the seams apply to the newest upstream?")
    p_next.add_argument("--ref", default=None)
    sub.add_parser("status", help="show the fork's position relative to upstream")
    args = parser.parse_args(argv)

    try:
        brand = load_yaml(BRAND_FILE)
        ref = getattr(args, "ref", None) or brand["upstream"]["ref"]
        if args.cmd == "render":
            written = render()
            print(f"Rendered {len(written)} file(s) from upstream {read_lock()['commit'][:12]}.")
            return 0
        if args.cmd == "verify":
            problems = verify(args.rev)
            for problem in problems:
                print(f"✗ {problem}")
            if problems:
                return 1
            _, by_file, ids = load_manifest()
            print(f"✓ fork invariant holds: {len(ids)} seams in {len(by_file)} upstream files, base {read_lock()['commit'][:12]}")
            return 0
        if args.cmd == "sync":
            if args.init:
                commit = git_text("rev-parse", f"{args.init}^{{commit}}")
                write_lock({"remote": brand["upstream"]["url"], "ref": ref, "commit": commit})
                print(f"Initialized upstream lock at {commit[:12]}.")
                return 0
            return sync(ref, args.commit, fetch=not args.no_fetch, allow_rewrite=args.allow_rewrite)
        if args.cmd == "check-next":
            new = fetch_upstream(ref)
            render_check(new)
            behind = git_text("rev-list", "--count", f"{read_lock()['commit']}..{new}")
            print(f"✓ all seams apply cleanly to upstream {ref} @ {new[:12]} ({behind} commits ahead of the fork)")
            return 0
        if args.cmd == "status":
            lock = read_lock()
            _, by_file, ids = load_manifest()
            tip = git_text("rev-parse", "--verify", "-q", f"refs/remotes/{UPSTREAM_REMOTE}/{ref}", check=False)
            behind = git_text("rev-list", "--count", f"{lock['commit']}..{tip}") if tip else "?"
            print(f"upstream base : {lock['commit']} ({lock.get('ref')})")
            print(f"upstream tip  : {tip or 'unknown (fetch first)'}  [{behind} commits not yet synced]")
            print(f"seams         : {len(ids)} in {len(by_file)} files")
            return 0
    except NkError as exc:
        print(f"nk: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
