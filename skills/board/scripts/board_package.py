"""客户端安装包：版本号、校验和原子替换（ARCHITECTURE §6）。服务端和 boardctl 共用这一份。

版本号就是包内容的哈希，不手动编号：打包文件改了一个字节，版本就变；没改就不变。
不算进版本的：安装标记 `.devkit-install.json`、以 `.` 开头的路径（如 macOS 的 .DS_Store）、
__pycache__ 和 *.pyc —— 它们不是包的内容，算进去会让一份没动过的安装永远「对不上」。

只有带安装标记的目录才会被自动替换：标记由 devkit 的 install-skill.py 写入，意思是
「这个目录整个归安装程序管，里面没有手写的东西」。仓库检出、没有标记的目录一律不碰。
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
# 包里只允许这种路径：最多一层子目录，各段不以 . 开头，扩展名白名单。挡掉 ../、绝对路径和隐藏文件。
PATH_RE = re.compile(r"^(?:[A-Za-z0-9_-]+/)?[A-Za-z0-9_-][A-Za-z0-9_.-]*\.(?:md|py|json|txt)$")
REQUIRED = ("SKILL.md", "scripts/board.py", "scripts/boardctl.py",
            "scripts/board_package.py", "scripts/board_schema.py")
MAX_BYTES = 2 * 1024 * 1024


def files_of(root):
    """目录里属于安装包的文件：{相对路径: bytes}。"""
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
    """服务端 GET /api/client 的响应体；目录不存在时返回 None（不提供更新）。"""
    if not Path(root).is_dir():
        return None
    files = files_of(root)
    if not files:
        return None
    return {"version": version_of(files), "files": {k: v.decode("utf-8") for k, v in files.items()}}


def check(pkg, expected=None):
    """校验服务端发来的包，返回 {相对路径: bytes}。任何一项不对就抛 ValueError，什么都不装。"""
    files = pkg.get("files") if isinstance(pkg, dict) else None
    if not isinstance(files, dict) or not files:
        raise ValueError("安装包格式不对")
    out, total = {}, 0
    for rel, text in files.items():
        if not isinstance(rel, str) or not isinstance(text, str) or not PATH_RE.match(rel):
            raise ValueError(f"安装包里有不允许的路径：{rel!r}")
        out[rel] = text.encode("utf-8")
        total += len(out[rel])
    if total > MAX_BYTES:
        raise ValueError("安装包过大")
    missing = [r for r in REQUIRED if r not in out]
    if missing:
        raise ValueError("安装包缺文件：" + ", ".join(missing))
    version = version_of(out)
    if version != pkg.get("version") or (expected and version != expected):
        raise ValueError("安装包内容和版本号对不上")
    return out


def managed(root):
    try:
        return json.loads((Path(root) / MARKER).read_text(encoding="utf-8")) == MANAGED
    except (OSError, ValueError):
        return False


def install_roots(own_root):
    """本机所有受管的 board 安装（按真实路径去重）：自己所在的一份，加上各 agent 的默认位置。
    符号链接（如 ~/.local/share/single-core-board/skill → ~/.agents/skills/board）按指向的目录算。"""
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
    """把一处受管安装整体换成 files，保留安装标记和目录权限。先在同级临时目录写好再一次改名；
    任何一步失败，原目录原样留着。"""
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
