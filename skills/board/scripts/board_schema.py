"""看板的数据规则（v2，2026-09-23）—— 唯一的一份。

board_ops.py、board_store.py、boardctl.py 都从这里取：迁移、空字段规整、事件的追加与推导。
页面不再有自己的一份（2026-10-21 起）：它只发操作，推导值由服务端放进卡片视图的 `derived`。
改这里后跑 `python3 board_schema.py --selftest`。

v2 的形状（字段说明在 BOARD.md §1）：
  卡上只放**当前值**：state / breakpoint / nextAction / …
  变化记进只增账本 events[]，每条写的是**新值**：
    {ts, kind: "note",  text}
    {ts, kind: "bp",    text}                 断点被设成 text（"" = 清空）
    {ts, kind: "na",    text}                 下一步被设成 text
    {ts, kind: "state", to, auto?, noticedAt?}  状态变成 to；auto = 服务端 6 小时兜底切出
  只由迁移产生、以后不再写的两种旧式条目：
    {ts, kind: "bp"|"na", text, replaced: true}  v1 history：text 这个旧值在 ts 被换掉
    {ts, kind: "session", out, noticedAt?}        v1 sessions：一次 RUNNING 的起止
  推导（不再落字段）：breakpointAt / nextActionAt = 该类最近一条（含 replaced）的 ts；
  enteredAt / doneAt = 当前状态对应的最后一条 state 事件；会话列表见 sessions()。

为什么这样拆：v1 的四个时间戳要靠每个写入点自觉「值真的变了才打戳」，漏一处就是一个
静默的错数（ARCHITECTURE 不变量 3）；状态切换根本没记，撤回过几次、在 BLOCKED 等了多久，
发生一次丢一次。
"""
import json
import sys

VERSION = 2
CAP_MS = 6 * 3600 * 1000   # 一次 RUNNING 的上限（服务端兜底切出用）

# 空值不落盘的字段。读的一方一律按「缺 = 空」处理。
OPTIONAL = ("due", "deliverable", "breakpoint", "nextAction", "waitingOn",
            "blockingOthers", "cadence", "log")
# v1 独有、迁移后删掉的字段
V1_ONLY = ("notes", "history", "sessions", "breakpointAt", "nextActionAt",
           "enteredAt", "doneAt", "archivedUpTo", "roadmap")
# 合并时判断「同一条事件」的签名
EVENT_SIG = ("ts", "kind", "text", "to", "out", "replaced")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def valid_start(v, now):
    """一次可信的会话起点：数值、量级像毫秒、不在未来（与 rank.js 的 isSessionStart 同口径）。"""
    return _num(v) and 1e12 <= v <= now + 60000


# ---------------------------------------------------------------- 规整 / 迁移

def compact_card(c):
    """空值字段不落盘。原地修改并返回。"""
    for k in OPTIONAL:
        if k in c and c[k] in ("", None):
            del c[k]
    return c


def migrate_card(c):
    """v1 卡 → v2 卡。只读 c，返回新对象；已是 v2 的卡（有 events、没有 v1 字段）原样规整。"""
    c = dict(c)
    ev = list(c.get("events") or [])
    for n in c.get("notes") or []:
        if isinstance(n, dict) and _num(n.get("ts")):
            ev.append({"ts": n["ts"], "kind": "note", "text": n.get("text", "")})
    hist = [h for h in (c.get("history") or []) if isinstance(h, dict) and _num(h.get("ts"))]
    for h in hist:
        ev.append({"ts": h["ts"], "kind": h.get("kind"), "text": h.get("text", ""), "replaced": True})
    # 当前值那一条：优先用 v1 的戳；没有戳时，最后一次被替换的时刻就是它写入的时刻；
    # 再没有就退到 v1 读者的同一个兜底（bp → touchedAt，na → createdAt），并标 approx。
    for kind, field, stamp, fallback in (("bp", "breakpoint", "breakpointAt", "touchedAt"),
                                         ("na", "nextAction", "nextActionAt", "createdAt")):
        if "events" in c:
            continue
        prior = [h["ts"] for h in hist if h.get("kind") == kind]
        if _num(c.get(stamp)):
            # 有戳就照戳记，当前值为空也记（"" = 在这一刻被清空）——否则「上次推进」会丢
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
    ev.sort(key=lambda e: e["ts"])            # 稳定排序：同一时刻保持写入顺序
    c["events"] = ev
    return compact_card(c)


def migrate_board(b):
    """整份板子迁到 v2（幂等）。返回新对象，不改入参。"""
    b = dict(b)
    b["threads"] = [migrate_card(t) for t in b.get("threads") or [] if isinstance(t, dict)]
    b["version"] = VERSION
    return b


# ---------------------------------------------------------------- 推导

def events(c):
    ev = c.get("events")
    return ev if isinstance(ev, list) else []


def last_change(c, kind):
    """bp / na 最近一次改变的那条。v1 的 replaced 条目也算：它的 ts 就是那次改变发生的时刻。"""
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
    """「上次真正推进」：nextAction 最近一次换值的时刻（standing 卡的 cadence 计时用它）。"""
    e = last_change(c, "na")
    return e["ts"] if e else None


