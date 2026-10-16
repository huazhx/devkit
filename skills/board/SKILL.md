---
name: board
description: 查看或管理个人任务看板：查询任务、记录进展、修改状态、勾进度和整理收件箱。用户提到看板、board、任务卡或线程状态时使用。
---

通过本 skill 的 `scripts/board.py` 直接调用已配置看板的 HTTPS API；运行环境只需 Python 3.10 及以上版本。安装包包含 CLI 和 schema，可独立运行。

连接配置位于 `~/.config/single-core-board/client.json`，含 `url`、`token` 和 `ca_file`（已信任的 Caddy 根证书路径），权限为 `600`。也可通过 `BOARD_URL`、`BOARD_API_TOKEN`、`BOARD_CA_FILE` 或 `BOARD_CLIENT_CONFIG` 指定。保留证书链与 IP 校验，连接失败时停止，不回退到 SSH 或本地任务文件。HTTP 仅允许用于本机隔离实例。

首次连接或排查配置时读取 [连接配置](references/SETUP.md)，不要把 token 写进对话、卡片或 skill 文件。

```sh
python3 <skill目录>/scripts/board.py list --state ready
python3 <skill目录>/scripts/board.py show '<卡名或ID>' --no-log
python3 <skill目录>/scripts/board.py show '<卡名或ID>' --events 5
python3 <skill目录>/scripts/board.py note '<卡名或ID>' '用户的记录'
python3 <skill目录>/scripts/board.py set '<卡名或ID>' state=done
python3 <skill目录>/scripts/board.py inbox --list
python3 <skill目录>/scripts/board.py backup-status
```

先查所需的卡，不读取完整 `board.json`。需要历史时只取相关卡最近几条事件。写入只走 CLI，成功后页面自动刷新；连接或 CAS 冲突报错时重新核实，不写本地副本、不强制覆盖、不盲目重试写入。

断点和下一步只能写用户原话；确需修改时加 `--user-verbatim`。不要替用户决定未决事项。切入任务前先核对正在运行的卡，同一时刻最多一张 `running`。记录第一次出现的人、文件或结论时写清上下文，保留用户的不确定语气。

在已有项目中遵守该项目的任务权限和 agent 分工。创建卡片、修改计划、处理收件箱、删除或查字段定义时，按需读取 [操作手册](references/BOARD.md)（源码仓库对应 `docs/BOARD.md`）。手册命令中的 `python3 boardctl.py` 对应此处的 `python3 <skill目录>/scripts/board.py`。
