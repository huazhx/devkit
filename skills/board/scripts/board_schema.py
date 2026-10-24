"""The board's data rules (v2, 2026-09-23): the one and only copy.

board_ops.py, board_store.py and boardctl.py all take them from here: migration, normalizing empty
fields, appending events and deriving values from them.
The page no longer has its own copy (since 2026-10-21): it only sends operations, and the server
puts the derived values into the card view's `derived`.
After changing this file, run `python3 board_schema.py --selftest`.

The v2 shape (fields are described in BOARD.md §1):
  The card holds only **current values**: state / breakpoint / nextAction / …
  Changes go into the append-only ledger events[]; each entry records the **new value**:
    {ts, kind: "note",  text}
    {ts, kind: "bp",    text}                 the breakpoint was set to text ("" = cleared)
    {ts, kind: "na",    text}                 the next action was set to text
    {ts, kind: "state", to, auto?, noticedAt?}  the state became to; auto = the server's 6-hour fallback auto-pause
  Two legacy entry kinds that only the migration produces and that are never written again:
    {ts, kind: "bp"|"na", text, replaced: true}  v1 history: the old value text was replaced at ts
    {ts, kind: "session", out, noticedAt?}        v1 sessions: the start and end of one RUNNING period
  Derived (no longer stored as fields): breakpointAt / nextActionAt = ts of the latest entry of that
  kind (replaced ones included); enteredAt / doneAt = the last state event for the current state;
  for the session list see sessions().

Why it is split this way: v1's four timestamps relied on every write site remembering to "stamp
only when the value really changed", and each missed site was a silent wrong number (ARCHITECTURE
invariant 3); state changes were not recorded at all, so how many times a card was reopened, or
how long it waited in BLOCKED, was lost every time it happened.
"""
import json
import sys

VERSION = 2
CAP_MS = 6 * 3600 * 1000   # cap on one RUNNING period (used by the server's fallback auto-pause)

# Fields whose empty values are not stored. Readers always treat "missing" as "empty".
OPTIONAL = ("due", "deliverable", "breakpoint", "nextAction", "waitingOn",
            "blockingOthers", "cadence", "log")
# Fields only v1 had; the migration deletes them
V1_ONLY = ("notes", "history", "sessions", "breakpointAt", "nextActionAt",
           "enteredAt", "doneAt", "archivedUpTo", "roadmap")
# Signature that decides "the same event" when merging
EVENT_SIG = ("ts", "kind", "text", "to", "out", "replaced")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def valid_start(v, now):
    """A trustworthy session start: a number, of millisecond magnitude, not in the future (same rule as isSessionStart in rank.js)."""
    return _num(v) and 1e12 <= v <= now + 60000


# ---------------------------------------------------------------- normalization / migration

def compact_card(c):
    """Empty fields are not stored. Modifies in place and returns the card."""
    for k in OPTIONAL:
        if k in c and c[k] in ("", None):
            del c[k]
    return c


def migrate_card(c):
    """v1 card → v2 card. Only reads c and returns a new object; a card already in v2 (has events, no v1 fields) is just normalized."""
    c = dict(c)
    ev = list(c.get("events") or [])
    for n in c.get("notes") or []:
        if isinstance(n, dict) and _num(n.get("ts")):
            ev.append({"ts": n["ts"], "kind": "note", "text": n.get("text", "")})
    hist = [h for h in (c.get("history") or []) if isinstance(h, dict) and _num(h.get("ts"))]
    for h in hist:
        ev.append({"ts": h["ts"], "kind": h.get("kind"), "text": h.get("text", ""), "replaced": True})
    # The entry for the current value: prefer the v1 stamp; without one, the time of the last
    # replacement is when it was written; failing that, fall back to what v1 readers used
    # (bp → touchedAt, na → createdAt) and mark it approx.
    for kind, field, stamp, fallback in (("bp", "breakpoint", "breakpointAt", "touchedAt"),
                                         ("na", "nextAction", "nextActionAt", "createdAt")):
        if "events" in c:
            continue
        prior = [h["ts"] for h in hist if h.get("kind") == kind]
        if _num(c.get(stamp)):
            # With a stamp, record it as stamped, even if the current value is empty ("" = cleared at this moment); otherwise "last progress" would be lost
            ev.append({"ts": c[stamp], "kind": kind, "text": c.get(field) or ""})
        elif not c.get(field):
            continue
        elif prior:
            ev.append({"ts": max(prior), "kind": kind, "text": c[field]})
        else:
            ts = c.get(fallback) if _num(c.get(fallback)) else c.get("createdAt", 0)
            ev.append({"ts": ts, "kind": kind, "text": c[field], "approx": True})
    for s in c.get("sessions") or []:
        if isinstance(s, dict) and _num(s.get("ts")) and _num(s.get("out")):
            e = {"ts": s["ts"], "kind": "session", "out": s["out"]}
            if _num(s.get("noticedAt")):
                e["noticedAt"] = s["noticedAt"]
            ev.append(e)
    if "events" not in c:
        if c.get("state") == "running" and _num(c.get("enteredAt")):
            ev.append({"ts": c["enteredAt"], "kind": "state", "to": "running"})
        if c.get("state") == "done" and _num(c.get("doneAt")):
            ev.append({"ts": c["doneAt"], "kind": "state", "to": "done"})
    for k in V1_ONLY:
        c.pop(k, None)
    ev.sort(key=lambda e: e["ts"])            # stable sort: entries at the same moment keep their write order
    c["events"] = ev
    return compact_card(c)


