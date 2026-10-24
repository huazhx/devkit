# Board connection setup

This skill is a client for a deployed board; it needs Python 3.10 or later and network access. It contains no server, no task data and no access credentials.

## Configure once

Write the connection details provided by the service administrator to `~/.config/single-core-board/client.json`, and set its permissions to `600`:

```json
{
  "url": "https://board.example.com",
  "token": "replace-with-your-api-token",
  "ca_file": "/path/to/trusted-root.crt"
}
```

`url` is the actual board entry point, including any non-default port. `token` is the board API token, not the web login password or a GitHub token. `ca_file` is the public root certificate of the private CA the server uses; the client reads it directly, so there is no need to import it into the operating system's trust store, and no need for the CA private key. If the server uses a public CA that the system already trusts, `ca_file` can be omitted.

Keep the configuration file and the root certificate outside the skill installation directory, so they stay untouched when the skill is updated. You can also point `BOARD_CLIENT_CONFIG` at another configuration file, or set `BOARD_URL`, `BOARD_API_TOKEN` and `BOARD_CA_FILE`. Never commit a real token to a repository, put it in command-line arguments, or paste it into a conversation.

## Verify and invoke

Read-only check after installing for Claude Code:

```sh
python3 ~/.claude/skills/board/scripts/board.py list
```

In a Claude Code conversation, type `/board show the current tasks`, or just ask Claude to manage the board. For Codex, the installation directory is `~/.agents/skills/board`, and the skill can be invoked explicitly with `$board`. To read history, use `show '<task name or ID>' --events 5`; there is no need to hand the full task JSON to the model.

## Connection failures

- Missing credentials: check that the configuration file belongs to the user running the agent, and that a board API token is configured.
- Certificate verification failed: check whether the root certificate matches, whether it has expired, and whether the URL's IP address or domain name is in the server certificate. Keep verification on.
- Public HTTP refused: change the URL to the deployed HTTPS entry point. HTTP is allowed only for an isolated instance on the local machine.
- "was just changed elsewhere" (HTTP 409): the field to be changed was changed elsewhere after it was read, and nothing was written this time. Re-read the current card, then decide how to update it; do not retry automatically.
- "Server and client versions do not match": the client first updates itself automatically to match the server, then reruns the command once. If this message still appears, automatic update is turned off or did not complete; install manually as described under "Updating" below and try again. Do not bypass the CLI to write data directly.

## Updating

Every time the client connects to the service, it checks the version. If it differs from the server's, then once the command finishes the client automatically switches to the server's version and prints one line on stderr; the directories installed by the installer on this machine, such as `~/.claude/skills/board` and `~/.agents/skills/board`, are updated together, and the connection configuration is not affected. If you do not want automatic updates, add `"auto_update": false` to `client.json` or set `BOARD_AUTO_UPDATE=0`, then manually run `git pull` and `python3 install-skill.py board` in the devkit directory (for Codex, add `--agent codex`). When the connection address or credentials have been changed through environment variables, automatic update is off by default.

Fields, states and write constraints: see the [operating manual](BOARD.md).
