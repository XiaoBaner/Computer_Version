# conversation/ —— 对话、规则与记忆模块

给**下一位接手的人**看的一页式契约。读完这一页加上 `conversation/service.py` 的顶部注释，应当能直接接线，不需要猜。

- 维护人：施孔烨（规则与存储）
- 上游：Flask（陈尧武）负责身份、`I1 → R1` 回合映射、已播历史与 HTTP
- 下游：王皓负责逐句 TTS、字幕、打断停声
- 约束：本目录只依赖标准库；**不联网、不读麦克风、不在 import 时启动任何东西**

## 1 三十秒接入

```python
from conversation import service
from conversation.memory import DomainError, MemoryStore

store = MemoryStore("/var/lib/app/conversation.sqlite")      # 文件型，进程内单例
service.configure(store=store)                                # 每个 worker 启动时一次

# ① 欢迎轮（input_id 必须为 None）
reply = service.opening({"user_id": uid, "session_id": sid, "turn_id": "R0",
                         "input_id": None, "background": bg, "history": []})
rate = service.speech_rate(ctx)                               # "slow" / "normal" / "fast"
factor = service.speech_rate_factor(ctx)                      # 0.85 / 1.0 / 1.15，直接给 TTS

# ② 正式输入：先把本轮登记为来源，再回答
service.record_source(ctx, final_text, save_authorized=wants_save,
                      authorized_text=excerpt)                # excerpt 见 §4
try:
    reply = service.respond(ctx, final_text, lambda: cancelled_flag)
except service.TurnCancelled:
    return                                                    # 静默结束，不播任何兜底句

# ③ 保存/改写/删除
service.apply_memory(uid, {"op": "save", "kind": "preference",
                           "session_id": sid, "source_turn_id": turn_id,
                           "value": "语速慢一点", "request_id": rid})
```

`reply` 就是 `{"intent", "sentences", "voice_style", "memory_draft"}`：
`sentences[i]` 是**字幕与 TTS 的唯一来源**，不要拼接、截断或改写。

## 2 四个正式入口与四个辅助入口

| 函数 | 何时调用 | 失败形态 |
| --- | --- | --- |
| `opening(ctx)` | 每个会话首次 `session.ready` 之后，Flask 自己去重 | `DomainError` 400 |
| `respond(ctx, text, cancelled)` | 正式转写或键盘原话 | `TurnCancelled`（取消）/ `DomainError`（其余） |
| `list_memories(user_id)` | 页面打开记忆列表 | `DomainError` 503（库不可用，可重试） |
| `apply_memory(user_id, cmd)` | 六种记忆命令 | `DomainError` 400/401/403/404/409/503 |
| `configure(store=..., generator=..., reviewer=..., on_decision=...)` | 进程启动一次；测试里重置 | — |
| `record_source(ctx, text, save_authorized=..., authorized_text=...)` | 正式输入被接纳后、写入之前 | 403/400 |
| `speech_rate(ctx, text="")` | 与 `opening`/`respond` 一起取，传给 TTS | — |
| `apply_memory_with_status(user_id, cmd)` | 需要区分 201/200 的 `confirm` | 同 `apply_memory` |

## 3 `ctx` 逐字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `user_id` | 非空字符串 | 服务器从 Cookie 绑定得到，**不要**取页面自报身份 |
| `session_id` | 非空字符串 | 一次页面会话 |
| `turn_id` | 非空字符串 | 回复回合；欢迎轮固定 `R0`，不能作为记忆来源 |
| `input_id` | 字符串或 `None` | 正式输入的 ID；`respond` 与 `record_source` 必填，欢迎轮为 `None` |
| `background` | dict | 已授权背景。只读 `speech_rate`（`slow`/`normal`），其余字段不会外泄 |
| `history` | list[dict] | 每项 `{"role": "user"/"assistant", "text": str}` |

**history 的过滤是 Flask 的责任**（v1 帧里没有 `ended`/`shown` 字段，模块无法替你判断整句是否播完）。约定：

- 用户历史只收**正式提交**的原话；角色历史只收**整句已播完**（`ended`）或文字模式**确已展示**（`shown`）的句子；
- 半句被打断只产生事件，不进历史；
- 模块自己还会做四件事：只取末 8 条、只认 `user`/`assistant`、末条与当前输入相同则丢弃、超过 1000 字截中间。

## 4 保存授权（最容易接错的一处）

`record_source(..., save_authorized=True)` **必须**同时给 `authorized_text`，它是本轮原话里用户真正要求保存的那一小段。缺了它会直接 403，而不是默默把整轮当授权。

```python
# 对：只授权"语速慢一点"，"今天失眠"仍然不可保存
service.record_source(ctx, "记住，语速慢一点，别记今天失眠。",
                      save_authorized=True, authorized_text="语速慢一点")

# 错（403）：漏传片段
service.record_source(ctx, text, save_authorized=True)
```

