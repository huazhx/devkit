# devkit

Small tools that make life easier.

## claude-subscriptions

Manage multiple Claude Code subscriptions from one shell command.

```bash
source claude-subscriptions.sh    # creates the `cc` command
cc list                           # show subscriptions (tokens masked)
cc <name>                         # launch Claude Code with that subscription
cc edit                           # edit ~/.config/claude/subscriptions.conf
```

Config auto-created on first use. See `subscriptions.conf.example` for format.

*(more tools coming)*
