# Single-Core Board — Board Operating Protocol

This file is the complete manual for AI agents operating the board. For everyday operations, read the board skill first; when a task involves fields, plans, complex state changes, deletion or maintenance, read the relevant section as needed. Perform only the operations listed here; when you cannot confirm what the user intends, check with the user. Do not guess.

## 0. Service and data boundaries

The board runs as a standalone Linux service; systemd starts it at boot and restarts it after a crash. The web page is a view of the task data; the browser and `boardctl.py` both read from and write to the same service through the authenticated API. Every write is a **standard operation** defined by the server (API contract: [OPS.md](OPS.md)): each operation changes only the card, fields or inbox entry it names, and never uploads the whole board.

**`board.sqlite` in the service data directory is the only writable source of task data.** The standard path is `/var/lib/single-core-board/board.sqlite`; it can be configured with `BOARD_DATA_DIR`. `GET /board.json` and the `board.json` exported by backups are read-only views of it, not a second copy of the data. The code repository holds only code; real cards, credentials and backups are all kept outside it. A separate private GitHub data repository may hold recovery snapshots, but it takes no part in day-to-day task synchronization and is never pulled back automatically to overwrite production data. Credentials and private keys do not go into the data repository.

**If the service is unreachable or authentication fails, stop and report the failure. Do not read an old snapshot and present it as the current state, and do not fall back to writing local JSON.** The same rule applies whether the CLI runs on the service host or on another machine.

CLI connection settings are described in [connection setup](SETUP.md): the configuration file defaults to `~/.config/single-core-board/client.json`; `BOARD_URL`, `BOARD_API_TOKEN` and `BOARD_CREDENTIALS_FILE` are also supported. The browser uses a login session; the CLI uses an API token. Do not record credentials in cards, in output or in the code repository.

The board contains only the cards the service already holds. The system does not collect tasks automatically from a Research Log or a notes vault. A Research Log is the record of one long-running research thread and is linked from a card's `log` field; a task gets a card first, and the log is linked to it afterwards.

The service code and its deployment are maintained by its administrator. This manual specifies how to operate on cards; deployment, connection and backup commands are in [connection setup](SETUP.md).

**Deploying a new service does not mean the data takeover is complete.** When migrating from an old board, first identify every source that may still write and every unsynchronized change, save a complete snapshot and verify it, and only then switch the write entry point. Until the takeover is complete, do not declare an empty instance, or a copy on any one machine, to be the latest state of the existing board.

## 1. Data structure

Top-level structure:

```json
{ "version": 2, "rev": 0, "savedAt": 0, "lastActiveId": "…", "threads": [], "inbox": [] }
```

- `threads` is the array of cards. Array order carries no queueing meaning; the page does its own sorting.
- `inbox` is an array of `{ "id": uuid, "ts": epoch-ms, "text": "..." }` objects. The page links, ticks, promotes and deletes entries by `id`; it must not be written as an array of strings. Since 2026-10-29 the inbox **is the to-do catalog**: an entry may also carry `card` (the card it belongs to), `when`, `due`, `wait`, `pick` (`{day, rank, why}`) and `doneAt`; see §1.1.
- `rev` is the global revision number; every operation that changes data increments it by one. Each card also has its own `rev`. `savedAt` is the Unix millisecond timestamp of the most recent change. Both are for checking only; they are not write tokens and need not be sent back when writing.
- `lastActiveId` is the ID of the card that most recently **left** `running`. It is updated whenever a card leaves `running`; it only affects context ordering within one urgency band. If it is missing or points to a deleted card, ignore it.

Card fields:

| Field | Meaning | Values and constraints |
|---|---|---|
| `id` | Unique card identifier | Random string; never changes after creation. |
| `title` | Task name | A short noun phrase. |
| `state` | State | `ready` / `running` / `blocked` / `done` / `trashed`. By default the CLI does not list cards in the Trash; only `list --state trashed` lists them. |
| `priority` | Priority | `0` is highest, then `1`, `2`. |
| `due` | Due date | `YYYY-MM-DD`; may be omitted when empty. |
| `deliverable` | Deliverable | A one-sentence acceptance criterion; must not be written as steps. |
| `breakpoint` | Breakpoint | The state only the user knew when pausing; every rewrite appends a `bp` event. **No longer asked for (2026-10-29):** the page neither shows nor asks for it and sends the stored value back unchanged on a switch; old values stay readable in `show`. Do not ask the user for one; a finding worth keeping goes in a note. |
| `nextAction` | Next action | An action that can be carried out directly; if it is a decision, write it as "Decide: A or B, by criterion C". Every rewrite appends an `na` event. |
| `waitingOn` | Whom I am waiting on | When the AI sets `blocked`, it must hold content the user provided; empty in other states. For the exception when the user blocks a card on the page themselves, see §3.2. |
| `blockingOthers` | Who is waiting on me | A person's name or one sentence. The opposite direction to `waitingOn`; when non-empty, it adds 4 to urgency. |
| `lane` | Task type | `project` / `standing` / `errand` / `claude`; determines the required information and how the card is queued. |
| `cadence` | Expected interval, in days, for a standing task | Integer number of days, used only by `standing`; empty for other types. |
| `log` | Research Log link | E.g. `[[Research notes]]`; multiple links are joined with ` · `. Linking a notes vault is not a prerequisite for running the board. |
| `plan` | Open questions of this thread | `{ "summaryAt": epoch-ms, "tracks": [...] }`, the tracks being questions (`q`); when there are none, the field is not set. Card steps (`flow` tracks) are retired since 2026-10-29: to-dos are the only checklist (§1.1, §3.6). |
| `retiredSteps` | The steps the card carried before 2026-10-29 | `{ "at", "tracks", "todos" }`, written only by `plan.retireSteps`; kept so the change can be undone. Not live: never tick, copy or report them as steps. |
| `createdAt` / `touchedAt` | Creation time / time of latest activity | Unix milliseconds. Every card edit refreshes `touchedAt`. |
| `events` | Ledger of notes, field rewrites and state changes | Ascending by `ts`, append-only, unlimited, never truncated. The only exception is the user deleting their own note on the page. |

The event format is defined by `board_schema.py`; the rules exist only in this one place, on the server. The web page and the CLI no longer implement migration or write rules of their own.

| `kind` | Format | Meaning |
|---|---|---|
| `note` | `{ts, kind, text}` | A note, from the page, from filing an inbox entry under a thread, or from the CLI `note`. |
| `bp` / `na` | `{ts, kind, text}` | At `ts`, the breakpoint / next action was set to `text`. Records the **new value**; `""` means cleared. |
| `state` | `{ts, kind, to, auto?, noticedAt?}` | At `ts`, the card switched to state `to`; `auto: true` marks the 6-hour fallback auto-pause. |
| Legacy `bp` / `na` | `{…, replaced: true}` | Produced only by v1 migration: records the old value that was replaced at `ts`. Do not interpret it as the new value set at that time. |
| Legacy `session` | `{ts, kind, out, noticedAt?}` | One run session, produced only by v1 migration. |
| Any event | `approx: true` | The old data had no timestamp, and migration used an estimated time. Keep this marker; do not change it to an exact time. |

The following times are derived from events and are no longer stored as separate fields. The server includes `derived` in each card's view (not stored in the database, and not sent back by clients); `python3 boardctl.py show <card>` lists the times in it:

- `breakpointAt` / `nextActionAt` is the `ts` of the most recent event of that kind, including legacy events marked `replaced`.
- `enteredAt` / `doneAt` is the `ts` of the last `state` event that corresponds to the current state; it is empty when the current state does not correspond.
- `trashedAt` is the time the card entered the Trash; `restoreTo` is the state the card returns to when restored from the Trash.
- A session starts at the state event that enters `running` and ends at the next state event that leaves `running`. Legacy `session` events also take part in the derivation; the session list is in `derived.sessions`, and the CLI `time` command totals it by day.

`due`, `deliverable`, `breakpoint`, `nextAction`, `waitingOn`, `blockingOthers`, `cadence` and `log` are not written to storage when empty; readers treat a missing field as empty. The current `version` is `2`. v1 data is migrated once, only when the server imports it; do not rebuild history by hand.

