#!/usr/bin/env python3
"""boardctl — 调度台的操作工具（投影读 + 定向写回）。

客户端通过 API 读取任务，只输出所需字段；写入带 CAS 令牌。
完整 JSON 由客户端处理，不需要装入模型上下文。

读写统一访问已认证的服务；写入使用 `X-Base-Saved-At`（CAS）。
服务不可达时退出，不写本地文件。连接配置见 README.md。

用法
  boardctl.py list                      # 全部卡的骨架
  boardctl.py list --due                # 按截止日排序
  boardctl.py show running              # 当前 running 那张
  boardctl.py show example-card        # id 前缀 / 标题子串都行（有歧义会报错）
  boardctl.py show 示例项目 --no-log     # 不读取 events
  boardctl.py note 示例项目 "完成合成数据验证"
  boardctl.py set 示例项目 due=2027-01-15 priority=0
  boardctl.py step 示例项目 验证 2       # 勾选第 2 格（--undone 取消）
  boardctl.py q 示例项目 1 --decide "采用方案 A" --user-verbatim
  boardctl.py time --days 7             # 时间去哪了（与页面「⏱ 时间」视图同口径）
  boardctl.py inbox "一条还没归卡的想法"   # 记到收件箱（页面上可归入线程 / 升成卡 / 删除）
  boardctl.py notify                    # 只敲页面刷新

字段红线（`BOARD.md` §5）没有变：**断点 / 下一步不许代写**。这两个字段要用
`--user-verbatim` 才写得进去 —— 它是提醒你「这段字得是用户说的」，不是权限位。
"""

import argparse
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

import board_schema as S   # 迁移 / 事件账本 / 推导：规则只有那一份

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
        raise ValueError("BOARD_URL 只能包含协议、主机和端口")
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
    raise ValueError("远程看板必须使用 HTTPS；HTTP 仅用于本机隔离实例")


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
        die("没有 API 凭据；请配置 BOARD_CLIENT_CONFIG 或 BOARD_CREDENTIALS_FILE。")
    return token


def request(path, data=None, method="GET", headers=None):
    context = transport()
    req = urllib.request.Request(SERVER + path, data=data, method=method,
        headers={"Authorization": "Bearer " + api_token(), **(headers or {})})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), NoRedirect())
    with opener.open(req, timeout=10) as response:
        return json.load(response)

# `set` 允许写的字段。id / events / plan / createdAt 等走各自的命令或不允许改。
SETTABLE = {
    "title", "state", "priority", "due", "deliverable", "breakpoint", "nextAction",
    "waitingOn", "blockingOthers", "lane", "cadence", "log",
}
INT_FIELDS = {"priority", "cadence"}
USER_ONLY = {"breakpoint", "nextAction"}
STATES = {"ready", "running", "blocked", "done", "trashed"}
LANES = {"project", "standing", "errand"}



def fmt_dur(ms):
    """与 rank.js 的 fmtDur 同格式：一律 NhMMm，好竖着扫。"""
    m = max(0, int(round(ms / 60000)))
    return f"{m // 60}h{m % 60:02d}m"


def die(msg, code=1):
    print(f"boardctl: {msg}", file=sys.stderr)
    sys.exit(code)


def now_ms():
    return int(time.time() * 1000)


def load():
    """服务是唯一读源；网络失败时不回落到旧快照。"""
    try:
        return S.migrate_board(request("/board.json"))
    except Exception as e:
        die(f"无法读取服务 {SERVER}（{e}）；没有读取本地快照。")


def resolve(board, ref):
    """id → id 前缀 → 标题全等 → 标题子串；'running' 当状态关键字。"""
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
            die("匹配到多张卡，请给更长的 id 前缀：\n"
                + "\n".join(f"  {c['id'][:8]}  {c['title']}" for c in hits))
    die(f"找不到匹配「{ref}」的卡")


def save(board, base_saved_at, quiet=False):
    """PUT 带 CAS；失败时不写本地文件。"""
    board["savedAt"] = now_ms()
    board["version"] = S.VERSION
    for c in board["threads"]:
        S.compact_card(c)
    body = (json.dumps(board, ensure_ascii=False, indent=2) + "\n").encode()
    try:
        result = request("/board.json", data=body, method="PUT", headers={
            "Content-Type": "application/json", "X-Base-Saved-At": str(base_saved_at)})
        board["savedAt"] = result["savedAt"]
        if not quiet:
            print(f"已写回（服务端 CAS，savedAt={board['savedAt']}）；页面已自动刷新")
        return "server"
    except urllib.error.HTTPError as e:
        if e.code == 409:
            die("CAS 冲突 409：board.json 被别处改过。**不要覆盖** —— 重新读一次再来。")
        die(f"服务端返回 HTTP {e.code}")
    except Exception as e:
        die(f"写入服务失败（{e}）；没有改写任何本地文件。")


