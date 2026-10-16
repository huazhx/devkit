# devkit

Small tools that make life easier.

新机器先看 [使用指南](docs/using-devkit.md)：里面有可直接交给 Claude Code 的安装 prompt，以及手动安装、连接和更新方法。各工具按需安装。

## Tools

| Tool | What it does |
|------|--------------|
| [claude-subscriptions](claude-subscriptions/) | Manage multiple Claude Code subscriptions from one shell command (`cc`). Source it once and it installs itself. |

## Agent skills

| Skill | 用途 |
|-------|------|
| [board](skills/board/) | 通过已认证的 HTTPS API 查询任务、记录进展、修改状态和整理收件箱。客户端可独立运行。 |

在运行 Claude Code 的机器上安装，要求 Python 3.10 及以上版本：

```sh
git clone https://github.com/huazhx/devkit.git
python3 devkit/install-skill.py board
```

默认安装到 `~/.claude/skills/board`；Codex 使用 `--agent codex`。按 [连接配置](skills/board/references/SETUP.md) 在 skill 目录外配置服务器 URL、API token 和公开根证书路径，然后在 Claude Code 中使用 `/board`。

更新时在仓库中执行 `git pull --ff-only`，再运行同一安装命令。安装程序仅更新自己安装的目录；遇到已有的其他目录或 symlink 会停止。连接配置不会随 skill 更新而被覆盖。本仓库不包含看板服务器、真实任务、访问凭据或部署证书。

## Docs

| Doc | What it covers |
|-----|----------------|
| [使用 devkit](docs/using-devkit.md) | 新机器安装 prompt、board skill 的安装与调用、连接配置和后续更新。 |
| [GitHub SSH proxy setup](docs/github-ssh-proxy-setup.md) | Reaching GitHub over SSH through an HTTP proxy via `ProxyCommand`. |

*(more tools coming)*