def migrate_board(b):
    """Migrate a whole board to v2 (idempotent). Returns a new object and leaves the argument unchanged."""
    b = dict(b)
    b["threads"] = [migrate_card(t) for t in b.get("threads") or [] if isinstance(t, dict)]
    b["version"] = VERSION
    return b


# ---------------------------------------------------------------- derivations

def events(c):
    ev = c.get("events")
    return ev if isinstance(ev, list) else []


def last_change(c, kind):
    """The latest change entry for bp / na. v1 replaced entries count too: their ts is when that change happened."""
    for e in reversed(events(c)):
        if e.get("kind") == kind:
            return e
    return None


def last_state(c, to):
    for e in reversed(events(c)):
        if e.get("kind") == "state":
            return e if e.get("to") == to else None
    return None


def breakpoint_at(c):
    e = last_change(c, "bp")
    return e["ts"] if e else None


def next_action_at(c):
    """The "last real progress": when nextAction last changed value (the cadence timer of standing cards uses it)."""
    e = last_change(c, "na")
    return e["ts"] if e else None


def entered_at(c):
    """Start of the current RUNNING period; None when not running, so a start left over from an old state is never used."""
    if c.get("state") != "running":
        return None
    e = last_state(c, "running")
    return e["ts"] if e else None


def done_at(c):
    if c.get("state") != "done":
        return None
    e = last_state(c, "done")
    return e["ts"] if e else None


def trashed_at(c):
    if c.get("state") != "trashed":
        return None
    e = last_state(c, "trashed")
    return e["ts"] if e else None


STATES = ("ready", "running", "blocked", "done", "trashed")


def restore_state(c):
    """Which state a card returns to when restored from the Trash: the last non-trashed state before it went in.

    A card that was running before going to the Trash is restored as ready (so there is never a second
    running card); a card with no recorded state event defaults to ready.
    The last state event is the one that moved it to the Trash, so the search starts from the
    second-to-last one backwards (same rule as restoreState in rank.js before the rework)."""
    ev = [e for e in events(c) if e.get("kind") == "state"]
    for e in reversed(ev[:-1]):
        to = e.get("to")
        if to == "trashed":
            continue
        if to == "running":
            return "ready"
        return to if to in STATES else "ready"
    return "ready"


def sessions(c):
    """All finished sessions [{ts, out, capped, noticedAt?}], by ts ascending.
    Legacy session entries are read as they are; new ones are paired from state events: entering running to the next exit."""
    out, start = [], None
    for e in events(c):
        k = e.get("kind")
        if k == "session":
            s = {"ts": e["ts"], "out": e["out"], "capped": e["out"] - e["ts"] == CAP_MS}
        elif k == "state":
            if e.get("to") == "running":
                start = e["ts"]
                continue
            if start is None:
                continue
            s = {"ts": start, "out": e["ts"], "capped": bool(e.get("auto"))}
            start = None
            if e.get("noticedAt"):
                s["noticedAt"] = e["noticedAt"]
        else:
            continue
        out.append(s)
    out.sort(key=lambda s: s["ts"])
    return out


def derived(c):
    """The derived values of the card view: all computed from events, never stored, never sent back by clients (docs/OPS.md §2).

    The page no longer carries its own derivation rules: a derivation changes only here, and the page and the CLI read the same number."""
    return {
        "breakpointAt": breakpoint_at(c),
        "nextActionAt": next_action_at(c),
        "enteredAt": entered_at(c),
        "doneAt": done_at(c),
        "trashedAt": trashed_at(c),
        "restoreTo": restore_state(c),
        "sessions": sessions(c),
    }


# ---------------------------------------------------------------- writes

def log_event(c, e):
    c.setdefault("events", []).append(e)
    return e


