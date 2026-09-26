#!/usr/bin/env python3
"""nk: Northkey fork tooling.

The fork's tree is always ``upstream tree + Northkey-owned paths + seams``
(see northkey/seams.yaml). This tool keeps it that way:

  nk.py render            re-apply every seam to the pristine upstream text
  nk.py verify [--rev R]  prove the invariant (exact tree comparison, modes included)
  nk.py sync              merge the newest upstream and re-derive the rebrand
  nk.py check-next        dry run: would the seams apply to upstream right now?
  nk.py status            base commit, how far upstream has moved, seam counts
  nk.py leaks             upstream brand strings still visible on primary surfaces
  nk.py pins              Northkey workflow action pins that upstream has moved past
  nk.py tag-refspecs      upstream release tags to mirror into the fork

`sync` builds the merge commit in a private index (upstream tree + owned files +
rendered seams), verifies that commit, and only then fast-forwards the branch.
The working tree is untouched until a verified commit exists, and uncommitted
edits to tracked owned files (for example a fixed anchor in seams.yaml) ride
along in the sync commit.

Only the standard library plus a YAML parser (ruamel.yaml or PyYAML) is needed,
so it runs in CI without the project's dependencies.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BRAND_PATH = "northkey/brand.yaml"
SEAMS_PATH = "northkey/seams.yaml"
LOCK_PATH = "northkey/upstream.lock.json"
UPSTREAM_REMOTE = "upstream"
UPSTREAM_TAGS = "refs/upstream-tags"
PLACEHOLDER = re.compile(r"⟦([A-Za-z0-9_.]+)⟧")
# Brand values are rendered into Python/TS/shell string literals: refuse anything
# that could terminate or escape a literal.
UNSAFE_VALUE = re.compile(r"[\"'`\\\r\n$⟦⟧]")
IN_PROGRESS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "BISECT_LOG")


class NkError(RuntimeError):
    """A failure the maintainer must act on; printed without a traceback."""


# ── git plumbing ──────────────────────────────────────────────────────────────

def git(*args: str, input: bytes | None = None, check: bool = True, env: dict | None = None) -> bytes:
    full_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", "GIT_LITERAL_PATHSPECS": "1",
                **(env or {})}
    for _ in range(40):
        result = subprocess.run(["git", *args], cwd=ROOT, input=input, capture_output=True, env=full_env)
        # Editors and test runners refresh the index concurrently; wait out a brief index.lock.
        if result.returncode == 0 or b"index.lock" not in result.stderr:
            break
        time.sleep(0.25)
    if check and result.returncode != 0:
        raise NkError(f"git {' '.join(args)} failed:\n{result.stderr.decode('utf-8', 'replace').strip()}")
    return result.stdout


def git_text(*args: str, check: bool = True, env: dict | None = None, input: bytes | None = None) -> str:
    return git(*args, check=check, env=env, input=input).decode("utf-8", "replace").strip()


def git_ok(*args: str) -> bool:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}).returncode == 0


def git_path(name: str) -> Path:
    return ROOT / git_text("rev-parse", "--git-path", name)


def ls_tree(rev: str) -> dict[str, tuple[str, str]]:
    """{path: (mode, object id)} for every blob/link/gitlink in ``rev``."""
    entries = {}
    for record in git("ls-tree", "-r", "-z", "--full-tree", rev).split(b"\0"):
        if record:
            meta, path = record.split(b"\t", 1)
            mode, _kind, oid = meta.decode().split(" ")
            entries[path.decode("utf-8")] = (mode, oid)
    return entries


def index_entries(env: dict | None = None) -> dict[str, tuple[str, str]]:
    entries = {}
    for record in git("ls-files", "-s", "-z", env=env).split(b"\0"):
        if record:
            meta, path = record.split(b"\t", 1)
            mode, oid, _stage = meta.decode().split(" ")
            entries[path.decode("utf-8")] = (mode, oid)
    return entries


_OBJECT_FORMAT: list[str] = []


def blob_id(data: bytes) -> str:
    if not _OBJECT_FORMAT:
        _OBJECT_FORMAT.append(git_text("rev-parse", "--show-object-format"))
    algo = hashlib.sha256 if _OBJECT_FORMAT[0] == "sha256" else hashlib.sha1
    return algo(b"blob %d\0" % len(data) + data).hexdigest()


def write_blob(data: bytes) -> str:
    return git("hash-object", "-w", "--no-filters", "--stdin", input=data).decode().strip()


def index_info(entries: dict[str, tuple[str, str]]) -> bytes:
    return b"".join(f"{mode} {oid}\t{path}".encode("utf-8") + b"\0" for path, (mode, oid) in entries.items())


def is_ancestor(older: str, newer: str) -> bool:
    return git_ok("merge-base", "--is-ancestor", older, newer)


# ── files, brand and manifest (read from a revision or the working tree) ─────

class Source:
    """Where the manifest, brand and lock are read from: a revision, or the working tree."""

    def __init__(self, rev: str | None = None):
        self.rev = rev

    def read(self, path: str) -> bytes:
        if self.rev is None:
            return (ROOT / path).read_bytes()
        return git("cat-file", "blob", f"{self.rev}:{path}")


def load_yaml(data: bytes):
    text = data.decode("utf-8-sig")
    try:
        from ruamel.yaml import YAML
    except ImportError:
        try:
            import yaml  # PyYAML
        except ImportError as exc:  # pragma: no cover - environment problem
            raise NkError("nk.py needs ruamel.yaml or PyYAML (pip install ruamel.yaml)") from exc
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise NkError(f"invalid YAML: {exc}") from exc
    try:
        return YAML(typ="safe", pure=True).load(text)
    except Exception as exc:  # ruamel's error hierarchy varies by version
        raise NkError(f"invalid YAML: {exc}") from exc


def read_lock(src: Source | None = None) -> dict:
    src = src or Source()
    try:
        return json.loads(src.read(LOCK_PATH))
    except (FileNotFoundError, NkError) as exc:
        raise NkError(f"{LOCK_PATH} is missing; run `nk.py sync --init <commit>` first") from exc


def lock_bytes(lock: dict) -> bytes:
    return (json.dumps(lock, indent=2) + "\n").encode("utf-8")


def _flatten(prefix: str, value, out: dict) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _flatten(f"{prefix}{key}.", item, out)
    else:
        out[prefix[:-1]] = value


def brand_context(brand: dict) -> dict:
    """Placeholder values: brand.yaml flattened to dotted keys, plus derived URLs."""
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
    find: str
    replace: str
    count: int | None  # None = every occurrence (at least one)


@dataclass(frozen=True)
class Seam:
    id: str
    file: str
    required: bool
    edits: tuple[Edit, ...]


@dataclass
class Manifest:
    owned: list[str]
    seams: list[Seam]
    surfaces: list[str] = field(default_factory=list)

    @property
    def by_file(self) -> dict[str, list[Seam]]:
        grouped: dict[str, list[Seam]] = {}
        for seam in self.seams:
            grouped.setdefault(seam.file, []).append(seam)
        return grouped


def load_manifest(src: Source | None = None) -> Manifest:
    data = load_yaml((src or Source()).read(SEAMS_PATH))
    if data.get("version") != 1:
        raise NkError("seams.yaml: unsupported version")
    owned = list(data.get("owned") or [])
    seams, ids = [], set()
    for raw in data.get("seams") or []:
        sid, path = raw["id"], raw["file"]
        if sid in ids:
            raise NkError(f"seams.yaml: duplicate seam id {sid}")
        if is_owned(path, owned):
            raise NkError(f"seams.yaml: {sid} targets an owned path {path}")
        ids.add(sid)
        edits = []
        for edit in raw["edits"]:
            count = edit.get("count", 1)
            edits.append(Edit(edit["find"], edit["replace"], None if count == "all" else int(count)))
        seams.append(Seam(sid, path, bool(raw.get("required", True)), tuple(edits)))
    return Manifest(owned, seams, list(data.get("surfaces") or []))


def is_owned(path: str, owned: list[str]) -> bool:
    for pattern in owned:
        if pattern.endswith("/**"):
            if path.startswith(pattern[:-2]):
                return True
        elif fnmatch.fnmatchcase(path, pattern):
            return True
    return False


# ── render ────────────────────────────────────────────────────────────────────

def apply_seams(path: str, pristine: bytes, seams: list[Seam], ctx: dict) -> tuple[bytes, list[str]]:
    """Render ``path`` from pristine bytes. Returns (bytes, ids of optional seams skipped).

    Rendering is byte-level (any encoding, any line endings) and all-or-nothing per
    seam: a required seam whose anchor moved raises; an optional one is skipped whole.
    """
    data, skipped = pristine, []
    for seam in seams:
        attempt, problem = data, None
        for edit in seam.edits:
            find = edit.find.encode("utf-8")
            found = attempt.count(find)
            if (edit.count is None and found == 0) or (edit.count is not None and found != edit.count):
                problem = (f"seam {seam.id}: expected {'at least 1' if edit.count is None else edit.count} "
                           f"match(es) in {path}, found {found}:\n    {edit.find!r}")
                break
            attempt = attempt.replace(find, expand(edit.replace, ctx, f"seam {seam.id}").encode("utf-8"))
        if problem is None:
            data = attempt
        elif seam.required:
            raise NkError(problem + "\n  Upstream changed this spot. Update the anchor in northkey/seams.yaml.")
        else:
            skipped.append(seam.id)
    return data, skipped


def render_tree(base: str, manifest: Manifest, ctx: dict,
                tree: dict[str, tuple[str, str]] | None = None) -> tuple[dict[str, tuple[str, bytes]], list[str]]:
    """{seam file: (mode, rendered bytes)} from upstream ``base``, plus skipped optional seams."""
    tree = tree if tree is not None else ls_tree(base)
    rendered, skipped = {}, []
    for path, seams in manifest.by_file.items():
        if path not in tree:
            missing = [s.id for s in seams if s.required]
            if missing:
                raise NkError(f"{path} does not exist in upstream {base[:12]} (seams {', '.join(missing)}); "
                              "move them to the file's new home")
            skipped.extend(s.id for s in seams)
            continue
        mode, oid = tree[path]
        data, skip = apply_seams(path, git("cat-file", "blob", oid), seams, ctx)
        rendered[path] = (mode, data)
        skipped.extend(skip)
    return rendered, skipped


def render(*, force: bool = False) -> tuple[int, list[str]]:
    """Re-apply the seams from the working tree's manifest onto the index and working tree."""
    src = Source()
    lock, manifest = read_lock(src), load_manifest(src)
    rendered, skipped = render_tree(lock["commit"], manifest, brand_context(load_yaml(src.read(BRAND_PATH))))
    paths = list(rendered)
    state_file = git_path("northkey-rendered.json")
    try:
        last_rendered = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last_rendered = {}
    if not force and paths:
        head = ls_tree("HEAD")
        index = index_entries()
        worktree = git("hash-object", "--stdin-paths", input="\n".join(paths).encode("utf-8")).decode().split()
        dirty = []
        for path, wt_oid in zip(paths, worktree):
            # Content nk produced (now, at HEAD, or on the last render) is not a hand edit.
            known = {blob_id(rendered[path][1]), head.get(path, (None, None))[1], last_rendered.get(path)}
            if wt_oid not in known or index.get(path, (None, None))[1] not in known:
                dirty.append(path)
        if dirty:
            raise NkError("uncommitted edits in seam files would be overwritten (use --force to discard):\n  "
                          + "\n  ".join(dirty))
    entries = {path: (mode, write_blob(data)) for path, (mode, data) in rendered.items()}
    # Files whose seams were removed since HEAD go back to pristine upstream.
    try:
        previous = set(load_manifest(Source("HEAD")).by_file)
    except NkError:
        previous = set()
    base_tree = ls_tree(lock["commit"])
    for path in sorted(previous - set(rendered)):
        if path in base_tree:
            entries[path] = base_tree[path]
    if entries:
        git("update-index", "-z", "--add", "--index-info", input=index_info(entries))
        # Materialize from the index so each file gets its .gitattributes line endings.
        git("checkout", "--", *entries)
    state_file.write_text(json.dumps({path: oid for path, (_mode, oid) in entries.items()}), encoding="utf-8")
    return len(entries), skipped


