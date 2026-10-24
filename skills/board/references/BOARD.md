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
- `inbox` is an array of `{ "id": uuid, "ts": epoch-ms, "text": "..." }` objects. The page moves, promotes and deletes entries by `id`; it must not be written as an array of strings.
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
| `breakpoint` | Breakpoint | The current state that only the user knows and that decays over time; every rewrite appends a `bp` event. |
| `nextAction` | Next action | An action that can be carried out directly; if it is a decision, write it as "Decide: A or B, by criterion C". Every rewrite appends an `na` event. |
| `waitingOn` | Whom I am waiting on | When the AI sets `blocked`, it must hold content the user provided; empty in other states. For the exception when the user shelves a card on the page themselves, see §3.2. |
| `blockingOthers` | Who is waiting on me | A person's name or one sentence. The opposite direction to `waitingOn`; when non-empty, it adds 4 to urgency. |
| `lane` | Task type | `project` / `standing` / `errand`; determines the required information and how the card is queued. |
| `cadence` | Expected interval, in days, for a standing task | Integer number of days, used only by `standing`; empty for other types. |
| `log` | Research Log link | E.g. `[[Research notes]]`; multiple links are joined with ` · `. Linking a notes vault is not a prerequisite for running the board. |
| `plan` | Progress summary, i.e. the global position of this thread | `{ "summaryAt": epoch-ms, "tracks": [...] }`; when there is no summary, the field is not set. Track format: see §3.6. |
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

| Type | Task shape | Information the user must provide | Breakpoint and next action |
|---|---|---|---|
| `project` | Has an end point, runs across several periods, and is costly to resume after an interruption. | Title, deliverable, breakpoint, next action, what it is waiting on; record the due date if there is one. | The breakpoint keeps what has already been ruled out and why; the next action states an action. |
| `standing` | Has no end point, only a position in its progress. | Title and a position pointer. | On this type of card the two fields mean the same thing; one position pointer is enough. |
| `errand` | A one-off errand that can be finished in one sitting. | Title; the task itself is the deliverable. | Not required, and not asked for. |

Standing cards always go at the end of the ready queue, are shown semi-transparent, take no part in urgency ranking, and their row shows only how long they have been idle (since 2026-10-19; before that, a standing card past its `cadence` was pushed back into the main queue).

## 2. The standard flow for every write

