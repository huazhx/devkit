"""Client package: version, verification and atomic replacement (ARCHITECTURE §6). The server and boardctl share this one copy.

The version is a hash of the package content, never numbered by hand: change one byte of a
packaged file and the version changes; change nothing and it stays.
Not counted in the version: the install marker `.devkit-install.json`, paths starting with `.`
(such as macOS's .DS_Store), __pycache__ and *.pyc. They are not package content, and counting them
would make an untouched install "mismatch" forever.

Only directories with an install marker are replaced automatically: devkit's install-skill.py
writes the marker, and it means "the installer owns this whole directory; nothing in it was written
by hand". A repository checkout or any directory without the marker is never touched.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

MARKER = ".devkit-install.json"
MANAGED = {"source": "huazhx/devkit", "skill": "board"}
# The only paths allowed in a package: at most one subdirectory, no segment starting with ., an extension allowlist. Blocks ../, absolute paths and hidden files.
PATH_RE = re.compile(r"^(?:[A-Za-z0-9_-]+/)?[A-Za-z0-9_-][A-Za-z0-9_.-]*\.(?:md|py|json|txt)$")
REQUIRED = ("SKILL.md", "scripts/board.py", "scripts/boardctl.py",
            "scripts/board_package.py", "scripts/board_schema.py")
MAX_BYTES = 2 * 1024 * 1024


def files_of(root):
    """The files in a directory that belong to the package: {relative path: bytes}."""
    root = Path(root)
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
            continue
        if p.is_symlink() or not p.is_file() or p.suffix == ".pyc":
            continue
        out[rel.as_posix()] = p.read_bytes()
    return out


def version_of(files):
    h = hashlib.sha256()
    for rel in sorted(files):
        data = files[rel]
        h.update(rel.encode("utf-8") + b"\0" + len(data).to_bytes(8, "big") + data)
    return h.hexdigest()[:16]


def payload(root):
    """The response body of the server's GET /api/client; None if the directory does not exist (no updates offered)."""
    if not Path(root).is_dir():
        return None
    files = files_of(root)
    if not files:
        return None
    return {"version": version_of(files), "files": {k: v.decode("utf-8") for k, v in files.items()}}


def check(pkg, expected=None):
    """Verify a package sent by the server and return {relative path: bytes}. Any problem raises ValueError and nothing is installed."""
    files = pkg.get("files") if isinstance(pkg, dict) else None
    if not isinstance(files, dict) or not files:
        raise ValueError("malformed package")
    out, total = {}, 0
    for rel, text in files.items():
        if not isinstance(rel, str) or not isinstance(text, str) or not PATH_RE.match(rel):
            raise ValueError(f"the package contains a path that is not allowed: {rel!r}")
        out[rel] = text.encode("utf-8")
        total += len(out[rel])
    if total > MAX_BYTES:
        raise ValueError("the package is too large")
    missing = [r for r in REQUIRED if r not in out]
    if missing:
        raise ValueError("the package is missing files: " + ", ".join(missing))
    version = version_of(out)
    if version != pkg.get("version") or (expected and version != expected):
        raise ValueError("the package content does not match its version")
    return out


def managed(root):
    try:
        return json.loads((Path(root) / MARKER).read_text(encoding="utf-8")) == MANAGED
    except (OSError, ValueError):
        return False


def install_roots(own_root):
    """Every managed board install on this machine (deduplicated by real path): the one this file is in, plus each agent's default location.
    A symlink (such as ~/.local/share/single-core-board/skill → ~/.agents/skills/board) counts as the directory it points to."""
    home = Path.home()
    candidates = [Path(own_root), home / ".claude/skills/board", home / ".agents/skills/board",
                  home / ".local/share/single-core-board/skill", home / ".hermes/skills/board"]
    seen, out = set(), []
    for c in candidates:
        try:
            real = c.resolve(strict=True)
        except OSError:
            continue
        if real in seen or not real.is_dir() or not managed(real):
            continue
        seen.add(real)
        out.append(real)
    return out


def install(files, root):
    """Replace one managed install with files as a whole, keeping the install marker and the directory mode. Everything is written
    to a sibling temporary directory first, then renamed in one step; if any step fails, the original directory stays as it was."""
    root = Path(root)
    staged = Path(tempfile.mkdtemp(prefix=".board-update-", dir=root.parent))
    old = None
    try:
        for rel, data in files.items():
            p = staged / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        (staged / MARKER).write_text(json.dumps(MANAGED) + "\n", encoding="utf-8")
        os.chmod(staged, stat.S_IMODE(root.stat().st_mode))
        old = Path(tempfile.mkdtemp(prefix=".board-old-", dir=root.parent))
        old.rmdir()
        os.replace(root, old)
        try:
            os.replace(staged, root)
        except OSError:
            os.replace(old, root)
            old = None
            raise
    finally:
        if staged.exists():
            shutil.rmtree(staged, ignore_errors=True)
        if old is not None and old.exists():
            shutil.rmtree(old, ignore_errors=True)
