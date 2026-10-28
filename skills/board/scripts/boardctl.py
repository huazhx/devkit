#!/usr/bin/env python3
"""boardctl — the board's operations tool (projected reads + targeted writes).

The client reads tasks through the API and prints only the fields needed; the full JSON never has
to enter a model's context.

Every write is a standard operation via `POST /api/ops` (docs/OPS.md): each one names a single card,
field or inbox item and never uploads the whole board. Data rules such as state changes, the event
ledger and plan stamps run only on the server. If the service is unreachable the tool exits and
writes no local file. Connection setup: README.md.

Usage
  boardctl.py list                      # skeleton of every card
  boardctl.py list --due                # sorted by due date
  boardctl.py show running              # the card that is running now
  boardctl.py show example-card         # an id prefix or a title substring works (ambiguity is an error)
  boardctl.py show example-project --no-log   # do not read events
  boardctl.py note example-project "Finished the synthetic-data check"
  boardctl.py set example-project due=2027-01-15 priority=0
  boardctl.py plan example-project      # the card's open questions (card steps are retired: to-dos are the checklist)
  boardctl.py q example-project 1 --decide "Take option A" --user-verbatim
  boardctl.py time --days 7             # where the time went (same rules as the page's "Time" view)
  boardctl.py todo add "Email X about Y" card=example-card due=2026-11-01   # a to-do; card / when / due / wait are optional
  boardctl.py todo list [--today]       # open to-dos, or today's picks in order with reasons
  boardctl.py todo set "Email X" wait="X's reply"   # an empty value clears a field
  boardctl.py todo pick "Email X" 1 "Needs X's reply before 11-02"   # Claude's order for today (BOARD_TZ, default Asia/Shanghai)
  boardctl.py todo done "Email X"       # --undo to untick
  boardctl.py inbox "A to-do"           # the same list (the inbox is the to-do catalog since 2026-10-29)
  boardctl.py ops --limit 20            # recent operation log (who changed what, and when)
  boardctl.py notify                    # only nudge the page to refresh

The field red line (`BOARD.md` §5) is unchanged: **never write a breakpoint or next action on the
user's behalf**. These two fields are written only with `--user-verbatim`, which is a reminder that
"this text must be the user's own words", not a permission bit.
"""

import argparse
import atexit
import json
import os
import ipaddress
import ssl
import sys
import time
import uuid
import urllib.error
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

import board_schema as S   # migrations / event ledger / derivations: the one copy of the rules
import board_package as P  # package version and auto-update (ARCHITECTURE §6)

ROOT = os.path.dirname(os.path.abspath(__file__))
CLIENT_CONFIG = os.path.expanduser(os.environ.get(
    "BOARD_CLIENT_CONFIG", "~/.config/single-core-board/client.json"))
try:
    with open(CLIENT_CONFIG, encoding="utf-8") as f:
        _client_config = json.load(f)
except FileNotFoundError:
    _client_config = {}
SERVER = os.environ.get("BOARD_URL", _client_config.get("url", "http://127.0.0.1:8765")).rstrip("/")
CA_FILE = os.environ.get("BOARD_CA_FILE", _client_config.get("ca_file"))


def transport():
    """Verify the configured CA and IP; plain HTTP is only for local instances."""
    url = urllib.parse.urlsplit(SERVER)
    if url.username or url.password or url.query or url.fragment:
        raise ValueError("BOARD_URL may contain only the scheme, host and port")
    if url.scheme == "https":
        return ssl.create_default_context(cafile=os.path.expanduser(CA_FILE) if CA_FILE else None)
    if url.scheme == "http":
        host = url.hostname or ""
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = host == "localhost"
        if local:
            return None
    raise ValueError("A remote board must use HTTPS; HTTP is only for isolated local instances")


def api_token():
    token = os.environ.get("BOARD_API_TOKEN")
    path = os.environ.get("BOARD_CREDENTIALS_FILE")
    if not token and not path:
        token = _client_config.get("token")
        path = _client_config.get("credentials_file")
    if not token:
        if path:
            with open(os.path.expanduser(path), encoding="utf-8") as f:
                token = json.load(f)["api_token"]
    if not token:
        die("No API credentials; configure BOARD_CLIENT_CONFIG or BOARD_CREDENTIALS_FILE.")
    return token


def request(path, data=None, method="GET", headers=None):
    context = transport()
    req = urllib.request.Request(SERVER + path, data=data, method=method,
        headers={"Authorization": "Bearer " + api_token(), **(headers or {})})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), NoRedirect())
    try:
        with opener.open(req, timeout=10) as response:
            _note_server_client(response.headers.get("X-Board-Client"))
            return json.load(response)
    except urllib.error.HTTPError as e:
        _note_server_client(e.headers.get("X-Board-Client"))
        raise