def entered_at(c):
    """这次 RUNNING 的起点；不在 running 就是 None，避免使用旧状态遗留的起点。"""
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
    """从垃圾桶恢复时回到哪个状态：进桶前最后一个非 trashed 的状态。

    进桶前是 running 的恢复成 ready（避免出现第二张 running）；从没记过状态事件的卡默认 ready。
    最后一条 state 事件就是进桶那条，所以从倒数第二条往前找（与改造前 rank.js restoreState 同口径）。"""
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
    """全部已结束的会话 [{ts, out, capped, noticedAt?}]，按 ts 升序。
    旧式 session 条目原样读；新的从 state 事件配对：进 running 到下一次离开。"""
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
    """卡片视图里的 derived：全部从 events 推出，不落库，客户端也不回传（docs/OPS.md §2）。

    页面不再自带一份推导规则——改推导只改这里，页面和 CLI 读到的是同一个数。"""
    return {
        "breakpointAt": breakpoint_at(c),
        "nextActionAt": next_action_at(c),
        "enteredAt": entered_at(c),
        "doneAt": done_at(c),
        "trashedAt": trashed_at(c),
        "restoreTo": restore_state(c),
        "sessions": sessions(c),
    }


# ---------------------------------------------------------------- 写

def log_event(c, e):
    c.setdefault("events", []).append(e)
    return e


def set_text(c, field, value, ts, reaffirm=False):
    """改 breakpoint / nextAction：值真的变了才写，并记一条新值事件。返回是否变了。

    reaffirm：切换时原样留下的非空断点也记一条——走过一遍切换仪式就是重新确认了它，
    「断点凉了」从这一刻重新计时（与 rank.js setText 的 reaffirm 同义）。"""
    kind = {"breakpoint": "bp", "nextAction": "na"}[field]
    value = value or ""
    if value == (c.get(field) or "") and not (reaffirm and value):
        return False
    c[field] = value
    log_event(c, {"ts": ts, "kind": kind, "text": value})
    return True


def set_state(c, to, ts, auto=False, noticed_at=None):
    """改 state 并记一条事件；进 done 以外的状态时顺手清 waitingOn 的责任留给调用方。"""
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


# ---------------------------------------------------------------- 自检

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
    check("v1 字段全删", not any(k in c for k in V1_ONLY))
    check("空字段不落盘", not any(k in c for k in ("waitingOn", "due", "cadence")))
    check("条目数守恒", len(c["events"]) == 1 + 2 + 2 + 1 + 1)
    check("breakpointAt 取 v1 的戳", breakpoint_at(c) == 800)
    check("nextActionAt 无戳时取最后一次替换", next_action_at(c) == 500)
    check("enteredAt 推导", entered_at(c) == 1000)
    check("旧 session 读出且识别兜底", sessions(c) == [{"ts": 600, "out": 600 + CAP_MS, "capped": True}])
    check("幂等", migrate_card(c) == c)
    check("不改入参", "notes" in v1)
    set_state(c, "ready", 2000, auto=True)
    check("新会话从 state 事件配对", sessions(c)[-1] == {"ts": 1000, "out": 2000, "capped": True})
    check("离开 running 后 enteredAt 为空", entered_at(c) is None)
    check("值没变不记", not set_text(c, "breakpoint", "b2", 3000))
    set_text(c, "breakpoint", "b3", 3000)
    check("改断点记新值", breakpoint_at(c) == 3000 and c["breakpoint"] == "b3")
    d = migrate_card({"id": "d", "state": "done", "doneAt": 50, "createdAt": 10})
    check("doneAt 推导", done_at(d) == 50)
    set_state(d, "ready", 60)
    check("撤回后 doneAt 为空", done_at(d) is None)
    check("reaffirm：原样留下的非空值也记一条", set_text(c, "breakpoint", "b3", 4000, reaffirm=True)
          and breakpoint_at(c) == 4000)
    check("reaffirm 不为空值记事件", not set_text({"breakpoint": ""}, "breakpoint", "", 4000, reaffirm=True))
    t = {"id": "t", "state": "ready", "events": []}
    set_state(t, "blocked", 10)
    set_state(t, "trashed", 20)
    check("恢复到进桶前的状态", restore_state(t) == "blocked" and trashed_at(t) == 20)
    r = {"id": "r", "state": "ready", "events": []}
    set_state(r, "running", 10)
    set_state(r, "trashed", 20)
    check("进桶前是 running 的恢复成 ready", restore_state(r) == "ready")
    check("没有状态记录默认 ready", restore_state({"state": "trashed", "events": []}) == "ready")
    dv = derived(c)
    check("derived 字段齐全", set(dv) == {"breakpointAt", "nextActionAt", "enteredAt", "doneAt",
                                         "trashedAt", "restoreTo", "sessions"})
    print("自检通过" if ok else "自检失败")
    return ok


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    if "--migrate" in sys.argv:           # 读 stdin 的 v1，写 stdout 的 v2（给迁移校验用）
        json.dump(migrate_board(json.load(sys.stdin)), sys.stdout, ensure_ascii=False, indent=2)
        sys.exit(0)
    print(__doc__)