# ---------------------------------------------------------------- 读

def cmd_list(a):
    board = load()
    cards = board["threads"]
    if a.state:
        cards = [c for c in cards if c.get("state") == a.state]
    else:   # 垃圾桶里的卡默认不列；要看就 --state trashed
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
    print(f"\n{len(cards)} / {len(board['threads'])} 张  ·  savedAt={board.get('savedAt')}")


def cmd_show(a):
    card = resolve(load(), a.ref)
    # 推导出的时间戳一并给出：v2 不再把它们落成字段，但读卡的人（和 AI）要用
    derived = {k: v for k, v in (("breakpointAt", S.breakpoint_at(card)),
                                 ("nextActionAt", S.next_action_at(card)),
                                 ("enteredAt", S.entered_at(card)),
                                 ("doneAt", S.done_at(card))) if v}
    if a.no_log:
        # events 是不截断的账本，不该整条倒进上下文——要看时长用 `time`
        card = {k: v for k, v in card.items() if k != "events"}
    elif a.events is not None:
        if a.events < 0:
            die("--events 必须是不小于 0 的整数")
        events = card.get("events", [])
        card = dict(card, events=events[-a.events:] if a.events else [],
                    eventsOmitted=max(0, len(events) - a.events))
    print(json.dumps(dict(card, derived=derived), ensure_ascii=False, indent=2))


def cmd_plan(a):
    card = resolve(load(), a.ref)
    plan = card.get("plan")
    if not plan:
        print(f"「{card['title']}」没有 plan 字段")
        return
    for tr in plan.get("tracks", []):
        if tr.get("kind") == "flow":
            done = sum(1 for s in tr["steps"] if s.get("done"))
            print(f"flow  {tr['name']}   {done}/{len(tr['steps'])}")
            for i, s in enumerate(tr["steps"], 1):
                print(f"        {i}. [{'x' if s.get('done') else ' '}] {s['t']}")
        else:
            print(f"q     {tr['name']}   [{tr.get('status')}]"
                  + (f"\n        → {tr['answer']}" if tr.get("answer") else ""))
    print(f"\nsummaryAt={plan.get('summaryAt')}")


def cmd_time(a):
    """时间去哪了 —— 与页面「⏱ 时间」视图同口径（js/rank.js 的时间聚合）：
    ① 跨午夜的会话整条算在开始那天，不切分；② 长度恰好 = 上限的那段是兜底估算，单独标出。"""
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
        entries = [(s["ts"], s["out"], s["capped"]) for s in S.sessions(c)]
        st = S.entered_at(c)
        if S.valid_start(st, now):
            entries.append((st, now, False))       # 正在跑的那次也算进去
        for ts, out, capped in entries:
            row = by_start.get(day_key(ts))
            if row is None:
                continue                            # 窗口之外
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
    print(f"今天 {t['day'].strftime('%-m/%-d')}   合计 {fmt_dur(t['total'])} · {t['count']} 次切换"
          + ("   （含兜底估算）" if t["capped"] else ""))
    if not t["cards"]:
        print("  （还没有记录 —— 账本从第一次切卡开始，过去不回填）")
    for c in sorted(t["cards"], key=lambda x: -x["ms"]):
        print(f"  {fmt_dur(c['ms']):>8}  {c['title']}"
              + ("   ← 含兜底估算（≥上限，非实测）" if c["capped"] else ""))
    print(f"\n近 {a.days} 天")
    for r in rows:
        top = " · ".join(x["title"] for x in sorted(r["cards"], key=lambda x: -x["ms"])[:3])
        print(f"  {r['day'].strftime('%-m/%-d'):<5} {fmt_dur(r['total']):>8}  {r['count']:>2} 次  {top}"
              + ("   （含兜底）" if r["capped"] else ""))


# ---------------------------------------------------------------- 写

def cmd_note(a):
    board = load()
    card = resolve(board, a.ref)
    S.add_note(card, a.text, now_ms())
    card["touchedAt"] = now_ms()
    save(board, board["savedAt"])
    print(f"已记到「{card['title']}」（现 {len(S.notes(card))} 条记录）")