- 只有"用户明确说出的那一项"能走 `save`；模型归纳出来的一律先 `propose`，由用户 `confirm`。
- **授权片段里的否定词不可截掉**：`不喜欢慢速` 不能授权 `喜欢慢速`，`别记今天失眠` 不能授权 `今天失眠`（模块会 403）。Flask 直接把用户那句片段原样传入，不要自己裁剪。
- 欢迎轮 `R0`、模型输出、相机观察都没有保存权；`respond` 收到 `turn_id="R0"` 会直接 400。
- 同一来源回合只能 `save` 一次；同一轮第二件事请改走 `propose` + `confirm`（再 `save` 会 409）。
- `confirm` / `discard_draft` / `delete` 都会**撤回该回合的保存授权**：之后同回合迟到的 `save` 一律 403，不会把刚撤回的内容重新写回去。

## 5 记忆命令、幂等键与状态

| op | 必要字段 | 幂等键 | 成功 |
| --- | --- | --- | --- |
| `propose` | `kind`、`text`、`session_id`、`source_turn_id`、`request_id` | `(sid, turn, op)` | 201 `{draft_id}` |
| `confirm` | `draft_id`，可选 `value` | `(draft_id, op)` | 首次 201，同文重试 200 |
| `save` | `kind`、`value`、`session_id`、`source_turn_id`、`request_id` | `(sid, turn, op)` | 201 完整记录 |
| `update` | `memory_id`、`value`、`expected_revision` | `(memory_id, op, expected_revision)` | 200 |
| `delete` | `memory_id` | `(memory_id, op)` | 204 空体 |
| `discard_draft` | `draft_id` | `(draft_id, op)` | 204 空体 |

- `preference` 的槽位：`voice_speed` / `address_name` / `language` / `sentence_length` / `subtitle` 自动归槽，**同槽改口会替换**（revision+1），不同槽并存。
- 草稿一旦建立就绑定了槽位：确认时把正文改成**另一个类别**（如把"语速慢一点"改成"打开字幕"）会 400，必须重新 `propose`；草稿原本无槽位则允许在确认时归入一个已知类别。
- 其它类别的 `preference` 无法自动判定是否"同一项"：请传 `preference_key`（如 `"topic:答辩"`），否则会新增一条。
- `revision` 从 1 开始；`update` 永远先验版本，旧版本（含同 `request_id` 重放）409。
- 跨用户与不存在统一 404，不区分"存在但无权"。

## 6 错误码 → HTTP

`DomainError` 字段：`status`、`code`、`message`、`retryable`。

| status | code | 场景 |
| --- | --- | --- |
| 400 | `INVALID_ARGUMENT` | 字段/格式错、来源回合不合法、过期时间已过 |
| 401 | `UNAUTHENTICATED` | 缺少用户身份 |
| 403 | `SAVE_NOT_AUTHORIZED` | 无授权片段、片段不覆盖待存内容 |
| 404 | `NOT_FOUND` | 记录不存在或不属于该用户 |
| 409 | `CONFLICT` / `REVISION_CONFLICT` | 同来源不同正文、草稿状态冲突、旧版本 |
| 503 | `MEMORY_UNAVAILABLE` | 库不可用 / 未注入 store；`retryable=True`，请给"重试"入口 |

## 7 线程、进程与存储

- **必须单进程多线程**（基线 §5.1 也是这么写的）。SQLite 用 `BEGIN IMMEDIATE` + `timeout=5` + 每事务新建连接、**未开 WAL**；多 worker 共享同一文件会在高并发下拿不到锁并返回 503。
- 测过：8 线程 × 15 次、4 进程 × 25 次写库均 0 失败；事务中途 `kill` 不会留下半条记录（`integrity_check=ok`）。
- 忘记 `configure(store=...)` 时，`opening`/`respond` 仍可用，但 `list_memories`/`apply_memory` 会 503 —— 这是故意的，避免"演示能存、重启丢光"。
- 离线演练可以显式注入 `DictMemoryStore()`；它按进程隔离，重启即空。

## 8 规则文件 `policy.yml`

- 文件名叫 `.yml`，内容其实是 **JSON**（JSON 是合法 YAML，且不引入 PyYAML 依赖）。按 JSON 编辑，按 JSON 解析。
- `configure()` 会校验：版本、枚举、句数/字数、`routes` 与代码里的 `ROUTES` 全等、`checks`/`priorities` 非空、`safety` 段完整。
- **危机、用药、诊断、怀疑、边界这 8 条话术在 `policy.yml` 的 `safety` 段**，改文案不用改代码；`safety.approval.by` 目前是"待团队核准"，`configure()` 会打一条 warning，`service.health()` 返回 `crisis_text_approved=false`。现场启用前必须填成真实核准人与日期（`on` 用日期字符串），否则 `/health` 会一直报未核准。
- 校验是**逐句**的（每句 ≤ `max_chars_per_sentence`），并拒绝"建议购买 / 可以吃 / 保证没事"这类越界措辞；正常否定句（如"不要自行停药"）不会被误杀。
- 新增路由要同时改三处：`policy.yml.routes`、`service.ROUTES`、`_route()`。三者不一致会在启动时报错。