# ---- Auto-update (ARCHITECTURE §6) ----
# The server puts the version of the client it packages into every API response header. When it
# differs from the local managed installs, after the command finishes the client fetches
# GET /api/client from the same service, verifies it, and atomically replaces every local managed
# install. The connection config lives outside the install directory and is not touched.
SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
_server_client = None
_update_tried = False


def _note_server_client(version):
    global _server_client
    if version and not _server_client:
        _server_client = version


def auto_update_enabled():
    """On by default only for the really configured service: tests and temporary instances change
    the address or credentials through environment variables, and their package must not overwrite
    the local installs."""
    flag = os.environ.get("BOARD_AUTO_UPDATE")
    if flag is not None:
        return flag == "1"
    if _client_config.get("auto_update") is False:
        return False
    return not any(os.environ.get(k) for k in (
        "BOARD_URL", "BOARD_CLIENT_CONFIG", "BOARD_CA_FILE", "BOARD_API_TOKEN", "BOARD_CREDENTIALS_FILE"))


def maybe_update():
    """Update if needed; return whether anything was replaced. A failure prints one line and does
    not affect the result of this command."""
    global _update_tried
    if _update_tried or not _server_client or not auto_update_enabled():
        return False
    _update_tried = True
    try:
        stale = [r for r in P.install_roots(SKILL_ROOT) if P.version_of(P.files_of(r)) != _server_client]
        if not stale:
            return False
        files = P.check(request("/api/client"), expected=_server_client)
        for root in stale:
            P.install(files, root)
    except Exception as e:
        print(f"boardctl: client auto-update did not finish ({e}); this command is not affected. "
              "To update by hand, see references/SETUP.md.", file=sys.stderr)
        return False
    print(f"boardctl: updated the local board client from the server ({len(stale)} installs, "
          f"version {_server_client}).", file=sys.stderr)
    return True


def _version_mismatch(e):
    """This rejection is because the client and server disagree on the interface (not a data or
    permission problem)."""
    err = _error_body(e).get("error")
    return (e.code in (405, 410) or (e.code == 404 and err != "not_found")
            or (e.code == 400 and err == "api_version"))

# Fields `set` may write. id / events / plan / createdAt etc. go through their own commands or may not be changed.
SETTABLE = {
    "title", "state", "priority", "due", "deliverable", "breakpoint", "nextAction",
    "waitingOn", "blockingOthers", "lane", "cadence", "log",
}
INT_FIELDS = {"priority", "cadence"}
USER_ONLY = {"breakpoint", "nextAction"}
STATES = {"ready", "running", "blocked", "done", "trashed"}
LANES = {"project", "standing", "errand", "claude"}



def fmt_dur(ms):
    """Same format as fmtDur in rank.js: always NhMMm, so a column scans easily."""
    m = max(0, int(round(ms / 60000)))
    return f"{m // 60}h{m % 60:02d}m"


def die(msg, code=1):
    print(f"boardctl: {msg}", file=sys.stderr)
    sys.exit(code)


def now_ms():
    return int(time.time() * 1000)


def load():
    """The service is the only read source; a network failure does not fall back to an old snapshot."""
    try:
        return S.migrate_board(request("/board.json"))
    except Exception as e:
        die(f"Cannot read the service {SERVER} ({e}); no local snapshot was read.")