def cmd_inbox(a):
    board = load()
    if a.list:
        if a.text:
            die("查看收件箱时不要同时提供要写入的文字")
        if a.limit < 1:
            die("--limit 必须大于 0")
        items = board.get("inbox", [])
        selected = items[:a.limit]
        if a.json:
            print(json.dumps(selected, ensure_ascii=False, indent=2))
        else:
            for item in selected:
                text = str(item.get("text", "")).replace("\n", " ")
                print(f"{str(item.get('id', ''))[:8]}  {text[:160]}{'…' if len(text) > 160 else ''}")
            print(f"\n显示 {len(selected)} / {len(items)} 条；--limit 可调整数量")
        return
    if not a.text or not a.text.strip():
        die("提供要记入的文字，或用 inbox --list 查看")
    board.setdefault("inbox", []).insert(
        0, {"id": str(uuid.uuid4()), "ts": now_ms(), "text": a.text.strip()})
    save(board, board["savedAt"])
    print(f"已记到收件箱（现 {len(board['inbox'])} 条）")


def cmd_create(a):
    title = a.title.strip()
    if not title:
        die("标题不能为空")
    board = load()
    if any(c.get("title") == title for c in board["threads"]):
        die("已有同名卡；先查看现有卡片，避免重复创建")
    ts = now_ms()
    card = {"id": str(uuid.uuid4()), "title": title, "state": "ready",
            "priority": a.priority, "lane": a.lane, "createdAt": ts,
            "touchedAt": ts, "events": []}
    for field in ("deliverable", "due", "log", "blockingOthers"):
        value = getattr(a, field)
        if value:
            card[field] = value
    board["threads"].append(card)
    save(board, board["savedAt"])
    print(f"已创建「{title}」（{card['id']}）")


def cmd_set(a):
    board = load()
    card = resolve(board, a.ref)
    ts = now_ms()
    old_state = card.get("state")
    changed = []
    for pair in a.kv:
        if "=" not in pair:
            die(f"要 k=v 形式，收到「{pair}」")
        k, v = pair.split("=", 1)
        if k not in SETTABLE:
            die(f"「{k}」不在可写字段里。可写：{', '.join(sorted(SETTABLE))}")
        if k in USER_ONLY and not a.user_verbatim:
            die(f"「{k}」是红线字段（BOARD.md §5）——只许存用户说过的话。"
                f"确认这段字来自用户本人，再加 --user-verbatim。")
        if k in INT_FIELDS:
            try:
                v = None if v in ("", "null") else int(v)
            except ValueError:
                die(f"{k} 要整数，收到「{v}」")
        if k == "state" and v not in STATES:
            die(f"state 只能是 {'/'.join(sorted(STATES))}")
        if k == "lane" and v not in LANES:
            die(f"lane 只能是 {'/'.join(sorted(LANES))}")

        old = card.get(k)
        if old == v:
            continue
        if k in ("breakpoint", "nextAction"):
            S.set_text(card, k, v, ts)      # 记一条新值事件（§7 决策轨迹）
        elif k == "state":
            S.set_state(card, v, ts)        # 进出 running 的事件就是会话的起止
        else:
            card[k] = v
        changed.append(f"{k}: {old!r} → {v!r}")

    if not changed:
        print("值没变，没写。")
        return
    new_state = card.get("state")
    if old_state != "running" and new_state == "running":
        others = [c["title"] for c in board["threads"]
                  if c.get("id") != card.get("id") and c.get("state") == "running"]
        if others:
            die(f"已有 running 卡：「{'、'.join(others)}」。先切出原卡，再开始这张；本次未保存。")
    card["touchedAt"] = ts
    save(board, board["savedAt"])
    print(f"「{card['title']}」已改：")
    for line in changed:
        print(f"  {line}")


def _plan_edit(board, ref, fn):
    card = resolve(board, ref)
    plan = card.get("plan")
    if not plan:
        die(f"「{card['title']}」没有 plan 字段")
    fn(plan)
    plan["summaryAt"] = now_ms()          # §3.6：确实改了 tracks 才打戳
    card["touchedAt"] = now_ms()
    save(board, board["savedAt"])
    return card


def cmd_step(a):
    def fn(plan):
        flows = [t for t in plan["tracks"] if t.get("kind") == "flow"]
        hits = [t for t in flows if t["name"] == a.track] or \
               [t for t in flows if a.track.lower() in t["name"].lower()]
        if len(hits) != 1:
            die("轨道要唯一：\n" + "\n".join(f"  {t['name']}" for t in flows))
        steps = hits[0]["steps"]
        if not 1 <= a.n <= len(steps):
            die(f"「{hits[0]['name']}」只有 {len(steps)} 格")
        steps[a.n - 1]["done"] = not a.undone
    card = _plan_edit(load(), a.ref, fn)
    print(f"「{card['title']}」已更新：{a.track} 第 {a.n} 格 → "
          f"{'未完成' if a.undone else '完成'}")