`lane` determines what must be filled in:

| Type | Task shape | Information the user must provide | Next action |
|---|---|---|---|
| `project` | Has an end point, runs across several periods, and is costly to resume after an interruption. | Title, deliverable, next action, what it is waiting on; record the due date if there is one. | The next action states an action; what has been ruled out and why goes in a note. |
| `standing` | Has no end point, only a position in its progress. | Title and a position pointer. | The next action is the position pointer. |
| `errand` | A one-off errand that can be finished in one sitting. | Title; the task itself is the deliverable. | Not required, and not asked for. |
| `claude` | Claude's own checkpoint: the card exists only so that Claude notices a date or runs a check. It is a reminder for Claude, not work the user owes. | Title; `deliverable` says what Claude checks or tells the user; `due` is the day to do it. | Not required, and not asked for. |

Standing cards always go at the end of the ready queue, are shown semi-transparent, take no part in urgency ranking, and their row shows only how long they have been idle (since 2026-10-19; before that, a standing card past its `cadence` was pushed back into the main queue).

### 1.1 To-dos: one catalog, the main line stays one card (2026-10-29)

User: 「把待办这个事情抽出来，整理一个目录，但是主线是卡片依旧只有一个，待办可以属于某些卡片，但不是非要有主线」.

- **What goes where.** To-dos are the only checklist. Anything to do, small or a step of a card's work, is a to-do in the catalog; a to-do may belong to a card (`card`) or to none, and it never becomes the running card. If one grows into a line of work, `inbox.promote` makes it a card. Card steps are retired (2026-10-29; the user: 「有了这个 todos，那个 progress 就可以不要了」, after the Running panel showed a card's steps and its to-dos as two checklists): the server refuses `flow` tracks and `plan.step`, and `plan.retireSteps` turned the open steps of every live card into to-dos of that card (`retire-steps` in the CLI; their details went into one note on the card). A card's `plan` keeps only questions (§3.6).
- **Who writes what.** The user adds a line (the page's one capture input, or by telling Claude), ticks what is done (the square box on the left of a to-do) and may move a to-do to another card or to none (its card chip). Claude sorts new entries (`card`, `when`, `due`, `wait`) and writes `pick`: every morning at 07:30 Beijing time a scheduled Sonnet run on usp (`~/.local/share/board-morning/`) picks at most 6 open to-dos for that day, keeping any picks already made, and Claude picks at the user's first message only when that run did not happen; it picks (`todo pick`, day = the user's local day), ranks them and writes one reason each, by these rules: an outside date that cannot be made up, counted back to a start day; things that wait on someone's reply go out first; what moves the current stage goal before what does not; within a tier, small first; standing work stays out. A to-do left unpicked for several days running gets one keep-or-drop question. The user never has to rank.
- **What a to-do is (D44, 2026-10-29).** User: 「要不你只把留给人看的就行了」. A to-do is something the user does, in one short line (about 30 characters); details go in a note on its card, or on 「想法池」 when it has none. Claude's own checkpoints ("on day X ask him …") are never to-dos: they are the `due` of a `claude`-lane card. Today shows only picks, so a to-do the user says is for today is picked when it is added, after the existing picks, with the reason 「你定的」; this is the only pick Hermes may make.
- **On the page.** From the top: Running, the one capture input, "Focus" (2026-10-29, was "Today"; user: 「应该把这个标题从 today 改成需要重点关注的一些事件」: two layers, "Coming up", every open to-do due within 7 days or overdue and not waiting on someone, by date, with no one picking it, so an interview written with its day as `due` shows by itself; and "Today", the day's picks in order with their reasons; hidden when both are empty), Ready, Blocked, For Claude, and at the end the "To-dos" zone, which folds the whole catalog, grouped by card, then "No card", "Later" (`when` after today) and "Waiting on someone" (user, 2026-10-29: the input right above Today, the whole list last). A to-do ticked today moves into a folded "Done" (user: 「直接写一个 “done” 就行了」) at the end of its list (Today, the catalog, the Running panel), where it can still be unticked, until the day ends (user, 2026-10-29: 「打对号的可以暂时折叠了」). The Running panel lists the to-dos that belong to its card, filtered from the catalog, not copied, with an input that adds one to that card; it shows at most 3 open ones (today's picks first, then a due or start date within 3 days) and folds the rest under "More" (user, same day: 「看板卡没必要全部显示 todos」).
- **CLI.** `todo list [--today]`, `todo add "<text>" [card=… when=… due=… wait=…]`, `todo set <to-do> key=value…`, `todo pick <to-do> <rank> "<why>" [--day …]`, `todo unpick <to-do>`, `todo done <to-do> [--undo]`. `<to-do>` is an id prefix or a piece of its text. `inbox "<text>"` still adds a to-do.

**The `claude` lane holds Claude's own checkpoints (user, 2026-10-26: "Open a new section for cards like these, after Inbox, collapsed by default. You read them yourself; looking at them tires me"; since the to-do catalog moved to the end of the page on 2026-10-29 the zone sits after Blocked).** Claude reads them from the CLI; the user does not need to look at them. `list`, `list --due` and `show` return them like any other card, and `list --lane claude` lists only them; Claude acts on one when its `due` has come. On the page, live ones (`ready` or `blocked`) appear only in the "For Claude" zone after Blocked, which is collapsed on every load; they are not in Ready or Blocked, take no part in urgency ranking, carry no urgency tint, u value or due chip, and are not offered by a to-do's card chip. A `running`, delivered or trashed one shows where any card in that state would. When Claude creates such a card, it uses `--lane claude`; work the user owes never goes in this lane, and a card the user keeps for their own ideas (for example a `standing` idea pool) stays where it is.

## 2. The standard flow for every write

1. **Re-read the service's current data** through the CLI or the authenticated API. Read only the target card or the skeleton projection; do not reconstruct the current state from memory, old output or a copy on disk.
2. Change only the target fields you have just read. Next actions (and a breakpoint, if one is ever written) and answers to questions must be the user's own words; see §5.
3. The CLI sends the change to the service as one standard operation (`POST /api/ops`, see [OPS.md](OPS.md)). When it changes fields, it attaches the old values it just read (`if`); the service checks and executes under one write lock, in one transaction, and writes one line to the operation log.
4. The success response returned by the service is what counts. A successful write is broadcast automatically to open web pages; there is no need to send a separate notification to the local machine.
5. On failure, do not write any local copy:
   - "was just changed elsewhere" (409): the field to be changed was changed by someone else after you read it, and nothing was written this time. Re-read the target card, confirm that the original intent still holds, then act; do not retry automatically.
   - "Breaks a board rule" (422), for example another card is already `running`: deal with that rule first, as the message says; do not work around it.
   - Network timeout: the CLI has already retried once with the same operation ID, and the service deduplicates by that ID, so nothing is appended twice. If it still fails, re-read first to check whether the change took effect, then decide whether to redo it.
   - "Server and client versions do not match": update the CLI or the service; do not switch to another way of writing.

There are no whole-board writes: a client cannot upload or replace the whole board, deletion can only be an explicit operation, and the service never deletes a card because some client is missing it. The old whole-board write endpoints `PUT /board.json` and `POST /merge` are disabled (they return 410). The web page's "Import and overwrite" is the only whole-board replacement; it requires a person's confirmation, and the service first exports the current data to the data directory.

### 2.1 Read projections and CLI usage

Do not routinely dump the full `board.json` into the AI's context. Look at the skeleton list first, and at a single card when needed; read the long event ledger only when checking history. Run all commands in the code repository directory; the CLI reads the service data and submits changes itself.

```sh
python3 boardctl.py list
python3 boardctl.py list --due
python3 boardctl.py list --lane claude
python3 boardctl.py list --state trashed
python3 boardctl.py show running --no-log
python3 boardctl.py show 'Example project' --no-log
python3 boardctl.py show 'Example project' --events 5
python3 boardctl.py plan 'Example project'
python3 boardctl.py time --days 7
python3 boardctl.py note 'Example project' 'A progress note the user provided'
python3 boardctl.py inbox 'An idea that does not belong to a known card yet'
python3 boardctl.py inbox --list --limit 20
python3 boardctl.py create 'A new task the user raised' --lane errand
python3 boardctl.py set 'Example project' due=2027-01-15
python3 boardctl.py todo add 'Run the verification' card='Example project'
python3 boardctl.py todo done 'Run the verification'
python3 boardctl.py q 'Example project' 1 --decide 'A decision the user gave in person' --user-verbatim
```

The project names, track names and text in these examples are placeholders; in real use, replace them with the card that actually matches and with the content the user provided. An ID prefix or a title substring also identifies a card; if it is ambiguous, give the full ID instead of guessing.

`breakpoint`, `nextAction` and the `answer` of a `q` cannot be written without `--user-verbatim`. The flag reminds the operator to check that the text is the user's own words; it cannot turn AI-drafted text into the user's words. The commands and flags currently available are those shown by `python3 boardctl.py --help` and each subcommand's `--help`. An operation the CLI does not offer must not be worked around by writing the data file directly; if a programmatic operation is truly needed, call only the standard operations listed in [OPS.md](OPS.md). `python3 boardctl.py ops --limit 20` lists the recent operation log: which client changed which card with which operation, and when. Start here when investigating a mistaken change or recovering deleted content.

`python3 boardctl.py notify` only sends an authenticated refresh notification to the configured service; it cannot be used to cover up a failed write.

## 3. Operations manual

### 3.1 A small task the user states in one sentence

Decide the `lane` first. A one-off favour a colleague asked for is usually an `errand`: fill in only the title; if the user said who is waiting, fill in `blockingOthers`. A card that only reminds Claude to notice a date or run a check goes in `claude` (§1).

Record only information the user has already given. You may rewrite the user's description as a one-sentence `deliverable` acceptance criterion; if the user gave no due date, next action or waiting-on target, leave those empty. `state` is `ready`; `priority` defaults to `1` and is set to `0` only if the user says it is urgent. Do not invent content to fill out the card.

### 3.2 Switching state

- **At most one card is `running` at a time; it is the user's main line of work.** When the user does something else on the side while waiting for AI output, do not switch cards, and do not remind the user to switch cards for it; anything worth keeping goes into that card's notes (user, 2026-10-21: they often do other things while waiting for AI, and switching cards minute by minute neither matches what actually happens nor is of any use). When the user wants to change the main line to B, or will be away from A for a long time, while A is still running: first ask the user for A's next action (the first thing to do on coming back), store their words in A and move A back to `ready`; only then can B start. This is the whole switch ritual: do not ask for a breakpoint (2026-10-29); a finding the user wants kept goes into A's notes. An `errand` does not require a next action; a `standing` card needs only its position pointer. Both the CLI and the server refuse to run a second card at the same time; they do not switch the original card out automatically.
- A card whose next action was written 7 or more days ago (`derived.nextActionAt`; the page shows "Stale Nd" and a Re-enter button) must be re-entered; it must not resume by following the old next action. Before starting, ask the user to rewrite `nextAction`; the AI must not write it for them. The clock restarts only when the text changes. Do not force this procedure on errands or standing cards. (Until 2026-10-29 the clock ran from the breakpoint.)
- Before the AI sets `blocked`, it must know whom or what the user is waiting on; if the user has not said, ask. When unblocking, clear any `waitingOn` that no longer applies.
- To deliver, use `python3 boardctl.py set <card> state=done`; the command appends a state event. Delivered cards stay in the service data and are not moved anywhere.
- On the page, the user can deliver (Deliver) or block (Block) a ready card directly. Blocking from the page allows an empty `waitingOn`, and the page marks it as not filled in; this exception does not allow the AI to guess what the card is waiting on.
- An ordinary delete on the page (Move to trash) puts the card in `trashed`; it can be restored and asks for no second confirmation. Delete forever inside the Trash is irreversible and requires confirmation. The AI must not permanently delete a card on its own; it must first get the user's explicit authorization.
- A state change must append a `{ts, kind: "state", to}` event and refresh `touchedAt`; when a card leaves `running`, `lastActiveId` is updated at the same time. Do it through the CLI or the page; never change only the current `state` and leave out the history.

**6-hour fallback:** every 60 seconds the service checks the running card; once it exceeds the cap, the card is moved back to `ready` automatically, appending `{kind: "state", to: "ready", auto: true}`. The event's `ts` is "the time it entered running plus 6 hours", not the time the overrun was noticed; if it is noticed late, `noticedAt` is recorded as well. This way, delays such as a service restart do not create fake sessions of a dozen hours or more.

The cap is defined by `CAP_MS` in `board_schema.py`; `SESSION_CAP_MS` in `web/js/rank.js` must match it. The auto-pause writes **not a single word into the next action (or the breakpoint)**; the card shows "Auto-paused", and the user updates the next action on return. It is an internal standard operation of the service (`board.sweep`), shares the write lock and transaction with API writes, and is also recorded in the operation log.

### 3.3 Recording a line of progress or a stray thought

`python3 boardctl.py note <card> 'note text'` appends a `note` event to the card. The text may be the user's own words or a one-sentence condensation that does not change the meaning; existing notes must not be modified.

If it is something to do and you are not sure which card it belongs to, use `python3 boardctl.py todo add 'text'` without `card=`; do not guess where it belongs. `inbox.move` (append an entry to a card as a note and remove it) still exists for the CLI and old clients; the page no longer offers it, because linking a to-do to a card (`card`) keeps it a to-do.

When writing any note, deliverable or question, the first mention of a file, person, meeting or conclusion must be made clear in that same sentence. A reader who has only this card must still be able to resolve every reference. Do not write things like "the old card" or "the approach from last time" that cannot be checked on their own.

Handle vague wording from the user in three ways: for omitted information, ask the user and then fill it in; mark things the user has not yet decided as undecided; keep the questioning tone of statements in which the user deliberately left room. Never store "could we add X?" as "decided to add X".

### 3.4 Long-term tasks

The executor must not design the scope and acceptance of a long-term task on its own. First the user discusses the scope with the model responsible for planning, sets up a Research Log if needed, and together they settle the deliverable and the next action; only then does the task go on the board. If the user explicitly asks for a card first, create at most a `ready` placeholder card per §3.1, leave `log` empty, and do not present it as finished planning.

For a task whose results the user cannot yet judge, the first stage is to build a verifiable acceptance checklist, and only then to execute. Do not substitute "the AI thinks it is done" for acceptance criteria.

### 3.5 Queueing, and "optimizations" you must not make

Urgency is defined by `urgency()` in `web/js/rank.js`:

```text
u = [6,3,1][priority]
  + min(idle days × 0.2, 2)
  + (has due: 3, plus proximity: overdue 10 / tomorrow 8 / within 3 days 5 / within 7 days 3 / within 14 days 1 / later 0)
  + (blockingOthers non-empty: 4)
```

Having a due date adds 3 by itself, so that a task with an explicit due date is not pushed back merely because another card has been sitting idle for a long time. Next-action staleness and the expected amount of the user's judgment time a task will take are not urgency terms.

Do not reorder the `threads` array to queue tasks. Only within the same urgency band, 1.0 wide, does the page use context affinity to break ties: tasks that share a Research Log with the card `lastActiveId` points to go first. Context affinity does not enter the urgency score and does not cross bands.

Do not clean up, merge or polish existing `events` entries.

### 3.6 Questions, and the retired card steps

A card's `plan` holds only its open **questions**: `{kind: "q", name, status: "open" | "decided", answer?, at?, blockedBy?}`, for things where you must get a result before you know which way to go. No percentage is shown; the page lists them under "Questions" only when a card has one.

- A question's `answer` can only be something the user said; if the user has not decided, it is `open`. Answer with `python3 boardctl.py q <card> <n> --decide '…' --user-verbatim`.
- The user's tentative ideas and reservations are recorded as they are, as `open`, keeping the questioning tone. Do not force parallel questions into a fixed chain of stages.
- To write the questions whole, use `python3 boardctl.py plan <card> --set plan.json` (content `{"tracks": [...]}`); if the plan was changed elsewhere after you read it, the write is rejected rather than overwriting it.

**Card steps are retired (2026-10-29).** Until then a `flow` track held tickable steps ("Progress", a quest tracker on the Running panel); now anything to do is a to-do (§1.1). The server refuses a `flow` track in `card.create` / `card.update` and the `plan.step` operation (422, naming the way forward); the CLI `step` command only prints it. What used to go into a step goes into a to-do of the card: `todo add '<text>' card=<card>`, ticked with `todo done`. For an exploratory thread, the old rule still holds in to-do form: write down only the next one or two things that are already settled, and write a direction not yet thought through as a `q`, not as a to-do. The steps a card carried are kept in its `retiredSteps` for undo; `plan <card>` and `show` summarize them in one line. If an old backup brings steps back, run `python3 boardctl.py retire-steps --all --dry-run`, then without `--dry-run`.

`roadmap` is retired; do not reintroduce it, nor card steps.

### 3.7 Delivered cards

Delivered cards remain in the same service data. The Delivered view sorts them by the derived `doneAt`, newest first, groups them by month, and renders them the same way as cards in the ready queue; by default it shows the latest 30, and the rest expand on demand. A month header shows the number of all cards in that month, not just the number currently drawn. Reopen returns a card to the ready queue and records the state change.

Do not save delivered cards as notes and then remove them from the board, and do not create a second writable archive. When page cost needs to be kept down, use pagination; do not split off a copy of the task data that could diverge.

## 4. Standards for the lines on a card

- **Deliverable `deliverable`:** write an acceptance criterion. Acceptable: "Compare five models under one shared setting and deliver reproducible results"; not acceptable: "Run experiments, organize data, write docs". The AI may rewrite the user's description as an acceptance criterion.
- **Breakpoint `breakpoint`:** no longer asked for (2026-10-29). When the user says what has been ruled out and on what evidence, add it as a note on the card in their words (`note`); do not prompt for it as a field.
- **Next action `nextAction`:** the only line the switch ritual asks for. Record the direct action the user gave, for example "Check whether max_len in the eval config is 512". If it is a decision, ask the user to state the options and the criterion; do not write it for them as "keep pushing forward". The page shows it right after the card title.

## 5. Red lines

- Do not guess, invent or reuse board state from memory; only data the service has just returned counts.
- Do not write or polish the user's next action (or a breakpoint) for them; question answers likewise may store only what the user said. If the user's words are rough, store them as they are.
- Do not write a possibility the user left open as a decision. Mark uncertain progress as "to be confirmed"; do not fabricate progress.
- Do not rewrite an existing Research Log; when something needs recording, only insert a new dated section at the top, keeping the newest first.
- Do not "discover" tasks on your own from a Research Log, a chat or a notes vault and create cards for them. Creating a card must be grounded in the user's intent.
- When the user asks only for card changes, do not change the web page, the service code or the systemd configuration on your own initiative.
- Do not commit real tasks, history, credentials or backups to the code repository; do not publish board data to other AI platforms or to public web pages.
- Do not write local JSON directly because of a network failure, do not create a second writable task source, and do not use Git to merge or synchronize live task state.
- Do not truncate or rewrite events, do not lose card IDs, do not bypass the standard operations to change the database directly, and do not run test writes, wipes or deletion tests on the real board.

## 6. Service maintenance and migration

A container deployment is managed by `single-core-board-compose.service`; a native deployment uses `single-core-board.service`; the two must never write to the same data directory at the same time. Clients use the configured, controlled service entry point; do not require each client machine to start its own service. Setting up authentication and the access address, checking service status, and the backup timers (local snapshot hourly, push weekly) are described in [connection setup](SETUP.md).

`GET /events` provides SSE to each browser tab. After each successful operation commits, a broadcast goes out automatically and open pages re-read right away; `POST /notify` only broadcasts a refresh notification. Both require authentication. The page does not cache unsaved edits: an operation has either been accepted by the service, or the page says clearly that it was not saved.

Wherever a client runs, it talks to the same service. If the connection fails, stop; once the connection is back, re-read, and do not write old client state back.

Migration and restore are maintenance operations that need explicit authorization, and must be checked in the following order:

1. Identify the write sources on the old service, in browsers and on each machine, confirm any unsynchronized data, and stop writes through the old entry points; explicitly mark any source you could not confirm as unverified. Old-version pages that are still open can no longer write the whole board (410), but they show a write failure that reminds the user to reload.
2. Save the original complete snapshot and check its digest, card count, event count and inbox count, as well as `savedAt` and `lastActiveId`. Include `done` and `trashed`; do not migrate only the ready cards.
3. With the service stopped, import into the data directory outside the repository with `board-admin.py import-json <board.json>`. The import immediately exports once and compares the result card by card with the original file; on any mismatch it deletes the new database and reports an error. Keep the permission restrictions on credentials and backups.
4. Start the service and check the completeness of what it reads back, login, the access path and both backup timers; verify writes on a separate instance with fictional data.
5. Point the web page, the CLI and the AI at one operation entry point, and only then explicitly announce that the takeover is complete. Keep the old snapshot as a read-only backup; do not keep running a second writable board.

Scheduled snapshots (exported in the `board.json` shape) and the `undo` entries in the operation log are recovery material, not a data source clients may write to. A backup on the same machine cannot replace an off-machine backup: local snapshots are hourly, while the off-site copy is pushed only weekly, so the off-site copy lagging by up to one week is by design; if it lags more than that, check the push. To tell whether there are commits that have not been pushed, check whether `local_commit` and `remote_commit` are the same. To restore the whole board, stop the service first, save the current data with `board-admin.py export-json`, check the snapshot you intend to restore, then run `import-json --replace` and start the service. When only one mistaken change needs to be recovered, check `boardctl.py ops` first and change it back with a standard operation; do not roll back the whole board.

## 7. Decision trail and knowledge notes

A card's `events[]` is the complete decision trail: notes, every new value of the breakpoint and next action, and every state change all stay in one place. The `replaced`, `session` and `approx` markers that came from migrating the old version must be kept. Do not rebuild, truncate or re-estimate history because the deployment method changed.

The trail stays in the service's database; it is not periodically copied into a Research Log, and no separate set of Markdown work records is created. The page can expand the notes, and the CLI `show <card>` can read the full events.

If the user has linked a knowledge-notes vault, at the end of each round of discussion you may identify judgments that still hold outside this project, and ask the user whether they are worth writing up as knowledge notes. Only after the user agrees, write them according to the vault's own rules, keeping the user's own words. The working process stays on the card; the original notes are not deleted because a knowledge note was produced. The standalone board does not require any particular notes directory or vault instruction file to exist.

## 8. Reporting and proactive questions

Start a report with the result; make clear any card, field or file the first time it is mentioned. List each change on one line as "card name · field · old value → new value". Relay questions to and answers from the user verbatim; state plainly what is unfinished or unverified, and never substitute "handled" for the actual result.

A work report keeps only what affects the user's judgment. It can be organized in the following four boxes; omit empty boxes:

| Box | Content | Limit |
|---|---|---|
| Done | The part of the deliverable achieved, or the to-dos ticked. | 1 line. |
| Stuck on | What the user said blocks the card, from its next action or latest note; without the user's own words, only prompt the user to say it. | 1 line. |
| For you to decide | Questions that have two viable paths and that existing authorization cannot settle. | 3 items, 1 line each. |
| Done on my own initiative | Things done without asking separately that change later options. | 2 items. |

When all four boxes are empty, do not report just for the sake of reporting. Routine file reading, API wiring, code style and the trial-and-error process do not go into the report. Implementation details with only one viable path are handled by the AI; only a fork that affects the choice is handed to the user to decide.

Ask proactive questions only in passing, when the user is already discussing a task with the AI; at most two cards at a time, chosen in this order: the card that just left `running`; cards whose next action was written more than 7 days ago; cards with required fields left empty. **Never ask for a breakpoint; never press for a next action on an `errand`.** Do not scan and interrogate card by card, and do not interrupt the user just to ask.
