# 操作接口规格（v1，2026-10-21）

网页、CLI 和服务内部的定时任务都只通过本文件列出的**标准操作**改数据。每个操作只碰它点名的卡、字段或收件箱条目；任何操作都不能上传整份看板。规则（状态切换、事件账本、计划戳、迁移）只在服务端 Python 里有一份。

为什么这样做：2026-10 之前客户端整份上传 `board.json`、靠 `savedAt` 做 CAS 和三路合并。一个长时间开着的标签页可以拿着有效令牌、旧内容把 agent 新建的卡整份盖掉（「用我的 · 覆盖外部改动」按钮），合并基准存在浏览器本地存储、被所有标签页共用，也会把「我这里没有这张卡」误判成「我删了它」。操作接口从结构上排除了这一类：删除只能是显式操作，不从缺失推断。

## 1. 存储

`BOARD_DATA_DIR/board.sqlite`（SQLite，Python 标准库）。`journal_mode=DELETE`、`synchronous=FULL`：备份进程以只读方式打开同一个文件，WAL 模式在只读挂载下打不开。

| 表 | 列 | 说明 |
|---|---|---|
| `meta` | `key TEXT PRIMARY KEY, value TEXT` | `rev`（全局修订号，整数）、`saved_at`（最近一次改动的毫秒时刻）、`last_active_id`、`board_version`（=2） |
| `cards` | `id TEXT PRIMARY KEY, ord INTEGER, rev INTEGER, body TEXT` | `body` 是整张卡的 JSON（含 `events`，空字段不落），`rev` 是这张卡的修订号，`ord` 保持导入时的数组顺序，新卡取最大值 + 1 |
| `inbox` | `id TEXT PRIMARY KEY, ts INTEGER, body TEXT` | `body` 是 `{id, ts, text}` |
| `ops` | `seq INTEGER PRIMARY KEY AUTOINCREMENT, op_id TEXT UNIQUE, ts INTEGER, actor TEXT, client_id TEXT, op TEXT, args TEXT, undo TEXT, response TEXT` | 操作日志，只增。`undo` 存被改字段的旧值或被删对象的全文，用于人工找回 |

库结构版本记在 `PRAGMA user_version`。改结构 = 在 `board_store.py` 的迁移列表末尾加一个函数；启动时按序执行未执行的迁移，每个迁移一个事务。

## 2. 读

`GET /board.json` 返回：

```json
{ "version": 2, "rev": 1234, "savedAt": 1760000000000, "lastActiveId": "…",
  "threads": [ <卡片视图> ], "inbox": [ {"id": "…", "ts": 0, "text": "…"} ] }
```

**卡片视图** = 存储的卡 + `"rev"`（这张卡的修订号）+ `"derived"`：

```json
"derived": { "breakpointAt": ms|null, "nextActionAt": ms|null, "enteredAt": ms|null,
             "doneAt": ms|null, "trashedAt": ms|null, "restoreTo": "ready"|…,
             "sessions": [ {"ts": ms, "out": ms, "capped": bool, "noticedAt"?: ms} ] }
```

`derived` 由服务端按 `board_schema.py` 从 `events` 推出，不落库，客户端也不回传。`threads` 按 `ord` 排序；`inbox` 按 `ts` 降序。

`GET /api/ops?limit=N&before=SEQ` 返回最近的操作日志（`seq` 降序，默认 50 条，最多 500）。

## 3. 写

`POST /api/ops`，请求体：

```json
{ "api": 1, "opId": "<客户端生成的 uuid>", "op": "<操作名>", "args": { … } }
```

头：认证（会话 cookie 或 `Authorization: Bearer`），`X-Client-Id`（SSE 用来区分自己的改动）。

| 状态 | 体 | 含义 |
|---|---|---|
| 200 | `{"ok": true, "rev": N, "opSeq": S, "changed": {…}, "result": {…}, "duplicate": bool}` | 已执行（或这个 `opId` 以前已执行过，`duplicate: true`，不重复执行） |
| 400 | `{"error": "bad_request", "detail": "…"}` | 未知操作、参数缺失或不合法 |
| 400 | `{"error": "api_version", "detail": "…"}` | `api` 版本不认识（客户端和服务端对不上，CLI 会先自动更新再重跑，见 ARCHITECTURE §6） |
| 404 | `{"error": "not_found", "detail": "…"}` | 点名的卡或收件箱条目不存在 |
| 409 | `{"error": "stale", "detail": "…", "card": <卡片视图>}` | `if` 里的字段已被别处改过；什么都没写 |
| 409 | `{"error": "op_id_reused", "detail": "…"}` | 同一个 `opId` 配了不同的操作或参数 |
| 422 | `{"error": "rule", "detail": "…"}` | 违反不变量（第二张 running、永久删除非垃圾桶卡、导入未确认等）；什么都没写 |

