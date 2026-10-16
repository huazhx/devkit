# 使用 devkit

`devkit` 是公开的工具和 agent skills 仓库。克隆仓库后，按需安装其中的工具；克隆本身不会修改 Claude Code、shell 或订阅配置。

- `skills/board/`：看板客户端 skill，用于查询任务、记录进展和修改状态。
- `install-skill.py`：把指定 skill 安装到当前用户的 agent 目录。
- `claude-subscriptions/`：独立的 Claude Code 订阅切换工具，用法见它自己的 [README](../claude-subscriptions/README.md)。

## 给新机器 agent 的安装 prompt

将下面整段交给新机器上的 Claude Code：

```text
请为这台机器当前用户的 Claude Code 安装 https://github.com/huazhx/devkit 中的 board skill。

请完成以下操作；仓库下载后先阅读 README.md、docs/using-devkit.md 和 skills/board/references/SETUP.md：
1. 检查 Git 和 Python 3.10+，将仓库克隆到 ~/git/devkit。若已有同一仓库且工作区干净，执行 git pull --ff-only；遇到目录冲突或本机改动，保留并报告。
2. 执行 python3 ~/git/devkit/install-skill.py board。只安装 board；若安装器报告已有非它管理的目录或 symlink，保留并报告，不强制替换。
3. 复用已有连接配置。缺少 HTTPS 地址、API token 或所需的 CA 根证书时，先完成 skill 安装，再一次性列出缺项。token 由我在本机配置，不要让我粘贴进对话，也不要输出或提交它。CA 只需要公开根证书，不需要私钥；保持 TLS 验证开启。
4. 运行 python3 ~/.claude/skills/board/scripts/board.py --help 验证客户端。连接配置齐全后，再运行同一路径的 board.py list 做只读检查，不写测试任务。
5. 最后分别报告安装结果和连接结果，列出仍缺的信息，并告诉我如何在 Claude Code 新会话里调用 /board 查看当前任务。
```

这个 prompt 安装客户端。能否连接取决于看板服务是否已经提供可用的 HTTPS 入口，以及这台机器是否配置了连接信息。

## 手动安装

在运行 Claude Code 的同一个系统用户下执行，要求 Git 和 Python 3.10 及以上版本，无需第三方 Python 包：

```sh
mkdir -p ~/git
git clone https://github.com/huazhx/devkit.git ~/git/devkit
python3 ~/git/devkit/install-skill.py board
python3 ~/.claude/skills/board/scripts/board.py --help
```

公开仓库可以匿名克隆，无需 GitHub token 或私有仓库权限。默认目标是 `~/.claude/skills/board`。如果 `~/git/devkit` 已存在，先核对仓库来源和本地改动，再按下文更新，不要覆盖目录。

如果目标机器运行 Codex，将安装命令改为：

```sh
python3 ~/git/devkit/install-skill.py board --agent codex
```

Codex 的目标目录是 `~/.agents/skills/board`，后续客户端命令相应使用该目录。

## 连接看板并使用

按 [Board 连接配置](../skills/board/references/SETUP.md) 配置 `~/.config/single-core-board/client.json`，权限设为 `600`。需要看板的 HTTPS 地址、看板 API token；服务使用私有 CA 时，还需要相应的公开根证书路径。连接文件和证书放在 skill 目录外。

新机器需要能读取这份根证书；另一台机器已经信任它，不会自动让新机器也信任它。客户端可直接通过 `ca_file` 使用公开 `.crt` 文件，无需 CA 私钥或系统级安装。

配置完成后执行只读检查：

```sh
python3 ~/.claude/skills/board/scripts/board.py list
```

在 Claude Code 新会话里输入：

```text
/board 查看当前任务
```

也可以要求记录某项任务的进展或修改状态，agent 会按 skill 的操作约束执行。Codex 使用 `$board` 显式调用。

`--help` 成功表示客户端可以运行；`list` 成功才表示当前机器已连接看板。如果缺配置或服务尚未启用 HTTPS，保留已安装的 skill，等连接信息齐全后再验证。证书验证失败时，按连接配置文档排查，保持验证开启。

## 更新 skill

确认仓库来源仍为 `https://github.com/huazhx/devkit.git`，且 `git status --short` 没有本地改动后，执行：

```sh
git -C ~/git/devkit pull --ff-only
python3 ~/git/devkit/install-skill.py board
```

Codex 安装时继续使用 `--agent codex`。安装器会更新自己安装的 skill；位于目录外的连接配置和根证书保持不动。更新完成后，在 agent 新会话中使用。

如果安装器报告已有其他来源的目录或 symlink，它会保留原路径并停止。先确认旧 skill 的来源和迁移方式，再决定如何处理。

本仓库不包含看板服务器、任务数据、访问凭据或部署证书。安装或更新 skill 不会部署服务器，也不会自动创建连接配置。
