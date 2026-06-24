# claude-subscriptions

Manage multiple Claude Code subscriptions from one shell command. Each
subscription is a named set of `ANTHROPIC_*` environment variables; `cc <name>`
launches Claude Code with exactly that set and nothing inherited from the
surrounding shell.

## Setup (once)

Source the script a single time:

```bash
source /path/to/devkit/claude-subscriptions/claude-subscriptions.sh
```

That does two things:

1. defines the `cc` command in the current shell (active immediately), and
2. appends a `source` line to your `~/.bashrc` so every future shell has `cc`.

It's idempotent — sourcing again never duplicates the line. After this you only
ever touch the config (`cc edit`); you never edit your rc by hand.

Knobs:

- `CC_NO_AUTOINSTALL=1` — define `cc` but don't write to the rc.
- `CC_RC_FILE=~/.zshrc` — install into a different rc file.
- `CC_CMD=ccx` — use a different command name (`cc` shadows the C-compiler
  alias; pick another name if you build C on this box).

## Usage

```bash
cc list              # show subscriptions (tokens masked)
cc <name>            # launch Claude Code with that subscription
cc <name> [args…]    # extra args are forwarded to `claude`
cc edit              # edit the config in $EDITOR
cc install           # (re)add the rc source line
cc uninstall         # remove the rc source line
cc help              # show usage
```

## Config

Subscriptions live in `~/.config/claude/subscriptions.conf` (override the path
with `$CC_SUBS_FILE`). The file is auto-created with an `official` entry and
`chmod 600` on first use, and is re-read on every launch, so edits take effect
immediately.

One subscription per line, `|`-separated:

```
name | base_url | auth_var | token | model | small_fast_model
```

- `-` leaves a field unset. An all-`-` entry launches plain `claude` (the
  official `/login` flow).
- `auth_var` is the environment variable the token is exported as — usually
  `ANTHROPIC_AUTH_TOKEN` or `ANTHROPIC_API_KEY`.

See [`subscriptions.conf.example`](subscriptions.conf.example) for worked
examples.

> **Never commit your real config — it holds API keys.** The repo `.gitignore`
> already excludes `subscriptions.conf`; keep your real file at
> `~/.config/claude/subscriptions.conf` with `chmod 600`.