## 8.1 语速与 TTS 倍率

| `speech_rate` | 含义 | `speech_rate_factor` |
| --- | --- | --- |
| `slow` | 用户要求说慢 | 0.85 |
| `normal` | 默认 / 要求恢复 | 1.0 |
| `fast` | 用户要求说快 | 1.15 |

王皓侧用 `factor` 乘默认语速，**不要**把 `"slow"` 字符串直传 `voice.tts.synthesize`。倍率只在集成层用，`voice_style` 仍只表达语调（neutral/gentle/practice/check_in），两者互不覆盖。
识别规则只认"说慢/说快/语速/正常速度"这类与说话相关的说法：`走路很慢`、`我最近慢热`、`我很快乐` 都不会改变语速。

## 9 跑测试

```powershell
cd F:\git\Computer_Version                       # 必须从仓库根目录跑
.\.venv\Scripts\python.exe -m pytest -q tests/conversation
.\.venv\Scripts\python.exe tools\replay_contract.py
.\.venv\Scripts\python.exe -m conversation.demo  # 离线端到端演练
```

从非仓库根目录运行会 `ModuleNotFoundError: conversation`（包不可安装，没有 `conftest.py`/`pyproject.toml`）。

## 10 已知缺口（需要其他角色补，不在本目录）

| 缺口 | 归属 | 说明 |
| --- | --- | --- |
| `/api/memories`、`/conversation/*`、`/health` 路由 | 陈尧武 | 本目录只提供函数，不提供 HTTP |
| `history` 的 `ended`/`shown` 过滤 | 陈尧武/王皓 | v1 帧无状态字段，只能靠集成层保证 |
| 危机话术与求助入口的**内容** | 团队/王皓 | 位置已备好（`policy.yml.safety`），文本待核准 |
| 真实模型端点、别名、超时 | 模型服务负责人 | `conversation/provider.py` 读 `CONVERSATION_*` 环境变量 |
| `sentence_id`、播放回执、去重入历史 | 陈尧武/王皓 | 本模块只给 `sentences[i]`，不生成 ID |
| TTS 首包 P95 ≤ 3 s、打断停声 ≤ 500 ms | 王皓 | 本模块无音频，只有文字 |

## 11 本次相对参考包的变更（记在交接时）

| 变更 | 原因 |
| --- | --- |
| `record_source` 的 `authorized_text` 变为**必填**（缺省 403） | 旧默认会把整轮当授权，`别记今天失眠` 会被存下来 |
| 授权片段改为**否定词感知**匹配 | 旧子串匹配让 `不喜欢慢速` 授权 `喜欢慢速`，意思反转 |
| `confirm`/`discard_draft`/`delete` 撤回该回合授权 | 旧实现允许"撤回后同回合迟到 save"重新入库（201） |
| 草稿确认拒绝跨槽改写 | 旧实现把"打开字幕"写进语速槽，覆盖原语速 |
| 新增 `subtitle` 槽位 | 字幕开关是独立偏好，不能和语速/称呼互相覆盖 |
| 兜底与纠正回显统一过出口校验 | 旧实现只有模型输出过校验，回显可把病名/链接念出去 |
| `speech_rate` 新增 `fast` 与数值倍率 | 王皓需要 0.85/1.0/1.15，不能直传字符串 |
| 危险分流的 `我自己`/`自己割伤`/`准备动手` 形态 | 旧关键词漏掉无"刚刚"的自伤与无方法名的计划 |
| `opening` 的 `voice_style` 固定 `neutral`，慢速只经 `speech_rate` | 语速与语调必须分开，否则 TTS 会双重减速 |
| `correction` 回显用户给出的新事实（"好，是周五。"） | 旧实现只回"谢谢你纠正我"，现场 R11 会判失败 |
| 危机/用药/诊断/怀疑/边界话术移入 `policy.yml` 的 `safety` | 改文案不必改代码，且启动时校验越界措辞 |
| 偏好按槽位归并（称呼/语言/语速/句长） | 改口要替换，不是新增 |
| 未注入 store 时记忆 I/O 返回 503 | 避免"演示能存、重启丢光" |
| `DictMemoryStore` 只能显式注入 | 同上，把"默认内存"变成明确的离线选择 |