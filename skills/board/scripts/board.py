#!/usr/bin/env python3
"""Run the bundled HTTPS client; task data is never stored by this entrypoint."""
import os
from pathlib import Path
import runpy
import sys


def main():
    here = Path(__file__).resolve()
    candidates = [here.with_name("boardctl.py"),
                  here.parents[3] / "boardctl.py",
                  Path(os.environ.get("BOARD_CLIENT_PATH", "~/.local/share/single-core-board/skill/scripts/boardctl.py")).expanduser()]
    for client in candidates:
        if client.is_file() and client.resolve() != here:
            sys.path.insert(0, str(client.parent))
            runpy.run_path(str(client), run_name="__main__")
            return
    sys.exit("board: 未安装 HTTPS 客户端；请运行独立仓库 deploy/package-client.py")


if __name__ == "__main__":
    main()