def set_text(c, field, value, ts, reaffirm=False):
    """Change breakpoint / nextAction: write only when the value really changed, and record a new-value event. Returns whether it changed.

    reaffirm: a non-empty breakpoint left unchanged during a switch is recorded too; going through the
    switch ritual re-confirms it, and the "stale breakpoint" timer restarts from this moment (same
    meaning as reaffirm in rank.js setText)."""
    kind = {"breakpoint": "bp", "nextAction": "na"}[field]
    value = value or ""
    if value == (c.get(field) or "") and not (reaffirm and value):
        return False
    c[field] = value
    log_event(c, {"ts": ts, "kind": kind, "text": value})
    return True


def set_state(c, to, ts, auto=False, noticed_at=None):
    """Change state and record an event; clearing waitingOn when entering a state other than done is left to the caller."""
    if c.get("state") == to:
        return False
    c["state"] = to
    e = {"ts": ts, "kind": "state", "to": to}
    if auto:
        e["auto"] = True
    if noticed_at:
        e["noticedAt"] = noticed_at
    log_event(c, e)
    return True


def add_note(c, text, ts):
    return log_event(c, {"ts": ts, "kind": "note", "text": text})


def notes(c):
    return [e for e in events(c) if e.get("kind") == "note"]


# ---------------------------------------------------------------- self-test

def _selftest():
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("  ok  " if cond else "  FAIL ") + name)
        ok = ok and cond

    v1 = {"id": "a", "title": "t", "state": "running", "breakpoint": "b2", "nextAction": "n2",
          "waitingOn": "", "due": "", "cadence": None, "archivedUpTo": 5, "roadmap": "x",
          "createdAt": 100, "touchedAt": 900, "breakpointAt": 800, "enteredAt": 1000,
          "notes": [{"ts": 300, "text": "hi"}],
          "history": [{"ts": 400, "kind": "bp", "text": "b1"}, {"ts": 500, "kind": "na", "text": "n1"}],
          "sessions": [{"ts": 600, "out": 600 + CAP_MS}]}
    c = migrate_card(v1)
    check("all v1 fields deleted", not any(k in c for k in V1_ONLY))
    check("empty fields not stored", not any(k in c for k in ("waitingOn", "due", "cadence")))
    check("entry count conserved", len(c["events"]) == 1 + 2 + 2 + 1 + 1)
    check("breakpointAt takes the v1 stamp", breakpoint_at(c) == 800)
    check("nextActionAt without a stamp takes the last replacement", next_action_at(c) == 500)
    check("enteredAt derived", entered_at(c) == 1000)
    check("legacy session read and capped one recognized", sessions(c) == [{"ts": 600, "out": 600 + CAP_MS, "capped": True}])
    check("idempotent", migrate_card(c) == c)
    check("argument unchanged", "notes" in v1)
    set_state(c, "ready", 2000, auto=True)
    check("new session paired from state events", sessions(c)[-1] == {"ts": 1000, "out": 2000, "capped": True})
    check("enteredAt empty after leaving running", entered_at(c) is None)
    check("unchanged value not recorded", not set_text(c, "breakpoint", "b2", 3000))
    set_text(c, "breakpoint", "b3", 3000)
    check("changing the breakpoint records the new value", breakpoint_at(c) == 3000 and c["breakpoint"] == "b3")
    d = migrate_card({"id": "d", "state": "done", "doneAt": 50, "createdAt": 10})
    check("doneAt derived", done_at(d) == 50)
    set_state(d, "ready", 60)
    check("doneAt empty after reopening", done_at(d) is None)
    check("reaffirm: an unchanged non-empty value is recorded too", set_text(c, "breakpoint", "b3", 4000, reaffirm=True)
          and breakpoint_at(c) == 4000)
    check("reaffirm records no event for an empty value", not set_text({"breakpoint": ""}, "breakpoint", "", 4000, reaffirm=True))
    t = {"id": "t", "state": "ready", "events": []}
    set_state(t, "blocked", 10)
    set_state(t, "trashed", 20)
    check("restored to the state before the Trash", restore_state(t) == "blocked" and trashed_at(t) == 20)
    r = {"id": "r", "state": "ready", "events": []}
    set_state(r, "running", 10)
    set_state(r, "trashed", 20)
    check("running before the Trash is restored as ready", restore_state(r) == "ready")
    check("no state record defaults to ready", restore_state({"state": "trashed", "events": []}) == "ready")
    dv = derived(c)
    check("derived has every field", set(dv) == {"breakpointAt", "nextActionAt", "enteredAt", "doneAt",
                                         "trashedAt", "restoreTo", "sessions"})
    print("self-test passed" if ok else "self-test FAILED")
    return ok


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    if "--migrate" in sys.argv:           # read v1 from stdin, write v2 to stdout (for migration checks)
        json.dump(migrate_board(json.load(sys.stdin)), sys.stdout, ensure_ascii=False, indent=2)
        sys.exit(0)
    print(__doc__)