1. **Re-read the service's current data** through the CLI or the authenticated API. Read only the target card or the skeleton projection; do not reconstruct the current state from memory, old output or a copy on disk.
2. Change only the target fields you have just read. Breakpoints, next actions and answers to questions must be the user's own words; see §5.
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
python3 boardctl.py step 'Example project' Verify 2
python3 boardctl.py q 'Example project' 1 --decide 'A decision the user gave in person' --user-verbatim
```

The project names, track names and text in these examples are placeholders; in real use, replace them with the card that actually matches and with the content the user provided. An ID prefix or a title substring also identifies a card; if it is ambiguous, give the full ID instead of guessing.

`breakpoint`, `nextAction` and the `answer` of a `q` cannot be written without `--user-verbatim`. The flag reminds the operator to check that the text is the user's own words; it cannot turn AI-drafted text into the user's words. The commands and flags currently available are those shown by `python3 boardctl.py --help` and each subcommand's `--help`. An operation the CLI does not offer must not be worked around by writing the data file directly; if a programmatic operation is truly needed, call only the standard operations listed in [OPS.md](OPS.md). `python3 boardctl.py ops --limit 20` lists the recent operation log: which client changed which card with which operation, and when. Start here when investigating a mistaken change or recovering deleted content.

`python3 boardctl.py notify` only sends an authenticated refresh notification to the configured service; it cannot be used to cover up a failed write.

## 3. Operations manual

### 3.1 A small task the user states in one sentence

Decide the `lane` first. A one-off favour a colleague asked for is usually an `errand`: fill in only the title; if the user said who is waiting, fill in `blockingOthers`.

Record only information the user has already given. You may rewrite the user's description as a one-sentence `deliverable` acceptance criterion; if the user gave no due date, breakpoint, next action or waiting-on target, leave those empty. `state` is `ready`; `priority` defaults to `1` and is set to `0` only if the user says it is urgent. Do not invent content to fill out the card.

### 3.2 Switching state

- **At most one card is `running` at a time; it is the user's main line of work.** When the user does something else on the side while waiting for AI output, do not switch cards, and do not remind the user to switch cards for it; anything worth keeping goes into that card's notes (user, 2026-10-21: they often do other things while waiting for AI, and switching cards minute by minute neither matches what actually happens nor is of any use). When the user wants to change the main line to B, or will be away from A for a long time, while A is still running: first ask the user for A's breakpoint and next action, store their words in A and move A back to `ready`; only then can B start. An `errand` does not require a breakpoint or next action; a `standing` card needs only its position pointer. Both the CLI and the server refuse to run a second card at the same time; they do not switch the original card out automatically.
- A card whose breakpoint was written 7 or more days ago must be re-entered (Re-enter); it must not resume by following the old next action. Before starting, ask the user to rewrite `nextAction`; the AI must not write it for them. Do not force this procedure on errands, which need no breakpoint.
- Before the AI sets `blocked`, it must know whom or what the user is waiting on; if the user has not said, ask. When unblocking, clear any `waitingOn` that no longer applies.
- To deliver, use `python3 boardctl.py set <card> state=done`; the command appends a state event. Delivered cards stay in the service data and are not moved anywhere.
- On the page, the user can deliver (Deliver) or shelve (Shelve) a ready card directly. Shelving allows an empty `waitingOn`, and the page marks it as not filled in; this exception does not allow the AI to guess what the card is waiting on.
- An ordinary delete on the page (Move to trash) puts the card in `trashed`; it can be restored and asks for no second confirmation. Delete forever inside the Trash is irreversible and requires confirmation. The AI must not permanently delete a card on its own; it must first get the user's explicit authorization.
- A state change must append a `{ts, kind: "state", to}` event and refresh `touchedAt`; when a card leaves `running`, `lastActiveId` is updated at the same time. Do it through the CLI or the page; never change only the current `state` and leave out the history.

**6-hour fallback:** every 60 seconds the service checks the running card; once it exceeds the cap, the card is moved back to `ready` automatically, appending `{kind: "state", to: "ready", auto: true}`. The event's `ts` is "the time it entered running plus 6 hours", not the time the overrun was noticed; if it is noticed late, `noticedAt` is recorded as well. This way, delays such as a service restart do not create fake sessions of a dozen hours or more.

The cap is defined by `CAP_MS` in `board_schema.py`; `SESSION_CAP_MS` in `web/js/rank.js` must match it. The auto-pause writes **not a single word into the breakpoint or next action**; the page prompts the user to fill them in on return. It is an internal standard operation of the service (`board.sweep`), shares the write lock and transaction with API writes, and is also recorded in the operation log.

### 3.3 Recording a line of progress or a stray thought

`python3 boardctl.py note <card> 'note text'` appends a `note` event to the card. The text may be the user's own words or a one-sentence condensation that does not change the meaning; existing notes must not be modified.

If you are not sure which card it belongs to, use `python3 boardctl.py inbox 'note text'`; do not guess where it belongs. When the page files an inbox entry under a thread (File under a thread), it appends a `note` event to that card and then removes the inbox entry.

When writing any note, deliverable or progress summary, the first mention of a file, person, meeting or conclusion must be made clear in that same sentence. A reader who has only this card must still be able to resolve every reference. Do not write things like "the old card" or "the approach from last time" that cannot be checked on their own.

Handle vague wording from the user in three ways: for omitted information, ask the user and then fill it in; mark things the user has not yet decided as undecided; keep the questioning tone of statements in which the user deliberately left room. Never store "could we add X?" as "decided to add X".

### 3.4 Long-term tasks

The executor must not design the scope and acceptance of a long-term task on its own. First the user discusses the scope with the model responsible for planning, sets up a Research Log if needed, and together they settle the deliverable, breakpoint and next action; only then does the task go on the board. If the user explicitly asks for a card first, create at most a `ready` placeholder card per §3.1, leave `log` empty, and do not present it as finished planning.

For a task whose results the user cannot yet judge, the first stage is to build a verifiable acceptance checklist, and only then to execute. Do not substitute "the AI thinks it is done" for acceptance criteria.

### 3.5 Queueing, and "optimizations" you must not make

Urgency is defined by `urgency()` in `web/js/rank.js`:

```text
u = [6,3,1][priority]
  + min(idle days × 0.2, 2)
  + (has due: 3, plus proximity: overdue 10 / tomorrow 8 / within 3 days 5 / within 7 days 3 / within 14 days 1 / later 0)
  + (blockingOthers non-empty: 4)
