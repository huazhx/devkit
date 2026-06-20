# Claude Subscription Manager

A shell script that manages multiple Claude Code subscriptions via a pipe-delimited config file.

## Quick start

```bash
source claude-subscriptions.sh
```

This creates the `cc` command. The config is auto-created at `~/.config/claude/subscriptions.conf` on first use.

## Usage

```bash
cc list          # show all subscriptions (tokens masked)
cc edit          # open the config in $EDITOR
cc <name>        # launch Claude Code with that subscription
cc <name> [args] # launch with extra claude arguments
```

## Config format

Pipe-delimited: `name | base_url | auth_var | token | model | small_fast_model`

Use `-` for any field that should be left unset. Lines starting with `#` are comments.

```ini
# Built-in: plain `claude` with no overrides
official|-|-|-|-|-

# Custom provider examples
qwen|https://api.example.com/anthropic|ANTHROPIC_AUTH_TOKEN|sk-...|sonnet|-
ds|https://api.deepseek.com/anthropic|ANTHROPIC_AUTH_TOKEN|sk-...|deepseek-v4-pro[1m]|deepseek-v4-flash
```

## Avoiding name collision with `cc` (C compiler)

If you also compile C on this machine, rename the command before sourcing:

```bash
CC_CMD=ccx source ~/git_link/scripts/claude-subscriptions.sh
```

Then use `ccx list`, `ccx edit`, `ccx <name>`, etc.

## Security

- The config file is created with `chmod 600`
- Do NOT commit your real `subscriptions.conf` — it's in `.gitignore`