# ── verify ────────────────────────────────────────────────────────────────────

def verify(rev: str | None = None) -> tuple[list[str], list[str]]:
    """(problems, skipped optional seams) for ``rev``; rev=None checks the index against
    the working tree's manifest. The comparison is exact: every path, content and mode."""
    src = Source(rev)
    lock = read_lock(src)
    base = lock["commit"]
    if not git_ok("cat-file", "-e", f"{base}^{{commit}}"):
        return [f"upstream base {base} is not present locally; fetch upstream"], []
    manifest = load_manifest(src)
    problems: list[str] = []
    if rev and not is_ancestor(base, rev):
        problems.append(f"{rev} does not contain upstream base {base[:12]} (history must merge, never replace)")
    base_tree = ls_tree(base)
    try:
        rendered, skipped = render_tree(base, manifest, brand_context(load_yaml(src.read(BRAND_PATH))), base_tree)
    except NkError as exc:
        return problems + [str(exc)], []
    expected = dict(base_tree)
    expected.update({path: (mode, blob_id(data)) for path, (mode, data) in rendered.items()})
    target = ls_tree(rev) if rev else index_entries()
    for path, (mode, oid) in target.items():
        if is_owned(path, manifest.owned):
            if path in base_tree:
                problems.append(f"{path}: upstream now ships an owned path; move the Northkey file")
            continue
        want = expected.get(path)
        if want is None:
            problems.append(f"{path}: added outside the seam manifest; make it owned or remove it")
        elif want != (mode, oid):
            what = "mode" if want[1] == oid else "content"
            origin = "its rendered seams" if path in rendered else "upstream"
            problems.append(f"{path}: {what} differs from {origin}; "
                            f"{'run `nk.py render`' if path in rendered else 'make it a seam or revert it'}")
    for path in base_tree:
        if path not in target and not is_owned(path, manifest.owned):
            problems.append(f"{path}: deleted, but upstream still ships it")
    return problems, skipped


