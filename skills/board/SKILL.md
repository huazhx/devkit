---
name: board
description: View or manage the personal task board (look up tasks, record progress, change states, tick off progress steps, sort the inbox). Use when the user mentions the board, task cards or thread state.
---

This skill's `scripts/board.py` calls the HTTPS API of the configured board directly; the runtime needs only Python 3.10 or later. The package contains the CLI and the schema and runs on its own.

The connection configuration is in `~/.config/single-core-board/client.json`, with `url`, `token` and `ca_file` (path to the trusted Caddy root certificate), permissions `600`. It can also be given through `BOARD_URL`, `BOARD_API_TOKEN`, `BOARD_CA_FILE` or `BOARD_CLIENT_CONFIG`. Keep certificate-chain and IP verification on; if the connection fails, stop, and do not fall back to SSH or a local task file. HTTP is allowed only for an isolated instance on the local machine.

On first connection, or when troubleshooting the configuration, read [Connection setup](references/SETUP.md); never put the token into the conversation, a card or a skill file.

```sh
python3 <skill-dir>/scripts/board.py list --state ready
python3 <skill-dir>/scripts/board.py show '<card name or ID>' --no-log
python3 <skill-dir>/scripts/board.py show '<card name or ID>' --events 5
python3 <skill-dir>/scripts/board.py note '<card name or ID>' 'A note from the user'
python3 <skill-dir>/scripts/board.py set '<card name or ID>' state=done
python3 <skill-dir>/scripts/board.py inbox --list
python3 <skill-dir>/scripts/board.py ops --limit 20
python3 <skill-dir>/scripts/board.py backup-status
```

Look up only the cards you need; do not read the full `board.json`. When history is needed, fetch only the last few events of the relevant card. Writes go only through the CLI: each command is one standard operation on the server, changes only the card or fields it names, and the page refreshes automatically on success. If the network drops, the CLI retries once automatically with the same operation ID, and the server does not execute it twice. When the CLI reports "was just changed elsewhere", re-read first, verify, then decide; do not write a local copy and do not repeat the write by hand. `ops --limit 20` shows who changed what recently.

Breakpoints and next actions may hold only the user's own words; when a change is truly needed, add `--user-verbatim`. Do not decide open matters for the user. Before switching into a task, check the card that is currently running; at most one card is `running` at a time. It is the user's main line of work; things the user does on the side while waiting for AI output need no card switch. When recording a person, file or conclusion for the first time, state its context, and keep the user's uncertain tone.

Within an existing project, follow that project's task permissions and division of work among agents. When creating cards, changing a plan, processing the inbox, deleting, or looking up field definitions, read the [operating manual](references/BOARD.md) as needed (in the source repository it is `docs/BOARD.md`). `python3 boardctl.py` in the manual's commands corresponds to `python3 <skill-dir>/scripts/board.py` here.