`changed`：`{"cards": [卡片视图…], "removed": [id…], "inbox": [全部条目]（仅收件箱变了时出现）, "lastActiveId": "…"（仅变了时出现）}`。客户端按 id 替换 / 追加 / 删除，不重算任何规则。

没有任何实际变化的操作（例如值没变）返回 200、`changed.cards` 为空、`rev` 不变，仍记入操作日志以保证幂等。

每个成功且有变化的操作：在一个事务里改相关行、被改的卡 `rev + 1`、`meta.rev + 1`、`saved_at = max(now, 旧值 + 1)`、写一行 `ops`；提交后 SSE 广播 `{"type": "change", "rev": N, "origin": "<X-Client-Id>"}`。

**`if` 条件**：`card.update`、`card.switch` 和 `plan.step` 可带 `"if": {字段: 期望的旧值}`。比较前把缺失、`null`、`""` 视为同一个值；`plan` 只比较 `tracks`（规范化 JSON）。任一字段不等 → 409 stale。只有调用方要改的字段才需要放进 `if`；别人改了这张卡的其他字段不算冲突。

**`touchedAt`**：任何改动卡片的操作都把它设成本次操作时刻（`card.purge` 除外）。

旧接口：`PUT /board.json` 和 `POST /merge` 返回 410 `{"error": "gone", "detail": "写入已改用 POST /api/ops；请刷新页面或更新 CLI"}`。还开着的旧页面因此不能再整份覆盖。`POST /notify` 保留（只广播）。

## 4. 操作清单

时刻 `now` 由服务端在操作开始时取一次，同一操作内所有事件共用。

| 操作 | 参数 | 效果与规则 |
|---|---|---|
| `card.create` | `title`（必填）、`priority`=1、`lane`="project"、`due`、`deliverable`、`log`、`blockingOthers`、`cadence`、`nextAction`、`breakpoint`、`state`="ready"\|"blocked"、`waitingOn`、`plan` | 服务端生成 `id`（uuid4）、`createdAt`=`touchedAt`=now、`events`=[]。`nextAction` / `breakpoint` 非空时各记一条事件；`state`="blocked" 时记一条 state 事件并写 `waitingOn`；`plan` 的 `summaryAt`=now。`result: {"id": …}` |
| `card.update` | `id`、`set: {字段: 新值}`、`if`（可选） | 可改：`title`（非空）、`priority`（0/1/2）、`due`（"" 或 YYYY-MM-DD）、`deliverable`、`breakpoint`、`nextAction`、`waitingOn`、`blockingOthers`、`lane`（project/standing/errand）、`cadence`（正整数或 null）、`log`、`plan`（null = 删除；否则对象且 `tracks` 为数组、每条 `kind` 是 flow 或 q）、`sample`（只能设成 null，用于清掉旧标记）。`breakpoint` / `nextAction` 走 `set_text`（值变了才记事件）。`plan`：`tracks` 变了则 `summaryAt`=now，没变则保留旧 `summaryAt`；客户端给的 `summaryAt` 忽略。`state` 不在这里改 |
| `card.state` | `id`、`to`（ready/running/blocked/done/trashed）、`waitingOn`（可选） | 同状态 = 无变化。进 running：已有别的 running 卡 → 422。离开 running：`lastActiveId`=这张卡。进 ready 或 running：删 `waitingOn`。进 blocked：给了 `waitingOn` 就写入 |
| `card.restore` | `id` | 必须在垃圾桶（否则 422）。目标状态 = `derived.restoreTo`（进桶前的状态；进桶前是 running 则 ready；没有记录则 ready），其余同 `card.state` |
| `card.reenter` | `id`、`nextAction`（非空） | 「断点凉了」后的重新进入：`set_text(nextAction)`，再按 `card.state` 规则进 running |
| `card.switch` | `from`（当前 running 卡）、`to`（目标卡，可空 = 只挂起）、`breakpoint`、`nextAction`、`dest`（ready/blocked）、`waitingOn`、`startTarget`=true、`if`（对 `from` 的 breakpoint / nextAction） | 一个事务：`from` 必须是 running（否则 422）；`set_text(breakpoint, reaffirm=True)`（原样留下也记一条，表示重新确认）、`set_text(nextAction)`、`from` 进 `dest`、`dest`=blocked 时写 `waitingOn` 否则删、`lastActiveId`=`from`；`to` 非空且 `startTarget` 时 `to` 进 running 并删 `waitingOn`。网页在目标断点已凉时传 `startTarget: false`，再单独发 `card.reenter` |
| `card.purge` | `id` | 必须是 trashed（否则 422）。删除这一行；`undo` 存整张卡 |
| `note.add` | `id`、`text`（非空） | 追加 `{ts: now, kind: "note", text}` |
| `note.delete` | `id`、`ts`、`text` | 删掉 `kind=note` 且 `ts`、`text` 都相等的那一条（从后往前找第一条）；找不到 → 404。`undo` 存被删条目 |
| `inbox.add` | `text`（非空） | 新条目 `{id: uuid4, ts: now, text}`。`result: {"id": …}` |
| `inbox.delete` | `id` | 删除；条目已不存在 = 无变化（不报错）。`undo` 存条目 |
| `inbox.promote` | `id` | 条目升成新卡：`title`=text 前 40 字、`state`=ready、`priority`=2、`lane`=project、`nextAction`=text（记一条 na 事件）；删条目。不存在 → 404。`result: {"id": 新卡 id}` |
| `inbox.move` | `id`、`card` | 条目文字作为一条 note 追加到 `card`，删条目。任一不存在 → 404 |
| `plan.step` | `id`、`track`（`plan.tracks` 的下标，或 flow 轨道名：先全等再子串，必须唯一）、`step`（0 起下标）、`done`（bool）、`if: {"text": 这一格的文字}`（可选） | 设这一格的 `done`，`plan.summaryAt`=now。格子文字不符 → 409 stale |
| `plan.q` | `id`、`n`（q 轨道中的序号，1 起）、`decide`（字符串或 null）、`today`（YYYY-MM-DD） | `decide` 非空：`status`=decided、`answer`、`at`=today；为 null：`status`=open，删 `answer`、`at`。`plan.summaryAt`=now |
| `board.import` | `board`（整份 v2 或 v1 看板）、`confirm`=true | 只供网页「导入覆盖」和维护使用。未确认 → 422。先把当前数据导出到 `BOARD_DATA_DIR/pre-import-<ms>.json.gz`，再整体替换；`undo` 只记那个文件名和条数 |