# ── sync ──────────────────────────────────────────────────────────────────────

def ensure_quiet_repository() -> None:
    busy = [name for name in IN_PROGRESS if git_path(name).exists()]
    if busy:
        raise NkError(f"a git operation is in progress ({', '.join(busy)}); finish or abort it first")
    if git("ls-files", "-u", "-z").strip(b"\0"):
        raise NkError("the index has unmerged entries; resolve them first")


def pending_owned_changes(owned: list[str]) -> list[str]:
    """Tracked, uncommitted paths; all must be Northkey-owned. Untracked files are never carried."""
    entries = git("status", "--porcelain=v1", "-z", "--untracked-files=no").decode("utf-8").split("\0")
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


def ensure_upstream_remote(url: str) -> None:
    current = git_text("remote", "get-url", UPSTREAM_REMOTE, check=False)
    if not current:
        git("remote", "add", UPSTREAM_REMOTE, url)
        git("remote", "set-url", "--push", UPSTREAM_REMOTE, "DISABLED-northkey-never-pushes-upstream")
    elif current.rstrip("/").removesuffix(".git") != url.rstrip("/").removesuffix(".git"):
        raise NkError(f"remote {UPSTREAM_REMOTE} points at {current}, expected {url}")


def fetch_upstream(ref: str) -> str:
    """Fetch upstream ``ref``; upstream tags land in refs/upstream-tags/ (never the fork's tags)."""
    brand = load_yaml(Source().read(BRAND_PATH))
    ensure_upstream_remote(brand["upstream"]["url"])
    git("fetch", "--no-tags", UPSTREAM_REMOTE,
        f"+refs/heads/{ref}:refs/remotes/{UPSTREAM_REMOTE}/{ref}", f"+refs/tags/*:{UPSTREAM_TAGS}/*")
    return git_text("rev-parse", f"refs/remotes/{UPSTREAM_REMOTE}/{ref}")