def cmd_q(a):
    def fn(plan):
        qs = [t for t in plan["tracks"] if t.get("kind") == "q"]
        if not 1 <= a.n <= len(qs):
            die(f"只有 {len(qs)} 条 q：\n" + "\n".join(f"  {i}. {t['name']}"
                                                       for i, t in enumerate(qs, 1)))
        q = qs[a.n - 1]
        if a.decide:
            if not a.user_verbatim:
                die("q 的 answer 只许是用户说过的话（§3.6）——确认后加 --user-verbatim。")
            q.update({"status": "decided", "answer": a.decide, "at": a.today})
        else:
            q.update({"status": "open"})
            q.pop("answer", None)
            q.pop("at", None)
    card = _plan_edit(load(), a.ref, fn)
    print(f"「{card['title']}」的第 {a.n} 条 q → "
          f"{'decided' if a.decide else 'open'}")


def cmd_notify(_a):
    try:
        request("/notify", data=b"", method="POST")
        print("页面已刷新")
    except Exception as e:
        die(f"服务不可达（{e}）")


def cmd_backup_status(_a):
    try:
        print(json.dumps(request("/backup-status"), ensure_ascii=False, indent=2))
    except Exception as e:
        die(f"无法读取备份状态（{e}）")


# ---------------------------------------------------------------- CLI

def main():
    p = argparse.ArgumentParser(prog="boardctl", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("list", help="全部卡的骨架")
    s.add_argument("--state"); s.add_argument("--lane")
    s.add_argument("--due", action="store_true", help="按截止日排序")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("show", help="单张卡（id 前缀 / 标题子串 / running）")
    s.add_argument("ref")
    group = s.add_mutually_exclusive_group()
    group.add_argument("--no-log", action="store_true")
    group.add_argument("--events", type=int, help="只显示最近 N 条事件")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("plan", help="只看 plan 的轨道与格子")
    s.add_argument("ref")
    s.set_defaults(fn=cmd_plan)

    s = sub.add_parser("time", help="时间去哪了（与页面「⏱ 时间」同口径）")
    s.add_argument("--days", type=int, default=7)
    s.set_defaults(fn=cmd_time)

    s = sub.add_parser("note", help="追加一条 notes")
    s.add_argument("ref"); s.add_argument("text")
    s.set_defaults(fn=cmd_note)

    s = sub.add_parser("inbox", help="记一条还没归卡的想法到收件箱")
    s.add_argument("text", nargs="?")
    s.add_argument("--list", action="store_true", help="只读收件箱")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_inbox)

    s = sub.add_parser("create", help="创建任务卡，不自动填写断点和下一步")
    s.add_argument("title")
    s.add_argument("--priority", type=int, choices=(0, 1, 2), default=1)
    s.add_argument("--lane", choices=sorted(LANES), default="project")
    for field in ("deliverable", "due", "log", "blockingOthers"):
        s.add_argument("--" + field, default="")
    s.set_defaults(fn=cmd_create)

    s = sub.add_parser("set", help="改字段：k=v，可给多个")
    s.add_argument("ref"); s.add_argument("kv", nargs="+")
    s.add_argument("--user-verbatim", action="store_true",
                   help="断点/下一步 必须带这个，表示这段字是用户说的")
    s.set_defaults(fn=cmd_set)

    s = sub.add_parser("step", help="勾/取消 plan 里的一格")
    s.add_argument("ref"); s.add_argument("track"); s.add_argument("n", type=int)
    s.add_argument("--undone", action="store_true")
    s.set_defaults(fn=cmd_step)

    s = sub.add_parser("q", help="改 plan 里的一条 q")
    s.add_argument("ref"); s.add_argument("n", type=int)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--decide", metavar="ANSWER")
    g.add_argument("--open", dest="open_", action="store_true")
    s.add_argument("--user-verbatim", action="store_true")
    s.add_argument("--today", default=time.strftime("%Y-%m-%d"))
    s.set_defaults(fn=cmd_q)

    s = sub.add_parser("notify", help="敲一下页面刷新")
    s.set_defaults(fn=cmd_notify)

    s = sub.add_parser("backup-status", help="最近一次本地备份和 GitHub 推送状态")
    s.set_defaults(fn=cmd_backup_status)

    a = p.parse_args()
    if getattr(a, "open_", False):
        a.decide = None
    a.fn(a)


if __name__ == "__main__":
    main()
