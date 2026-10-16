#!/usr/bin/env python3
"""Install a devkit skill for Claude Code or Codex without touching credentials."""
import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile

SKILLS = Path(__file__).resolve().parent / "skills"
SOURCE = "huazhx/devkit"
MARKER = ".devkit-install.json"


def install(name, agent="claude", destination=None):
    source = SKILLS / name
    if source.parent != SKILLS or not (source / "SKILL.md").is_file():
        raise ValueError("Unknown skill")
    if any(p.is_symlink() for p in source.rglob("*")):
        raise ValueError("Skill source must be self-contained, without symlinks")
    target = Path(destination).expanduser() if destination else (
        Path.home() / (".claude" if agent == "claude" else ".agents") / "skills" / name)
    if target.is_symlink():
        raise ValueError(f"Existing symlink left unchanged: {target}")
    if target.exists():
        try:
            owner = json.loads((target / MARKER).read_text())
        except (OSError, ValueError):
            owner = {}
        if owner != {"source": SOURCE, "skill": name}:
            raise ValueError(f"Existing unmanaged path left unchanged: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=f".{name}-stage-", dir=target.parent))
    previous = None
    committed = False
    try:
        shutil.copytree(source, staged, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (staged / MARKER).write_text(json.dumps({"source": SOURCE, "skill": name}) + "\n")
        if target.exists():
            previous = Path(tempfile.mkdtemp(prefix=f".{name}-previous-", dir=target.parent))
            previous.rmdir()
            os.replace(target, previous)
        try:
            os.replace(staged, target)
            committed = True
        except OSError:
            if previous is not None:
                os.replace(previous, target)
                previous = None
            raise
    finally:
        if staged.exists():
            shutil.rmtree(staged)
        if committed and previous is not None and previous.exists():
            shutil.rmtree(previous)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill", choices=sorted(p.name for p in SKILLS.iterdir()
                                               if (p / "SKILL.md").is_file()))
    parser.add_argument("--agent", choices=("claude", "codex"), default="claude")
    parser.add_argument("--dest", help="Override the installation directory")
    args = parser.parse_args()
    try:
        target = install(args.skill, args.agent, args.dest)
    except (OSError, ValueError) as error:
        parser.exit(1, f"install-skill: {error}\n")
    print(f"Installed {args.skill}: {target}")
    print("Connection settings stay outside this directory. See references/SETUP.md in the skill.")