def sync(ref: str, commit: str | None, *, fetch: bool, allow_rewrite: bool, discard_drift: bool) -> int:
    ensure_quiet_repository()
    src = Source()
    manifest = load_manifest(src)
    pending = pending_owned_changes(manifest.owned)
    head = git_text("rev-parse", "HEAD")
    drift, _ = verify(head)
    if drift and not discard_drift:
        raise NkError("HEAD already breaks the fork invariant, so a sync would silently drop these edits:\n  "
                      + "\n  ".join(drift[:20])
                      + "\n  Turn them into seams first, or pass --discard-drift to drop them deliberately.")
    lock = read_lock(src)
    new = fetch_upstream(ref) if fetch else git_text("rev-parse", f"refs/remotes/{UPSTREAM_REMOTE}/{ref}")
    if commit:
        new = git_text("rev-parse", f"{commit}^{{commit}}")
    base = lock["commit"]
    if new == base:
        print(f"Already on upstream {ref} @ {new[:12]}.")
        return 0
    if not is_ancestor(base, new) and not allow_rewrite:
        raise NkError(f"upstream {new[:12]} does not descend from {base[:12]} (history rewritten?); "
                      "re-run with --allow-rewrite after review")

    new_tree = ls_tree(new)
    collisions = sorted(p for p in new_tree if is_owned(p, manifest.owned))
    if collisions:
        raise NkError("upstream now ships Northkey-owned paths:\n  " + "\n  ".join(collisions[:20]))
    rendered, skipped = render_tree(new, manifest, brand_context(load_yaml(src.read(BRAND_PATH))), new_tree)

    # Owned files: HEAD's, overridden by pending tracked edits from the working tree.
    owned = {path: entry for path, entry in ls_tree(head).items() if is_owned(path, manifest.owned)}
    index = index_entries()
    for path in pending:
        if (ROOT / path).is_file():
            oid = git_text("hash-object", "-w", "--", path)  # clean filters, like `git add`
            owned[path] = (index.get(path, owned.get(path, ("100644", "")))[0], oid)
        else:
            owned.pop(path, None)
    new_lock = {**lock, "ref": ref, "commit": new, "previous": base}
    owned[LOCK_PATH] = ("100644", write_blob(lock_bytes(new_lock)))

    temp_index = git_path("northkey-sync.index")
    temp_index.unlink(missing_ok=True)
    env = {"GIT_INDEX_FILE": str(temp_index)}
    try:
        git("read-tree", new, env=env)
        entries = dict(owned)
        entries.update({path: (mode, write_blob(data)) for path, (mode, data) in rendered.items()})
        git("update-index", "-z", "--add", "--index-info", input=index_info(entries), env=env)
        tree = git_text("write-tree", env=env)
    finally:
        temp_index.unlink(missing_ok=True)

    count = git_text("rev-list", "--count", f"{base}..{new}")
    lines = [f"northkey: sync upstream {ref} @ {new[:12]}", "",
             f"Merges {count} upstream commit(s) {base[:12]}..{new[:12]} and re-derives the rebrand",
             "from northkey/seams.yaml (tree = upstream + owned paths + seams)."]
    if skipped:
        lines += ["", "Optional seams skipped (anchor moved upstream): " + ", ".join(sorted(set(skipped)))]
    if pending:
        lines += ["", "Carried uncommitted Northkey edits: " + ", ".join(sorted(pending))]
    result = git_text("commit-tree", tree, "-p", head, "-p", new, input="\n".join(lines).encode("utf-8") + b"\n")

    problems, _ = verify(result)
    if problems:
        raise NkError("the sync commit would break the fork invariant:\n  " + "\n  ".join(problems))
    if pending:  # stage them so the fast-forward keeps (identical) local content
        git("add", "-A", "--", *pending)
    git("merge", "--ff-only", "--quiet", result)
    print(f"Synced upstream {ref}: {base[:12]} -> {new[:12]} ({count} commits). HEAD {result[:12]}")
    for seam_id in sorted(set(skipped)):
        print(f"  ! optional seam skipped (anchor moved upstream): {seam_id}")
    for path in sorted(pending):
        print(f"  + carried uncommitted edit: {path}")
    return 0


