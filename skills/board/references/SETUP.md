# Board 连接配置

这个 skill 是已部署看板的客户端，需要 Python 3.10 及以上版本和网络访问能力。它不包含服务器、任务数据或访问凭据。

## 配置一次

将服务管理员提供的连接信息写入 `~/.config/single-core-board/client.json`，权限设为 `600`：

```json
{
  "url": "https://board.example.com",
  "token": "replace-with-your-api-token",
  "ca_file": "/path/to/trusted-root.crt"
}
```

`url` 是实际看板入口，含非默认端口。`token` 是看板 API 令牌，不是网页登录密码或 GitHub token。`ca_file` 是服务器所用私有 CA 的公开根证书；客户端直接读取它，不需要导入操作系统的信任库，也不需要 CA 私钥。服务器使用系统已信任的公共 CA 时可省略 `ca_file`。

配置文件和根证书放在 skill 安装目录之外，更新 skill 时保持不动。也可使用 `BOARD_CLIENT_CONFIG` 指向其他配置文件，或设置 `BOARD_URL`、`BOARD_API_TOKEN`、`BOARD_CA_FILE`。不要把真实 token 提交到仓库、放到命令行参数中或粘贴进对话。

## 验证与调用

Claude Code 安装后的只读检查：

```sh
python3 ~/.claude/skills/board/scripts/board.py list
```

在 Claude Code 对话中输入 `/board 查看当前任务`，或直接要求管理看板。Codex 的安装目录为 `~/.agents/skills/board`，可用 `$board` 显式调用。需要读取历史时用 `show '<任务名或ID>' --events 5`，无需把完整任务 JSON 交给模型。

## 连接失败

- 缺少凭据：确认配置文件属于运行 agent 的用户，并已配置看板 API token。
- 证书验证失败：检查根证书是否匹配、是否过期，以及 URL 的 IP 或域名是否在服务器证书中。保持验证开启。
- 公网 HTTP 被拒绝：将 URL 改成已部署的 HTTPS 入口。HTTP 只允许用于本机隔离实例。
- 「刚被别处改过」（HTTP 409）：要改的字段在读取之后被别处改了，这次什么都没写。重新读取当前卡片，再决定如何更新；不要自动重试。
- 「服务端与客户端版本不匹配」：客户端会先按服务端自动更新，再把这条命令重跑一次；仍然出现这句时，说明自动更新被关掉或没有完成，按下面「更新」手动安装后再试，不要绕过 CLI 直接写数据。

## 更新

客户端每次连上服务都会核对版本。和服务端不一致时，命令结束后自动换成服务端那一版，并在 stderr 提示一行；本机 `~/.claude/skills/board`、`~/.agents/skills/board` 等由安装程序装的目录会一起更新，连接配置不受影响。不想自动更新时，在 `client.json` 里加 `"auto_update": false`，或设置 `BOARD_AUTO_UPDATE=0`，然后在 devkit 目录手动运行 `git pull` 和 `python3 install-skill.py board`（Codex 再加 `--agent codex`）。用环境变量改过连接地址或凭据时默认不自动更新。

字段、状态与写入约束见 [操作手册](BOARD.md)。