def resolve(board, ref):
    """id → id prefix → exact title → title substring; 'running' works as a state keyword."""
    cards = board["threads"]
    for pred in (
        lambda c: c.get("id") == ref,
        lambda c: str(c.get("id", "")).startswith(ref),
        lambda c: c.get("title") == ref,
        lambda c: ref.strip() == c.get("state"),
        lambda c: ref.lower() in str(c.get("title", "")).lower(),
    ):
        hits = [c for c in cards if pred(c)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            die("More than one card matches; give a longer id prefix:\n"
                + "\n".join(f"  {c['id'][:8]}  {c['title']}" for c in hits))
    die(f"No card matches '{ref}'")


API_VERSION = 1   # interface version of docs/OPS.md; the server answers 400 if it does not know it


def _error_body(e):
    if not hasattr(e, "board_body"):    # the response body can be read only once; both the check and the message need it
        try:
            e.board_body = json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            e.board_body = {}
    return e.board_body


def _op_failure(e):
    """Turn a server rejection into "what happened, and what to do next". Every branch means
    nothing was written this time."""
    body = _error_body(e)
    err, detail = body.get("error"), body.get("detail") or ""
    if e.code == 400 and err == "api_version":
        return f"Server and client versions do not match ({detail}); nothing written. Update the CLI and try again."
    if e.code == 400:
        return f"The server refused this operation (invalid arguments; nothing written): {detail or err}"
    if e.code == 401:
        return "Authentication failed (HTTP 401): check the API token configuration; nothing written."
    if e.code == 404 and err == "not_found":
        return f"The object to change was not found (nothing written): {detail}. Check again with list / show."
    if e.code in (404, 405, 410):
        # An old service has no /api/ops (404), or a new service refuses an old whole-board write (410): the versions do not match
        return (f"Server and client versions do not match (HTTP {e.code}{': ' + detail if detail else ''}); "
                "nothing written. Update the CLI or the service and try again.")
    if e.code == 409 and err == "op_id_reused":
        return f"The same opId was sent with a different operation (a client problem; nothing written): {detail}"
    if e.code == 409:
        # No automatic retry: a value just changed elsewhere needs a person to look before deciding (OPS.md §5)
        return (f"The target was just changed elsewhere (nothing written): {detail}. Read it again with show, "
                "check that the change you want still holds, then run the command again; do not retry automatically.")
    if e.code == 422:
        return f"Breaks a board rule (nothing written): {detail}"
    return f"The server returned HTTP {e.code} (nothing written): {detail or err or e.reason}"


def op(name, args):
    """Send one standard operation (POST /api/ops) and return the server's 200 response body.

    Each operation gets a fresh opId. On a network error it retries once with the **same** opId: the
    server deduplicates by opId, so "the first attempt was written and only the response was lost"
    cannot record a note twice. An explicit server rejection (4xx) is not retried; it exits with an error."""
    body = json.dumps({"api": API_VERSION, "opId": str(uuid.uuid4()), "op": name, "args": args},
                      ensure_ascii=False).encode()
    headers = {"Content-Type": "application/json", "X-Client-Id": "boardctl"}
    for attempt in (1, 2):
        try:
            return request("/api/ops", data=body, method="POST", headers=headers)
        except urllib.error.HTTPError as e:
            # Interface mismatch: after updating the client from the server, re-run this command once with the new version (the rejected attempt wrote nothing)
            if _version_mismatch(e) and not os.environ.get("BOARD_CLIENT_REEXEC") and maybe_update():
                print("boardctl: re-running this command with the new client.", file=sys.stderr, flush=True)
                sys.stdout.flush()
                os.environ["BOARD_CLIENT_REEXEC"] = "1"
                os.execv(sys.executable, [sys.executable, *sys.argv])
            die(_op_failure(e))
        except ValueError as e:      # configuration error (e.g. HTTP on a public address): retrying makes no sense
            die(f"Writing to the service failed ({e}); no local file was changed.")
        except Exception as e:
            if attempt == 2:
                die(f"Writing to the service failed ({e}); retried once with the same opId. No local file "
                    "was changed; check with show whether this operation took effect.")
            time.sleep(1)


def changed_card(result, card_id):
    """The new view of this card in an operation response (None if it did not change)."""
    for c in (result.get("changed") or {}).get("cards") or []:
        if c.get("id") == card_id:
            return c
    return None


def written(result):
    """A one-line write receipt: the server revision; on a repeated submission, says it was not run again."""
    if result.get("duplicate"):
        return f"the server had already run this operation; it was not run again (rev={result.get('rev')})"
    return f"written to the service (rev={result.get('rev')}); the page refreshed itself"


def derived_of(card):
    """Derived timestamps: prefer the server's derived values (the rules exist once, on the server);
    derive locally only when an old service does not send them."""
    d = card.get("derived")
    if isinstance(d, dict):
        return d
    return {"breakpointAt": S.breakpoint_at(card), "nextActionAt": S.next_action_at(card),
            "enteredAt": S.entered_at(card), "doneAt": S.done_at(card),
            "sessions": S.sessions(card)}


# ---------------------------------------------------------------- reads

def cmd_list(a):
    board = load()
    cards = board["threads"]
    if a.state:
        cards = [c for c in cards if c.get("state") == a.state]
    else:   # cards in the Trash are not listed by default; use --state trashed to see them
        cards = [c for c in cards if c.get("state") != "trashed"]
    if a.lane:
        cards = [c for c in cards if c.get("lane") == a.lane]
    if a.due:
        cards = sorted(cards, key=lambda c: (c.get("due") or "9999", c.get("priority", 9)))
    else:
        cards = sorted(cards, key=lambda c: (c.get("state") != "running", c.get("priority", 9)))

    if a.json:
        print(json.dumps([{
            "id": c.get("id"), "title": c.get("title"), "state": c.get("state"),
            "priority": c.get("priority"), "due": c.get("due"), "lane": c.get("lane"),
            "nextAction": c.get("nextAction", ""),
        } for c in cards], ensure_ascii=False, indent=2))
        return

    for c in cards:
        print(f"{str(c.get('id'))[:8]:<9}{str(c.get('state')):<9}"
              f"P{c.get('priority')}  {str(c.get('due') or '—'):<12}"
              f"{str(c.get('lane') or '—'):<10}{c.get('title')}")
        na = (c.get("nextAction") or "").replace("\n", " ")
        if na:
            print(f"{'':<9}↳ {na[:72]}{'…' if len(na) > 72 else ''}")
    print(f"\n{len(cards)} / {len(board['threads'])} cards  ·  rev={board.get('rev')}  ·  savedAt={board.get('savedAt')}")


def cmd_show(a):
    card = resolve(load(), a.ref)
    # Include the derived timestamps: v2 no longer stores them as fields, but whoever reads the card (and the AI) needs them.
    # The session list is left out: like events, it grows without bound; use `time` for durations.
    full = derived_of(card)
    derived = {k: full.get(k) for k in ("breakpointAt", "nextActionAt", "enteredAt", "doneAt",
                                        "trashedAt") if full.get(k)}
    if card.get("state") == "trashed" and full.get("restoreTo"):
        derived["restoreTo"] = full["restoreTo"]
    if isinstance(card.get("retiredSteps"), dict):
        # the steps kept only so the 2026-10-29 change can be undone: not live, so not shown as steps
        card = dict(card, retiredSteps=retired_line(card))
    if a.no_log:
        # events is an untruncated ledger and should not be dumped into the context whole; use `time` for durations
        card = {k: v for k, v in card.items() if k != "events"}
    elif a.events is not None:
        if a.events < 0:
            die("--events must be an integer of at least 0")
        events = card.get("events", [])
        card = dict(card, events=events[-a.events:] if a.events else [],
                    eventsOmitted=max(0, len(events) - a.events))
    print(json.dumps(dict(card, derived=derived), ensure_ascii=False, indent=2))


def retired_line(card):
    r = card.get("retiredSteps") or {}
    steps = sum(len(t.get("steps") or []) for t in r.get("tracks") or [])
    from zoneinfo import ZoneInfo   # the user's day, as in local_today()
    tz = ZoneInfo(os.environ.get("BOARD_TZ", "Asia/Shanghai"))
    at = datetime.fromtimestamp(r["at"] / 1000, tz).strftime("%Y-%m-%d") if r.get("at") else "?"
    return (f"{steps} card steps retired on {at} ({len(r.get('todos') or [])} became to-dos: todo list); "
            "kept on the card only so the change can be undone")


def cmd_plan(a):
    board = load()
    card = resolve(board, a.ref)
    plan = card.get("plan")
    if a.set:
        # Replace this card's whole plan (one card.update with if: if the plan was changed elsewhere after the read, it is refused, not overwritten)
        try:
            src = sys.stdin.read() if a.set == "-" else open(a.set, encoding="utf-8").read()
            new = json.loads(src)
        except (OSError, ValueError) as e:
            die(f"Cannot read the plan file ({e})")
        new = {"tracks": new.get("tracks")} if isinstance(new, dict) else new
        result = op("card.update", {"id": card["id"], "set": {"plan": new}, "if": {"plan": plan}})
        print(f"Updated the plan of '{card['title']}'. {written(result)}")
        return
    tracks = (plan or {}).get("tracks", [])
    for tr in tracks:
        if tr.get("kind") == "q":
            print(f"q     {tr['name']}   [{tr.get('status')}]"
                  + (f"\n        → {tr['answer']}" if tr.get("answer") else ""))
    flows = [t for t in tracks if t.get("kind") == "flow"]
    if flows:   # only on a card not yet migrated (e.g. after importing an old backup)
        print(f"{sum(len(t.get('steps') or []) for t in flows)} card steps not yet retired; "
              f"run: retire-steps '{card['title']}'")
    if card.get("retiredSteps"):
        print(retired_line(card))
    mine = [i for i in board.get("inbox", []) if i.get("card") == card["id"] and not i.get("doneAt")]
    print(f"{len(mine)} open to-dos belong to this card (todo list)")
    if not tracks and not card.get("retiredSteps"):
        print(f"'{card['title']}' has no questions")


def cmd_time(a):
    """Where the time went: same rules as the page's "Time" view (the time aggregation in js/rank.js):
    ① a session that crosses midnight counts whole on the day it started, not split; ② a session
    whose length equals the cap exactly is a capped estimate from the auto-pause and is marked separately."""
    board = load()
    now = now_ms()
    today = datetime.fromtimestamp(now / 1000).replace(hour=0, minute=0, second=0, microsecond=0)
    rows, by_start = [], {}
    for i in range(a.days):
        d = today - timedelta(days=i)
        row = {"day": d, "total": 0, "capped": 0, "count": 0, "cards": []}
        rows.append(row)
        by_start[int(d.timestamp() * 1000)] = row

    def day_key(ms):
        return int(datetime.fromtimestamp(ms / 1000).replace(
            hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)

    for c in board["threads"]:
        d = derived_of(c)
        entries = [(s["ts"], s["out"], s["capped"]) for s in d.get("sessions") or []]
        st = d.get("enteredAt")
        if S.valid_start(st, now):
            entries.append((st, now, False))       # the session running now counts too
        for ts, out, capped in entries:
            row = by_start.get(day_key(ts))
            if row is None:
                continue                            # outside the window
            ms = max(0, int(out - ts))
            row["total"] += ms
            row["count"] += 1
            if capped:
                row["capped"] += ms
            hit = next((x for x in row["cards"] if x["id"] == c.get("id")), None)
            if hit is None:
                hit = {"id": c.get("id"), "title": c.get("title"), "ms": 0, "capped": 0}
                row["cards"].append(hit)
            hit["ms"] += ms
            if capped:
                hit["capped"] += ms

    t = rows[0]
    print(f"Today {t['day'].strftime('%-m/%-d')}   total {fmt_dur(t['total'])}, {t['count']} "
          + ("switch" if t["count"] == 1 else "switches")
          + ("   (includes capped estimates)" if t["capped"] else ""))
    if not t["cards"]:
        print("  (nothing recorded yet: the ledger starts at the first card switch; the past is not backfilled)")
    for c in sorted(t["cards"], key=lambda x: -x["ms"]):
        print(f"  {fmt_dur(c['ms']):>8}  {c['title']}"
              + ("   ← includes a capped estimate (reached the cap, not measured)" if c["capped"] else ""))
    print(f"\nLast {a.days} days")
    for r in rows:
        top = " · ".join(x["title"] for x in sorted(r["cards"], key=lambda x: -x["ms"])[:3])
        print(f"  {r['day'].strftime('%-m/%-d'):<5} {fmt_dur(r['total']):>8}  {r['count']:>2}x  {top}"
              + ("   (includes capped)" if r["capped"] else ""))


# ---------------------------------------------------------------- writes

def cmd_note(a):
    card = resolve(load(), a.ref)
    result = op("note.add", {"id": card["id"], "text": a.text})
    fresh = changed_card(result, card["id"]) or card
    print(f"Noted on '{card['title']}' ({len(S.notes(fresh))} notes now); {written(result)}")


def cmd_inbox(a):
    if a.list:
        if a.text:
            die("Do not give text to write while listing the inbox")
        if a.limit < 1:
            die("--limit must be greater than 0")
        items = load().get("inbox", [])
        selected = items[:a.limit]
        if a.json:
            print(json.dumps(selected, ensure_ascii=False, indent=2))
        else:
            for item in selected:
                text = str(item.get("text", "")).replace("\n", " ")
                print(f"{str(item.get('id', ''))[:8]}  {text[:160]}{'…' if len(text) > 160 else ''}")
            print(f"\nShowing {len(selected)} / {len(items)} items; --limit changes the number")
        return
    if not a.text or not a.text.strip():
        die("Give the text to add, or use inbox --list to view")
    result = op("inbox.add", {"text": a.text.strip()})
    inbox = (result.get("changed") or {}).get("inbox")
    count = f" ({len(inbox)} items now)" if isinstance(inbox, list) else ""
    print(f"Added to the inbox{count}; {written(result)}")


# ---------------------------------------------------------------- to-dos (2026-10-29)
# The inbox is the to-do catalog (OPS.md §4 inbox.*, BOARD.md §1). Claude sorts items (card, dates) and picks today's few.
# "Today" is the user's local day: BOARD_TZ, default Asia/Shanghai, so a pick made from a server in another zone lands on the page's day.

def local_today():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(os.environ.get("BOARD_TZ", "Asia/Shanghai"))).strftime("%Y-%m-%d")


def resolve_todo(board, ref):
    """id → id prefix → text substring, among all items (done ones too, so a tick can be undone)."""
    items = board.get("inbox", [])
    for pred in (lambda i: i.get("id") == ref,
                 lambda i: str(i.get("id", "")).startswith(ref),
                 lambda i: ref.lower() in str(i.get("text", "")).lower()):
        hits = [i for i in items if pred(i)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            die("More than one to-do matches; give a longer id prefix:\n"
                + "\n".join(f"  {str(i['id'])[:8]}  {i.get('text', '')[:60]}" for i in hits))
    die(f"No to-do matches '{ref}'")


def todo_line(board, it, day):
    card = next((c for c in board["threads"] if c.get("id") == it.get("card")), None)
    tags = []
    if it.get("pick", {}).get("day") == day:
        tags.append(f"today #{it['pick']['rank']}")
    if card:
        tags.append(card.get("title", ""))
    for k in ("when", "due"):
        if it.get(k):
            tags.append(f"{k} {it[k]}")
    if it.get("wait"):
        tags.append(f"waiting: {it['wait']}")
    if it.get("doneAt"):
        tags.append("done")
    text = str(it.get("text", "")).replace("\n", " ")
    return f"{str(it.get('id', ''))[:8]}  {text[:80]}{'…' if len(text) > 80 else ''}" + (f"   [{' · '.join(tags)}]" if tags else "")


def todo_fields(board, pairs):
    sets = {}
    for pair in pairs:
        if "=" not in pair:
            die(f"Fields are written key=value; got '{pair}'")
        k, v = pair.split("=", 1)
        if k not in ("text", "card", "when", "due", "wait"):
            die("A to-do field is one of text / card / when / due / wait")
        if k == "card" and v:
            v = resolve(board, v)["id"]
        sets[k] = v or None
    return sets


def cmd_todo(a):
    board = load()
    day = a.day or local_today()
    if a.action == "list":
        items = board.get("inbox", [])
        if a.today:
            items = sorted((i for i in items if i.get("pick", {}).get("day") == day), key=lambda i: i["pick"]["rank"])
        elif not a.all:
            items = [i for i in items if not i.get("doneAt")]
        if a.json:
            print(json.dumps(items, ensure_ascii=False, indent=2))
            return
        for it in items:
            print(todo_line(board, it, day))
            if a.today and it["pick"].get("why"):
                print(f"          why: {it['pick']['why']}")
        print(f"\n{len(items)} to-dos" + (f" picked for {day}" if a.today else "" if a.all else " open"))
        return
    if a.action == "add":
        if not a.args:
            die("todo add \"<text>\" [card=… when=… due=… wait=…]")
        text, sets = a.args[0], todo_fields(board, a.args[1:])
        result = op("inbox.add", {"text": text, **{k: v for k, v in sets.items() if v}})
        print(f"Added a to-do; {written(result)}")
        return
    if not a.args:
        die(f"todo {a.action} needs a to-do id prefix or a piece of its text")
    it = resolve_todo(board, a.args[0])
    if a.action == "set":
        sets = todo_fields(board, a.args[1:])
        if not sets:
            die("Give at least one key=value (an empty value clears the field)")
        result = op("inbox.update", {"id": it["id"], "set": sets})
    elif a.action == "pick":
        if len(a.args) < 3:
            die("todo pick <to-do> <rank> \"<why it sits here>\" [--day YYYY-MM-DD]")
        try:
            rank = int(a.args[1])
        except ValueError:
            die("rank must be a whole number")
        result = op("inbox.update", {"id": it["id"], "set": {"pick": {"day": day, "rank": rank, "why": a.args[2]}}})
    elif a.action == "unpick":
        result = op("inbox.update", {"id": it["id"], "set": {"pick": None}})
    elif a.action == "done":
        result = op("inbox.done", {"id": it["id"], "done": not a.undo})
    print(f"{a.action}: {todo_line(board, it, day).split('   [')[0]}; {written(result)}")


def cmd_create(a):
    title = a.title.strip()
    if not title:
        die("The title cannot be empty")
    # "Do not create a second card with the same title" is a usage rule for the AI (OPS.md §5), not a data rule, so it stays in the CLI
    if any(c.get("title") == title for c in load()["threads"]):
        die("A card with this title already exists; look at the existing cards first instead of creating a duplicate")
    args = {"title": title, "priority": a.priority, "lane": a.lane}
    for field in ("deliverable", "due", "log", "blockingOthers"):
        value = getattr(a, field)
        if value:
            args[field] = value
    result = op("card.create", args)
    card_id = (result.get("result") or {}).get("id")
    print(f"Created '{title}' ({card_id}); {written(result)}")


def cmd_set(a):
    board = load()
    card = resolve(board, a.ref)
    fields, new_state, changed = {}, None, []
    for pair in a.kv:
        if "=" not in pair:
            die(f"Expected k=v, got '{pair}'")
        k, v = pair.split("=", 1)
        if k not in SETTABLE:
            die(f"'{k}' is not a writable field. Writable: {', '.join(sorted(SETTABLE))}")
        if k in USER_ONLY and not a.user_verbatim:
            die(f"'{k}' is a red-line field (BOARD.md §5): it may hold only the user's own words. "
                f"Confirm this text comes from the user, then add --user-verbatim.")
        if k in INT_FIELDS:
            try:
                v = None if v in ("", "null") else int(v)
            except ValueError:
                die(f"{k} must be an integer, got '{v}'")
        if k == "state" and v not in STATES:
            die(f"state must be one of {'/'.join(sorted(STATES))}")
        if k == "lane" and v not in LANES:
            die(f"lane must be one of {'/'.join(sorted(LANES))}")
        old = card.get(k)
        if old == v or (old in (None, "") and v in (None, "")):
            continue
        if k == "state":
            new_state = v
        else:
            fields[k] = v
        changed.append(f"{k}: {old!r} → {v!r}")

    if not changed:
        print("No value changed; nothing written.")
        return
    if new_state == "running":
        # Only an early error, so fields are not written while the state is then refused: the server decides the real "only one running card at a time"
        others = [c["title"] for c in board["threads"]
                  if c.get("id") != card.get("id") and c.get("state") == "running"]
        if others:
            die(f"A card is already running: {', '.join(others)}. Switch out of it before starting this one; nothing was saved.")
    if fields:
        # if carries the old values just read: if these fields were changed elsewhere between the read and the write, the server refuses the whole operation (409)
        result = op("card.update", {"id": card["id"], "set": fields,
                                    "if": {k: card.get(k) for k in fields}})
    if new_state:
        try:
            result = op("card.state", {"id": card["id"], "to": new_state})
        except SystemExit:
            if fields:
                print(f"Note: {', '.join(fields)} written; only state was not changed to {new_state}.", file=sys.stderr)
            raise
    print(f"Changed '{card['title']}':")
    for line in changed:
        print(f"  {line}")
    print(written(result))


def cmd_step(a):
    """Retired with card steps (2026-10-29): to-dos are the only checklist. Kept so an old habit gets the way forward."""
    ref = f"'{a.ref}'" if a.ref else "<card>"
    die("Card steps are retired (2026-10-29): to-dos are the only checklist.\n"
        f"  Add one:  todo add \"<text>\" card={ref}\n"
        "  Tick one: todo done <to-do>   (todo list shows a card's to-dos; --undo unticks)")


def cmd_retire_steps(a):
    """One-time migration (2026-10-29): every card still carrying steps goes through plan.retireSteps on the server,
    which makes the open steps of a live card into to-dos and moves the steps to retiredSteps (the rules are there)."""
    board = load()
    if a.all == bool(a.ref):
        die("Give one card, or --all")
    cards = board["threads"] if a.all else [resolve(board, a.ref)]
    todo = [c for c in cards if any(t.get("kind") == "flow" for t in (c.get("plan") or {}).get("tracks", []))]
    if not todo:
        print("No card carries steps; nothing to do.")
        return
    for c in todo:
        flows = [t for t in c["plan"]["tracks"] if t.get("kind") == "flow"]
        live = c.get("state") in ("ready", "running", "blocked")
        open_steps = [st.get("t") for t in flows for st in t.get("steps") or [] if not st.get("done")]
        if a.dry_run:
            print(f"{c['id'][:8]}  {c.get('state'):<8} {c['title']}: {len(flows)} tracks, "
                  + (f"{len(open_steps)} open steps → to-dos" if live else "steps only moved (not a live card)"))
            for t in open_steps if live else []:
                print(f"          + {t}")
            continue
        result = op("plan.retireSteps", {"id": c["id"], "today": local_today()})
        r = result.get("result") or {}
        print(f"{c['id'][:8]}  {c['title']}: {r.get('tracks', 0)} tracks moved to retiredSteps, "
              f"{len(r.get('todos') or [])} to-dos" + ("; step details noted on the card" if r.get("note") else ""))
        for t in r.get("todos") or []:
            print(f"          + {t['text']}" + (f"   [when {t['when']}]" if t.get("when") else ""))
    if a.dry_run:
        print("\nDry run: nothing written.")
    else:
        print(written(result))


def cmd_q(a):
    card = resolve(load(), a.ref)
    plan = card.get("plan")
    if not plan:
        die(f"'{card['title']}' has no plan field")
    qs = [t for t in plan.get("tracks", []) if t.get("kind") == "q"]
    if not 1 <= a.n <= len(qs):
        die(f"There are only {len(qs)} q tracks:\n" + "\n".join(f"  {i}. {t['name']}"
                                                              for i, t in enumerate(qs, 1)))
    if a.decide and not a.user_verbatim:
        die("A q answer may only be the user's own words (§3.6); confirm, then add --user-verbatim.")
    result = op("plan.q", {"id": card["id"], "n": a.n, "decide": a.decide or None,
                           "today": a.today})
    print(f"'{card['title']}' q {a.n} → "
          f"{'decided' if a.decide else 'open'}; {written(result)}")


def cmd_ops(a):
    """Recent operation log: who (actor) changed what, when, with which operation. Start here to recover from a mistaken change."""
    if not 1 <= a.limit <= 500:
        die("--limit must be 1 to 500")
    try:
        data = request(f"/api/ops?limit={a.limit}")
    except urllib.error.HTTPError as e:
        die(_op_failure(e) if e.code in (404, 405, 410) else f"The server returned HTTP {e.code}")
    except Exception as e:
        die(f"Cannot read the operation log ({e})")
    entries = data.get("ops", []) if isinstance(data, dict) else data
    if a.json:
        print(json.dumps(entries, ensure_ascii=False, indent=2))
        return
    for e in entries:
        args = e.get("args")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                pass
        target = ""
        if isinstance(args, dict):
            target = args.get("id") or args.get("card") or args.get("from") or ""
        when = datetime.fromtimestamp((e.get("ts") or 0) / 1000).strftime("%m-%d %H:%M:%S")
        brief = json.dumps(args, ensure_ascii=False) if args is not None else ""
        print(f"{e.get('seq', ''):>6}  {when}  {str(e.get('actor') or ''):<6} "
              f"{str(e.get('op') or ''):<14} {str(target)[:8]:<9}"
              f"{brief[:80]}{'…' if len(brief) > 80 else ''}")
    print(f"\nShowing {len(entries)} entries (newest first); --json shows the full records")


def cmd_notify(_a):
    try:
        request("/notify", data=b"", method="POST")
        print("Page refreshed")
    except Exception as e:
        die(f"Service unreachable ({e})")


def cmd_backup_status(_a):
    try:
        print(json.dumps(request("/backup-status"), ensure_ascii=False, indent=2))
    except Exception as e:
        die(f"Cannot read the backup status ({e})")


# ---------------------------------------------------------------- CLI

def main():
    p = argparse.ArgumentParser(prog="boardctl", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("list", help="skeleton of every card")
    s.add_argument("--state"); s.add_argument("--lane")
    s.add_argument("--due", action="store_true", help="sort by due date")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("show", help="one card (id prefix / title substring / running)")
    s.add_argument("ref")
    group = s.add_mutually_exclusive_group()
    group.add_argument("--no-log", action="store_true")
    group.add_argument("--events", type=int, help="show only the last N events")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("plan", help="show the card's questions (q tracks); --set FILE replaces them whole (- reads stdin)")
    s.add_argument("ref")
    s.add_argument("--set", metavar="FILE", help="JSON of the new plan ({\"tracks\": [q tracks]}); card steps are retired, use todo add")
    s.set_defaults(fn=cmd_plan)

    s = sub.add_parser("time", help="where the time went (same rules as the page's \"Time\" view)")
    s.add_argument("--days", type=int, default=7)
    s.set_defaults(fn=cmd_time)

    s = sub.add_parser("note", help="append a note")
    s.add_argument("ref"); s.add_argument("text")
    s.set_defaults(fn=cmd_note)

    s = sub.add_parser("inbox", help="add a to-do (same list as todo; kept for old habits)")
    s.add_argument("text", nargs="?")
    s.add_argument("--list", action="store_true", help="only read the inbox")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_inbox)

    s = sub.add_parser("todo", help="the to-do catalog: list / add / set / pick / unpick / done")
    s.add_argument("action", choices=("list", "add", "set", "pick", "unpick", "done"))
    s.add_argument("args", nargs="*", help="add: text [key=value…]; set: to-do key=value…; pick: to-do rank why; done: to-do")
    s.add_argument("--today", action="store_true", help="list: only today's picks, in order, with reasons")
    s.add_argument("--all", action="store_true", help="list: include done to-dos")
    s.add_argument("--day", help="the day for pick / list --today (default: today in BOARD_TZ, Asia/Shanghai)")
    s.add_argument("--undo", action="store_true", help="done: mark not done")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_todo)

    s = sub.add_parser("create", help="create a task card; the breakpoint and next action are not filled in")
    s.add_argument("title")
    s.add_argument("--priority", type=int, choices=(0, 1, 2), default=1)
    s.add_argument("--lane", choices=sorted(LANES), default="project")
    for field in ("deliverable", "due", "log", "blockingOthers"):
        s.add_argument("--" + field, default="")
    s.set_defaults(fn=cmd_create)

    s = sub.add_parser("set", help="change fields: k=v, one or more")
    s.add_argument("ref"); s.add_argument("kv", nargs="+")
    s.add_argument("--user-verbatim", action="store_true",
                   help="required for breakpoint / nextAction: marks the text as the user's own words")
    s.set_defaults(fn=cmd_set)

    s = sub.add_parser("step", help="retired (2026-10-29): card steps became to-dos; use todo add / todo done")
    s.add_argument("ref", nargs="?"); s.add_argument("rest", nargs="*")
    s.add_argument("--undone", action="store_true")
    s.set_defaults(fn=cmd_step)

    s = sub.add_parser("retire-steps", help="maintenance: turn the steps a card still carries into to-dos (server rules)")
    s.add_argument("ref", nargs="?")
    s.add_argument("--all", action="store_true", help="every card that still carries steps")
    s.add_argument("--dry-run", action="store_true", help="list what would change; write nothing")
    s.set_defaults(fn=cmd_retire_steps)

    s = sub.add_parser("q", help="change one q track of the plan")
    s.add_argument("ref"); s.add_argument("n", type=int)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--decide", metavar="ANSWER")
    g.add_argument("--open", dest="open_", action="store_true")
    s.add_argument("--user-verbatim", action="store_true")
    s.add_argument("--today", default=time.strftime("%Y-%m-%d"))
    s.set_defaults(fn=cmd_q)

    s = sub.add_parser("ops", help="recent operation log (newest first)")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_ops)

    s = sub.add_parser("notify", help="nudge the page to refresh")
    s.set_defaults(fn=cmd_notify)

    s = sub.add_parser("backup-status", help="status of the latest local backup and GitHub push")
    s.set_defaults(fn=cmd_backup_status)

    a = p.parse_args()
    if getattr(a, "open_", False):
        a.decide = None
    atexit.register(maybe_update)   # update after the command ends (including a die exit): never change the run in progress
    a.fn(a)


if __name__ == "__main__":
    main()