# ── reporting ─────────────────────────────────────────────────────────────────

LEAK = re.compile(r"Hermes Agent|Nous Research|NOUS HERMES|Messenger of the Digital Gods|☤|\bHermes\b")


def leaks(manifest: Manifest) -> list[str]:
    """Upstream brand strings on the primary surfaces listed in seams.yaml `surfaces`."""
    found = []
    tracked = git("ls-files", "-z").decode("utf-8").split("\0")
    for rel in sorted(p for p in tracked if p and any(fnmatch.fnmatchcase(p, g) for g in manifest.surfaces)):
        try:
            lines = (ROOT / rel).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith(("#", "//", "*", "/*")) or "northkey" in stripped.lower():
                continue
            if LEAK.search(line):
                found.append(f"{rel}:{number}: {stripped[:140]}")
    return found


USES = re.compile(r"uses:\s*([\w.-]+/[\w./-]+)@([0-9a-f]{40})")


def stale_pins() -> list[str]:
    """Northkey workflows pinning an action SHA that upstream's workflows no longer use."""
    workflows = ROOT / ".github" / "workflows"
    upstream: dict[str, set[str]] = {}
    for wf in workflows.glob("*.yml"):
        if not wf.name.startswith("northkey-"):
            for action, sha in USES.findall(wf.read_text(encoding="utf-8")):
                upstream.setdefault(action, set()).add(sha)
    stale = []
    for wf in sorted(workflows.glob("northkey-*.yml")):
        for action, sha in USES.findall(wf.read_text(encoding="utf-8")):
            if action in upstream and sha not in upstream[action]:
                stale.append(f"{wf.name}: {action}@{sha[:12]}; upstream pins "
                             + ", ".join(s[:12] for s in sorted(upstream[action])))
    return stale


