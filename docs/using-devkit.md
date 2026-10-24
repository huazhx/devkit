# Using devkit

`devkit` is a public repository of tools and agent skills. After cloning it, install only the tools you need; cloning alone changes nothing in Claude Code, the shell or subscription settings.

- `skills/board/`: the board client skill, for looking up tasks, recording progress and changing states.
- `install-skill.py`: installs a given skill into the current user's agent directory.
- `claude-subscriptions/`: a separate tool for switching Claude Code subscriptions; see its own [README](../claude-subscriptions/README.md).

## Install prompt for an agent on a new machine

Hand the whole block below to Claude Code on the new machine:

```text
Install the board skill from https://github.com/huazhx/devkit for Claude Code, for the current user on this machine.

Do the following; after downloading the repository, read README.md, docs/using-devkit.md and skills/board/references/SETUP.md first:
1. Check for Git and Python 3.10+, then clone the repository to ~/git/devkit. If the same repository already exists and its working tree is clean, run git pull --ff-only; if there is a directory conflict or local changes, keep them and report.
2. Run python3 ~/git/devkit/install-skill.py board. Install only board; if the installer reports an existing directory or symlink it does not manage, keep it and report, do not force a replacement.
3. Reuse any existing connection config. If the HTTPS address, the API token or a needed CA root certificate is missing, finish installing the skill first, then list everything missing in one go. I will set the token on this machine myself: do not ask me to paste it into the conversation, and do not print or commit it. For the CA you only need the public root certificate, never the private key; keep TLS verification on.
4. Run python3 ~/.claude/skills/board/scripts/board.py --help to check the client. Once the connection config is complete, run board.py list from the same path as a read-only check; do not write test tasks.
5. Finally, report the install result and the connection result separately, list what is still missing, and tell me how to call /board in a new Claude Code session to see the current tasks.
```

This prompt installs the client. Whether it can connect depends on whether the board service already has a working HTTPS entry point and whether this machine has the connection details.

## Manual install

Run this as the same system user that runs Claude Code. It needs Git and Python 3.10 or later, and no third-party Python packages:

```sh
mkdir -p ~/git
git clone https://github.com/huazhx/devkit.git ~/git/devkit
python3 ~/git/devkit/install-skill.py board
python3 ~/.claude/skills/board/scripts/board.py --help
```

The public repository can be cloned anonymously, with no GitHub token or private-repository access. The default target is `~/.claude/skills/board`. If `~/git/devkit` already exists, check the repository's origin and local changes first, then update as described below; do not overwrite the directory.

If the target machine runs Codex, change the install command to:

```sh
python3 ~/git/devkit/install-skill.py board --agent codex
```

The Codex target directory is `~/.agents/skills/board`; use that directory in the client commands that follow.

## Connect to the board and use it

Following the [board connection setup](../skills/board/references/SETUP.md), write `~/.config/single-core-board/client.json` with permissions `600`. You need the board's HTTPS address and the board API token; if the service uses a private CA, you also need the path to its public root certificate. The connection file and the certificate live outside the skill directory.

The new machine must be able to read this root certificate: another machine trusting it does not make the new machine trust it. The client can use the public `.crt` file directly through `ca_file`, with no CA private key and no system-wide install.

Once configured, run a read-only check:

```sh
python3 ~/.claude/skills/board/scripts/board.py list
```

In a new Claude Code session, type:

```text
/board show the current tasks
```

You can also ask it to record progress on a task or change a state; the agent follows the skill's operating rules. In Codex, call it explicitly with `$board`.

A successful `--help` means the client runs; only a successful `list` means this machine is connected to the board. If config is missing or the service does not serve HTTPS yet, keep the installed skill and verify again once the connection details are complete. If certificate verification fails, troubleshoot with the connection setup doc and keep verification on.

## Update the skill

An installed client updates itself from the board server the next time it connects, so this section is only for updating by hand.

After confirming that the repository's origin is still `https://github.com/huazhx/devkit.git` and that `git status --short` shows no local changes, run:

```sh
git -C ~/git/devkit pull --ff-only
python3 ~/git/devkit/install-skill.py board
```

For a Codex install, keep using `--agent codex`. The installer updates the skill it installed; the connection config and root certificate outside the directory stay untouched. Use the updated skill in a new agent session.

If the installer reports an existing directory or symlink from another source, it keeps the original path and stops. Find out where the old skill came from and how to migrate it before deciding what to do.

This repository holds no board server, task data, credentials or deployment certificates. Installing or updating the skill does not deploy a server and does not create the connection config.
