# devkit

Small tools that make life easier.

On a new machine, start with the [usage guide](docs/using-devkit.md): it has an install prompt you can hand straight to Claude Code, plus manual install, connection and update steps. Install each tool only when you need it.

## Tools

| Tool | What it does |
|------|--------------|
| [claude-subscriptions](claude-subscriptions/) | Manage multiple Claude Code subscriptions from one shell command (`cc`). Source it once and it installs itself. |

## Agent skills

| Skill | What it does |
|-------|------|
| [board](skills/board/) | Look up tasks, record progress, change states and sort the inbox of a [Single-Core Board](https://github.com/huazhx/single-core-board) through its authenticated HTTPS API. The client runs on its own. |

Install it on the machine that runs Claude Code (Python 3.10 or later):

```sh
git clone https://github.com/huazhx/devkit.git
python3 devkit/install-skill.py board
```

It installs to `~/.claude/skills/board` by default; for Codex add `--agent codex`. Following the [connection setup](skills/board/references/SETUP.md), put the server URL, API token and public root certificate path in a config file outside the skill directory, then use `/board` in Claude Code.

An installed client updates itself from the board server the next time it connects. To update by hand, run `git pull --ff-only` in the repository and the same install command again. The installer only updates directories it installed itself and stops at any other existing directory or symlink. Updates never overwrite the connection config. This repository holds no board server, real tasks, credentials or deployment certificates.

## Docs

| Doc | What it covers |
|-----|----------------|
| [Using devkit](docs/using-devkit.md) | The install prompt for a new machine, installing and calling the board skill, connection setup and later updates. |
| [GitHub SSH proxy setup](docs/github-ssh-proxy-setup.md) | Reaching GitHub over SSH through an HTTP proxy via `ProxyCommand`. |

*(more tools coming)*