def tag_refspecs() -> list[str]:
    """Force-refspecs mirroring upstream release tags (v*) already merged into HEAD."""
    merged = set(git_text("tag", "--list", "--merged", "HEAD").splitlines())
    specs = []
    for ref in git_text("for-each-ref", "--format=%(refname)", f"{UPSTREAM_TAGS}/v*").splitlines():
        name = ref.removeprefix(f"{UPSTREAM_TAGS}/")
        if name in merged or is_ancestor(ref, "HEAD"):
            specs.append(f"+{ref}:refs/tags/{name}")
    return specs


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # ✓/✗ on legacy Windows consoles
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(prog="nk.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_render = sub.add_parser("render", help="re-apply all seams to the pristine upstream text")
    p_render.add_argument("--force", action="store_true", help="discard uncommitted edits in seam files")
    p_verify = sub.add_parser("verify", help="check the fork invariant")
    p_verify.add_argument("--rev", help="commit to verify with its own manifest (default: the index)")
    p_sync = sub.add_parser("sync", help="merge the newest upstream and re-derive the rebrand")
    p_sync.add_argument("--ref", default=None, help="upstream branch (default: brand.yaml upstream.ref)")
    p_sync.add_argument("--commit", help="sync to this upstream commit instead of the branch tip")
    p_sync.add_argument("--no-fetch", action="store_true")
    p_sync.add_argument("--allow-rewrite", action="store_true")
    p_sync.add_argument("--discard-drift", action="store_true", help="drop edits made outside the seams")
    p_sync.add_argument("--init", metavar="COMMIT", help="bootstrap the lock on this upstream commit")
    p_next = sub.add_parser("check-next", help="would the seams apply to the newest upstream?")
    p_next.add_argument("--ref", default=None)
    p_status = sub.add_parser("status", help="show the fork's position relative to upstream")
    p_status.add_argument("--ref", default=None)
    p_status.add_argument("--no-fetch", action="store_true", help="use the last fetched upstream refs")
    p_leaks = sub.add_parser("leaks", help="list upstream brand strings on primary surfaces")
    p_leaks.add_argument("--fail", action="store_true", help="exit 1 when any are found")
    p_pins = sub.add_parser("pins", help="Northkey workflow action pins that upstream has moved past")
    p_pins.add_argument("--fail", action="store_true", help="exit 1 when any are stale")
    sub.add_parser("tag-refspecs", help="print refspecs that mirror upstream v* tags merged into HEAD")
    args = parser.parse_args(argv)

    try:
        brand = load_yaml(Source().read(BRAND_PATH))
        ref = getattr(args, "ref", None) or brand["upstream"]["ref"]
        if args.cmd == "render":
            count, skipped = render(force=args.force)
            print(f"Rendered {count} file(s) from upstream {read_lock()['commit'][:12]}.")
            for seam_id in skipped:
                print(f"  ! optional seam skipped (anchor moved upstream): {seam_id}")
            return 0
        if args.cmd == "verify":
            problems, skipped = verify(args.rev)
            for problem in problems:
                print(f"✗ {problem}")
            for seam_id in skipped:
                print(f"! optional seam not applied (anchor moved upstream): {seam_id}")
            if problems:
                return 1
            manifest = load_manifest(Source(args.rev))
            print(f"✓ fork invariant holds: {len(manifest.seams)} seams in {len(manifest.by_file)} upstream "
                  f"files, base {read_lock(Source(args.rev))['commit'][:12]}")
            return 0
        if args.cmd == "sync":
            if args.init:
                commit = git_text("rev-parse", f"{args.init}^{{commit}}")
                (ROOT / LOCK_PATH).write_bytes(lock_bytes({"remote": brand["upstream"]["url"], "ref": ref,
                                                           "commit": commit}))
                print(f"Initialized upstream lock at {commit[:12]}.")
                return 0
            return sync(ref, args.commit, fetch=not args.no_fetch, allow_rewrite=args.allow_rewrite,
                        discard_drift=args.discard_drift)
        if args.cmd == "check-next":
            new = fetch_upstream(ref)
            _, skipped = render_tree(new, load_manifest(), brand_context(brand))
            behind = git_text("rev-list", "--count", f"{read_lock()['commit']}..{new}")
            print(f"✓ required seams apply to upstream {ref} @ {new[:12]} ({behind} commits ahead of the fork)")
            for seam_id in skipped:
                print(f"  ! optional seam would be skipped: {seam_id}")
            return 0
        if args.cmd == "status":
            lock, manifest = read_lock(), load_manifest()
            tip = (fetch_upstream(ref) if not args.no_fetch else
                   git_text("rev-parse", "--verify", "-q", f"refs/remotes/{UPSTREAM_REMOTE}/{ref}", check=False))
            behind = git_text("rev-list", "--count", f"{lock['commit']}..{tip}") if tip else "?"
            optional = sum(not s.required for s in manifest.seams)
            print(f"upstream base : {lock['commit']} ({lock.get('ref')})")
            print(f"upstream tip  : {tip or 'unknown'}{' (last fetch)' if args.no_fetch else ''}  "
                  f"[{behind} commits not yet synced]")
            print(f"seams         : {len(manifest.seams)} ({optional} optional) in {len(manifest.by_file)} files")
            return 0
        if args.cmd == "leaks":
            found = leaks(load_manifest())
            for line in found:
                print(line)
            print(f"{len(found)} upstream brand string(s) on primary surfaces", file=sys.stderr)
            return 1 if found and args.fail else 0
        if args.cmd == "pins":
            stale = stale_pins()
            for line in stale:
                print(line)
            print(f"{len(stale)} stale action pin(s) in Northkey workflows", file=sys.stderr)
            return 1 if stale and args.fail else 0
        if args.cmd == "tag-refspecs":
            print("\n".join(tag_refspecs()))
            return 0
    except NkError as exc:
        print(f"nk: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