```

Having a due date adds 3 by itself, so that a task with an explicit due date is not pushed back merely because another card has been sitting idle for a long time. Breakpoint decay and the expected amount of the user's judgment time a task will take are not urgency terms.

Do not reorder the `threads` array to queue tasks. Only within the same urgency band, 1.0 wide, does the page use context affinity to break ties: tasks that share a Research Log with the card `lastActiveId` points to go first. Context affinity does not enter the urgency score and does not cross bands.

Do not clean up, merge or polish existing `events` entries.

### 3.6 Recomputing the progress summary

`breakpoint` is local state that decays and that only the user can write; `plan` is the stable global position, which the AI may draft from text already on the card and hand to the user for correction.

Build a summary only for a `project` that spans weeks. A `standing` card has only a position pointer, and an `errand` needs no summary. A card without a `plan` is normal.

`tracks` consists of flow tracks and questions, with no order among them:

| Type | When to use | Shape |
|---|---|---|
| `flow` | What it should produce is known before it is done, for example checking items against a list that is already fixed. | `{kind: "flow", name, steps: [{t, done, detail?}]}`, shown on the page as a task tracker (one item per row). |
| `q` | You must get a result first before you know which way to go. | `{kind: "q", name, status: "open" \| "decided", answer?, at?, blockedBy?}`; no percentage is shown. |

Do not disguise a question with an unknown outcome as a step that can be ticked off, and do not force parallel questions into a fixed chain of stages.

- A question's `answer` can only be something the user said; if the user has not decided, it is `open`.
- The user's tentative ideas and reservations are recorded as they are, as `open`, keeping the questioning tone.
- Step names use text already on the card; do not invent steps to be completed out of thin air.
- A step name is a short name for ticking off: it starts with a verb and covers one thing; aim for 15 characters or fewer; the server limit is 30 characters. Conditions, reasons, dates, criteria and sources go into that step's `detail`, which the page folds under "Details" below the track; do not put them in parentheses in `t` (user, 2026-10-21: "short and sharp, the very concise kind you tick off").
- The page draws a flow track as a task tracker: one item per row; the first unfinished item is the current target; finished items fold into "N done"; no total and no progress bar are shown. The user can add a step directly on the page (Enter) or delete a step (click twice).
- Write a thread in one of two ways (user, 2026-10-21: "some tasks just don't have a clear plan…the steps in between may still change"):
  - **Checklist type**: what needs doing is clear in advance, for example wrapping up a paper submission. List all the known steps.
  - **Exploratory type**: the next step depends on the result of the previous one, for example planning an internship. List only the next one or two items that are already settled, and add the next item after finishing one; write a direction that has not been thought through yet as a `q`; do not invent steps to fill out a list. This is rolling wave planning from project management: near-term work is written in detail, far-off work stays as questions, and the plan is filled in as you go.
  - When the AI drafts an exploratory thread, it lists at most two items that are already settled; when the next step is unclear, ask the user, or write it as a `q`.
- To write a whole plan, use `python3 boardctl.py plan <card> --set plan.json` (content `{"tracks": [...]}`); if the plan was changed elsewhere after you read it, the write is rejected rather than overwriting it.
- `summaryAt` is refreshed only when the track contents are actually checked or changed, never as a side effect of editing other fields.

When the user changes the breakpoint or next action, check the summary while you are at it. When the page shows "Summary behind", check it first. Before ticking a step, read `python3 boardctl.py plan <card>`; question answers still require the user's own words and `--user-verbatim`.

`roadmap` is retired; do not reintroduce it. The global position is carried by `plan`.

### 3.7 Delivered cards

Delivered cards remain in the same service data. The Delivered view sorts them by the derived `doneAt`, newest first, groups them by month, and renders them the same way as cards in the ready queue; by default it shows the latest 30, and the rest expand on demand. A month header shows the number of all cards in that month, not just the number currently drawn. Reopen returns a card to the ready queue and records the state change.

Do not save delivered cards as notes and then remove them from the board, and do not create a second writable archive. When page cost needs to be kept down, use pagination; do not split off a copy of the task data that could diverge.

## 4. Standards for the three lines

- **Deliverable `deliverable`:** write an acceptance criterion. Acceptable: "Compare five models under one shared setting and deliver reproducible results"; not acceptable: "Run experiments, organize data, write docs". The AI may rewrite the user's description as an acceptance criterion.
- **Breakpoint `breakpoint`:** keep the state the user knows, especially what has already been ruled out and on what evidence. You may prompt the user to state "the baseline runs end to end; which failure causes have been ruled out, and on what evidence; which one is suspected now". The AI may only ask; it must not complete or polish the answer for the user.
- **Next action `nextAction`:** record the direct action the user gave, for example "Check whether max_len in the eval config is 512". If it is a decision, ask the user to state the options and the criterion; do not write it for them as "keep pushing forward".

## 5. Red lines

- Do not guess, invent or reuse board state from memory; only data the service has just returned counts.
- Do not write or polish the user's breakpoint or next action for them; question answers likewise may store only what the user said. If the user's words are rough, store them as they are.
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
| Done | The part of the deliverable achieved, or the flow-track steps completed. | 1 line. |
| Stuck on | The current breakpoint the user provided; without the user's own words, only prompt the user to fill it in. | 1 line. |
| For you to decide | Questions that have two viable paths and that existing authorization cannot settle. | 3 items, 1 line each. |
| Done on my own initiative | Things done without asking separately that change later options. | 2 items. |

When all four boxes are empty, do not report just for the sake of reporting. Routine file reading, API wiring, code style and the trial-and-error process do not go into the report. Implementation details with only one viable path are handled by the AI; only a fork that affects the choice is handed to the user to decide.

Ask proactive questions only in passing, when the user is already discussing a task with the AI; at most two cards at a time, chosen in this order: the card that just left `running`; cards whose breakpoint was written more than 7 days ago; cards with required fields left empty. **Never press for a breakpoint or next action on an `errand`.** Do not scan and interrogate card by card, and do not interrupt the user just to ask.
