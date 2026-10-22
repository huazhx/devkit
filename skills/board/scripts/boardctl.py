#!/usr/bin/env python3
"""boardctl — 调度台的操作工具（投影读 + 定向写回）。

客户端通过 API 读取任务，只输出所需字段；完整 JSON 不需要装入模型上下文。

写入一律是 `POST /api/ops` 的标准操作（docs/OPS.md）：每次只点名一张卡、一个字段或
一条收件箱条目，不上传整份看板。状态切换、事件账本、计划戳等数据规则只在服务端执行。
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
  boardctl.py plan 示例项目 --set plan.json  # 整份替换 plan（格子名 ≤ 30 字，说明放 detail）
  boardctl.py q 示例项目 1 --decide "采用方案 A" --user-verbatim
  boardctl.py time --days 7             # 时间去哪了（与页面「⏱ 时间」视图同口径）
  boardctl.py inbox "一条还没归卡的想法"   # 记到收件箱（页面上可归入线程 / 升成卡 / 删除）
  boardctl.py ops --limit 20            # 最近的操作日志（谁在什么时候改了什么）
  boardctl.py notify                    # 只敲页面刷新

字段红线（`BOARD.md` §5）没有变：**断点 / 下一步不许代写**。这两个字段要用
`--user-verbatim` 才写得进去 —— 它是提醒你「这段字得是用户说的」，不是权限位。
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

import board_schema as S   # 迁移 / 事件账本 / 推导：规则只有那一份
import board_package as P  # 安装包版本与自动更新（ARCHITECTURE §6）

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
    try:
        with opener.open(req, timeout=10) as response:
            _note_server_client(response.headers.get("X-Board-Client"))
            return json.load(response)
    except urllib.error.HTTPError as e:
        _note_server_client(e.headers.get("X-Board-Client"))
        raise


# ---- 自动更新（ARCHITECTURE §6）----
# 服务端在每个 API 响应头里带上它打包的客户端版本。和本机受管安装不一致时，命令结束后从同一个服务
# 取 GET /api/client，校验后原子替换本机所有受管安装。连接配置在安装目录外，不受影响。
SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
_server_client = None
_update_tried = False


def _note_server_client(version):
    global _server_client
    if version and not _server_client:
        _server_client = version


def auto_update_enabled():
    """默认只对真实配置的服务开启：测试和临时实例都会用环境变量改地址或凭据，不能拿它们的包覆盖本机安装。"""
    flag = os.environ.get("BOARD_AUTO_UPDATE")
    if flag is not None:
        return flag == "1"
    if _client_config.get("auto_update") is False:
        return False
    return not any(os.environ.get(k) for k in (
        "BOARD_URL", "BOARD_CLIENT_CONFIG", "BOARD_CA_FILE", "BOARD_API_TOKEN", "BOARD_CREDENTIALS_FILE"))


def maybe_update():
    """有需要就更新，返回是否换过。失败只提示一行，不影响这次命令的结果。"""
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
        print(f"boardctl: 客户端自动更新没有完成（{e}）；这次命令不受影响。"
              "手动更新见 references/SETUP.md。", file=sys.stderr)
        return False
    print(f"boardctl: 已按服务端更新本机看板客户端（{len(stale)} 处，版本 {_server_client}）。", file=sys.stderr)
    return True


def _version_mismatch(e):
    """这次被拒是因为客户端和服务端对不上接口（而不是数据或权限问题）。"""
    err = _error_body(e).get("error")
    return (e.code in (405, 410) or (e.code == 404 and err != "not_found")
            or (e.code == 400 and err == "api_version"))

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


API_VERSION = 1   # docs/OPS.md 的接口版本；服务端不认识时回 400


def _error_body(e):
    if not hasattr(e, "board_body"):    # 响应体只能读一次；判断和报错都要用
        try:
            e.board_body = json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            e.board_body = {}
    return e.board_body


def _op_failure(e):
    """把服务端的拒绝翻译成「发生了什么、下一步该怎么做」。所有分支都意味着这次什么都没写。"""
    body = _error_body(e)
    err, detail = body.get("error"), body.get("detail") or ""
    if e.code == 400 and err == "api_version":
        return f"服务端与客户端版本不匹配（{detail}）；未写入。更新 CLI 后再试。"
    if e.code == 400:
        return f"服务端拒绝了这个操作（参数不合法，未写入）：{detail or err}"
    if e.code == 401:
        return "认证失败（HTTP 401）：检查 API 令牌配置；未写入。"
    if e.code == 404 and err == "not_found":
        return f"找不到要改的对象（未写入）：{detail}。先 list / show 重新确认。"
    if e.code in (404, 405, 410):
        # 旧服务没有 /api/ops（回 404），或新服务拒绝旧式整份写入（回 410）：两边版本对不上
        return (f"服务端与客户端版本不匹配（HTTP {e.code}{'：' + detail if detail else ''}）；"
                "未写入。更新 CLI 或服务后再试。")
    if e.code == 409 and err == "op_id_reused":
        return f"同一个 opId 配了不同的操作（客户端问题，未写入）：{detail}"
    if e.code == 409:
        # 不自动重试：别处刚改过的值要由人看过再决定（OPS.md §5）
        return (f"目标刚被别处改过（未写入）：{detail}。先 show 重新读一次，"
                "确认要改的值仍然成立，再重新执行；不要自动重试。")
    if e.code == 422:
        return f"违反看板规则（未写入）：{detail}"
    return f"服务端返回 HTTP {e.code}（未写入）：{detail or err or e.reason}"


def op(name, args):
    """发一个标准操作（POST /api/ops），返回服务端的 200 响应体。

    opId 每个操作新生成一次。网络错误时用**同一个** opId 重试一次：服务端按 opId 去重，
    所以「第一次其实已经写进去、只是响应丢了」也不会让一条备注记两次。
    服务端明确拒绝（4xx）时不重试，直接报错退出。"""
    body = json.dumps({"api": API_VERSION, "opId": str(uuid.uuid4()), "op": name, "args": args},
                      ensure_ascii=False).encode()
    headers = {"Content-Type": "application/json", "X-Client-Id": "boardctl"}
    for attempt in (1, 2):
        try:
            return request("/api/ops", data=body, method="POST", headers=headers)
        except urllib.error.HTTPError as e:
            # 接口对不上：客户端按服务端更新后，用新版把这条命令重新执行一次（被拒的这次什么都没写）
            if _version_mismatch(e) and not os.environ.get("BOARD_CLIENT_REEXEC") and maybe_update():
                print("boardctl: 用新版客户端重新执行这条命令。", file=sys.stderr, flush=True)
                sys.stdout.flush()
                os.environ["BOARD_CLIENT_REEXEC"] = "1"
                os.execv(sys.executable, [sys.executable, *sys.argv])
            die(_op_failure(e))
        except ValueError as e:      # 配置错误（如公网 HTTP）：重试没有意义
            die(f"写入服务失败（{e}）；没有改写任何本地文件。")
        except Exception as e:
            if attempt == 2:
                die(f"写入服务失败（{e}）；已用同一 opId 重试一次。没有改写任何本地文件；"
                    "先 show 核实这次操作是否已经生效。")
            time.sleep(1)


def changed_card(result, card_id):
    """操作响应里这张卡的新视图（没变化时返回 None）。"""
    for c in (result.get("changed") or {}).get("cards") or []:
        if c.get("id") == card_id:
            return c
    return None


def written(result):
    """一行写入回执：服务端修订号；重复提交时说明没有再执行一次。"""
    if result.get("duplicate"):
        return f"服务端已执行过这次操作，没有重复执行（rev={result.get('rev')}）"
    return f"已写入服务（rev={result.get('rev')}）；页面已自动刷新"


def derived_of(card):
    """推导时间戳：优先用服务端给的 derived（规则只在服务端一份），旧服务没给时本地推。"""
    d = card.get("derived")
    if isinstance(d, dict):
        return d
    return {"breakpointAt": S.breakpoint_at(card), "nextActionAt": S.next_action_at(card),
            "enteredAt": S.entered_at(card), "doneAt": S.done_at(card),
            "sessions": S.sessions(card)}


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
    print(f"\n{len(cards)} / {len(board['threads'])} 张  ·  rev={board.get('rev')}  ·  savedAt={board.get('savedAt')}")


def cmd_show(a):
    card = resolve(load(), a.ref)
    # 推导出的时间戳一并给出：v2 不再把它们落成字段，但读卡的人（和 AI）要用。
    # 会话列表不放进来——它和 events 一样会无限增长；要看时长用 `time`。
    full = derived_of(card)
    derived = {k: full.get(k) for k in ("breakpointAt", "nextActionAt", "enteredAt", "doneAt",
                                        "trashedAt") if full.get(k)}
    if card.get("state") == "trashed" and full.get("restoreTo"):
        derived["restoreTo"] = full["restoreTo"]
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
    if a.set:
        # 整份换掉这张卡的 plan（一个 card.update，带 if：读完之后别处改过 plan 就拒绝，不覆盖）
        try:
            src = sys.stdin.read() if a.set == "-" else open(a.set, encoding="utf-8").read()
            new = json.loads(src)
        except (OSError, ValueError) as e:
            die(f"读不了 plan 文件（{e}）")
        new = {"tracks": new.get("tracks")} if isinstance(new, dict) else new
        result = op("card.update", {"id": card["id"], "set": {"plan": new}, "if": {"plan": plan}})
        print(f"已更新「{card['title']}」的 plan。{written(result)}")
        return
    if not plan:
        print(f"「{card['title']}」没有 plan 字段")
        return
    for tr in plan.get("tracks", []):
        if tr.get("kind") == "flow":
            done = sum(1 for s in tr["steps"] if s.get("done"))
            print(f"flow  {tr['name']}   {done}/{len(tr['steps'])}")
            for i, s in enumerate(tr["steps"], 1):
                print(f"        {i}. [{'x' if s.get('done') else ' '}] {s['t']}")
                if s.get("detail"):
                    print(f"             {s['detail']}")
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
        d = derived_of(c)
        entries = [(s["ts"], s["out"], s["capped"]) for s in d.get("sessions") or []]
        st = d.get("enteredAt")
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
    card = resolve(load(), a.ref)
    result = op("note.add", {"id": card["id"], "text": a.text})
    fresh = changed_card(result, card["id"]) or card
    print(f"已记到「{card['title']}」（现 {len(S.notes(fresh))} 条记录）；{written(result)}")


def cmd_inbox(a):
    if a.list:
        if a.text:
            die("查看收件箱时不要同时提供要写入的文字")
        if a.limit < 1:
            die("--limit 必须大于 0")
        items = load().get("inbox", [])
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
    result = op("inbox.add", {"text": a.text.strip()})
    inbox = (result.get("changed") or {}).get("inbox")
    count = f"（现 {len(inbox)} 条）" if isinstance(inbox, list) else ""
    print(f"已记到收件箱{count}；{written(result)}")


def cmd_create(a):
    title = a.title.strip()
    if not title:
        die("标题不能为空")
    # 「不重复建同名卡」是给 AI 的使用规则（OPS.md §5），不是数据规则，所以留在 CLI
    if any(c.get("title") == title for c in load()["threads"]):
        die("已有同名卡；先查看现有卡片，避免重复创建")
    args = {"title": title, "priority": a.priority, "lane": a.lane}
    for field in ("deliverable", "due", "log", "blockingOthers"):
        value = getattr(a, field)
        if value:
            args[field] = value
    result = op("card.create", args)
    card_id = (result.get("result") or {}).get("id")
    print(f"已创建「{title}」（{card_id}）；{written(result)}")


def cmd_set(a):
    board = load()
    card = resolve(board, a.ref)
    fields, new_state, changed = {}, None, []
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
        if old == v or (old in (None, "") and v in (None, "")):
            continue
        if k == "state":
            new_state = v
        else:
            fields[k] = v
        changed.append(f"{k}: {old!r} → {v!r}")

    if not changed:
        print("值没变，没写。")
        return
    if new_state == "running":
        # 只是提前报错、免得字段已改而状态被拒：真正的「同时只能一张 running」由服务端裁决
        others = [c["title"] for c in board["threads"]
                  if c.get("id") != card.get("id") and c.get("state") == "running"]
        if others:
            die(f"已有 running 卡：「{'、'.join(others)}」。先切出原卡，再开始这张；本次未保存。")
    if fields:
        # if 带上刚读到的旧值：这几个字段在读和写之间被别处改过，服务端就整条拒绝（409）
        result = op("card.update", {"id": card["id"], "set": fields,
                                    "if": {k: card.get(k) for k in fields}})
    if new_state:
        try:
            result = op("card.state", {"id": card["id"], "to": new_state})
        except SystemExit:
            if fields:
                print(f"注意：{', '.join(fields)} 已写入；只有 state 没改成 {new_state}。", file=sys.stderr)
            raise
    print(f"「{card['title']}」已改：")
    for line in changed:
        print(f"  {line}")
    print(written(result))


def cmd_step(a):
    card = resolve(load(), a.ref)
    plan = card.get("plan")
    if not plan:
        die(f"「{card['title']}」没有 plan 字段")
    flows = [t for t in plan.get("tracks", []) if t.get("kind") == "flow"]
    hits = [t for t in flows if t["name"] == a.track] or \
           [t for t in flows if a.track.lower() in t["name"].lower()]
    if len(hits) != 1:
        die("轨道要唯一：\n" + "\n".join(f"  {t['name']}" for t in flows))
    steps = hits[0].get("steps") or []
    if not 1 <= a.n <= len(steps):
        die(f"「{hits[0]['name']}」只有 {len(steps)} 格")
    # 带上这一格的文字：读和写之间轨道被重排过，服务端会按文字对不上拒绝，而不是勾错格
    result = op("plan.step", {"id": card["id"], "track": hits[0]["name"], "step": a.n - 1,
                              "done": not a.undone, "if": {"text": steps[a.n - 1].get("t")}})
    print(f"「{card['title']}」已更新：{hits[0]['name']} 第 {a.n} 格 → "
          f"{'未完成' if a.undone else '完成'}；{written(result)}")


def cmd_q(a):
    card = resolve(load(), a.ref)
    plan = card.get("plan")
    if not plan:
        die(f"「{card['title']}」没有 plan 字段")
    qs = [t for t in plan.get("tracks", []) if t.get("kind") == "q"]
    if not 1 <= a.n <= len(qs):
        die(f"只有 {len(qs)} 条 q：\n" + "\n".join(f"  {i}. {t['name']}"
                                                   for i, t in enumerate(qs, 1)))
    if a.decide and not a.user_verbatim:
        die("q 的 answer 只许是用户说过的话（§3.6）——确认后加 --user-verbatim。")
    result = op("plan.q", {"id": card["id"], "n": a.n, "decide": a.decide or None,
                           "today": a.today})
    print(f"「{card['title']}」的第 {a.n} 条 q → "
          f"{'decided' if a.decide else 'open'}；{written(result)}")


def cmd_ops(a):
    """最近的操作日志：谁（actor）在什么时候用哪个操作改了什么。找回误改时从这里查。"""
    if not 1 <= a.limit <= 500:
        die("--limit 取 1 到 500")
    try:
        data = request(f"/api/ops?limit={a.limit}")
    except urllib.error.HTTPError as e:
        die(_op_failure(e) if e.code in (404, 405, 410) else f"服务端返回 HTTP {e.code}")
    except Exception as e:
        die(f"无法读取操作日志（{e}）")
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
    print(f"\n显示 {len(entries)} 条（最新在前）；--json 看完整记录")


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

    s = sub.add_parser("plan", help="看 plan 的轨道与格子；--set FILE 整份替换（- 读标准输入）")
    s.add_argument("ref")
    s.add_argument("--set", metavar="FILE", help="新 plan 的 JSON（{\"tracks\": [...]}），格子名 ≤ 30 字，说明放 detail")
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

    s = sub.add_parser("ops", help="最近的操作日志（最新在前）")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_ops)

    s = sub.add_parser("notify", help="敲一下页面刷新")
    s.set_defaults(fn=cmd_notify)

    s = sub.add_parser("backup-status", help="最近一次本地备份和 GitHub 推送状态")
    s.set_defaults(fn=cmd_backup_status)

    a = p.parse_args()
    if getattr(a, "open_", False):
        a.decide = None
    atexit.register(maybe_update)   # 命令（含 die 退出）结束后再更新：不改正在运行的这一次
    a.fn(a)


if __name__ == "__main__":
    main()