服务内部操作（不经 HTTP，`actor`="server"）：`board.sweep` —— RUNNING 超过 6 小时自动切回 ready，规则同改造前的 `sweep()`。

## 5. 客户端约定

- 用 API 令牌认证的响应都带 `X-Board-Client: <版本>`，即服务端随镜像发布的客户端安装包的内容哈希。`GET /api/client`（需认证）返回 `{"version": "…", "files": {"相对路径": "文本内容", …}}`。这两处是旧客户端找到新版本的唯一通道，只许加字段，不许改坏（ARCHITECTURE §6）。

- 每次操作生成新的 `opId`。网络错误时用**同一个** `opId` 重试一次；服务端按 `opId` 去重，所以重试不会让一条备注记两次。
- 网页不做乐观更新、不存本地数据副本：发操作 → 等响应 → 按 `changed` 改内存里的视图 → 重画。收到别人的 SSE 改动就重新 `GET /board.json`。服务器连不上时页面只读，并提示这次操作没有保存。
- 409 stale：重新读取，告诉用户「这张卡刚被别处改过」，由用户重做；不要自动重试。
- CLI 的红线（断点 / 下一步要 `--user-verbatim`）和「不要重复建同名卡」是给 AI 的使用规则，留在 CLI；数据规则只在服务端。

## 6. 迁移与回退

- `board-admin.py import-json <board.json>`：把现有 `board.json` 导入一个**新的** `board.sqlite`（已存在则拒绝，除非 `--replace`，替换前先导出旧库）。导入后自动导出一次并与原文件逐卡比较（忽略 `savedAt`、`rev`），不一致则删掉新库并报错。
- `board-admin.py export-json [--out FILE]`：导出存储形状的整份看板（不含 `derived`）。备份和回退都用它。
- 服务启动时如果只有 `board.json`、没有 `board.sqlite`，拒绝启动并提示先运行 `import-json`；不自动迁移。
- 回退到旧版本：停新服务 → `export-json --out board.json` → 启动旧镜像。
