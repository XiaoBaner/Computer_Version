# 周末联调 · Flask 与浏览器接口 v1

周六上午前后端可以直接使用本文示例。陈尧武维护 HTTP、WebSocket 和会话状态；施孔烨提供审核后的回复与记忆操作；王皓对齐采集、转写与真实播放；陈跃权按事件渲染角色；王杰提供视觉事件，郭子毅交付带版本的分类器和类别表，由王杰接入。机器可读字段表、双向样例与本地回放分别见 [事件合同](../contracts/events.v1.json)、[联调样例](../contracts/examples.v1.json)、[回放脚本](../tools/replay_contract.py)。

## 1. 页面、身份与首次开口

1. 打开 `GET /`。浏览器为当前页面生成一次随机 `X-Page-ID` 请求头，调用 `GET /api/session/bootstrap`。同页请求失败重试沿用同一个键；真正刷新或另开标签页生成新键。Flask 按「已绑定用户、页面键」返回同一个逻辑会话，动态响应使用 `Cache-Control: no-store`。新会话可以读到同一用户已生效的记忆。
2. 浏览器用相同站点的会话 Cookie 连接 `/ws/session`，首包发送 `session.ready`。Flask 先校验 Origin 和 Cookie，再将首包的 `session_id` 与当前用户的会话匹配。一个逻辑会话只使用一个有效连接，重连替换旧连接，同时使旧连接和后台任务失效。浏览器权限、人物校准和模型预热与界面加载并行。
3. 第一次有效 ready 后由角色先发已审核的问候，例如「你好，今天想从哪儿聊起？」。服务端原子登记该会话已开场，再发欢迎回合。允许播放时同步播音与字幕；`audio_output=false` 时立即显示首句，回执 `shown`。同页解锁声音后可再次发 ready 更新播放能力，只影响后续句子。重连复用内存里的会话 ID 与页面时钟，取消断线前未完成的回复和播放，继续接收新的输入。首次问候传输途中断线时，页面恢复到可开聊状态。用户开口可随时打断欢迎。
4. 身份取自服务器绑定的 Cookie。本机未接账号系统时，Flask 在首次打开页面时创建随机匿名用户键，使用签名会话 Cookie 跨刷新绑定；服务器重启所用签名密钥保持稳定。双用户验收用独立浏览器配置或互相隔离的会话。请求里自报的用户 ID 无授权效力。服务端重启或会话过期时发 `SESSION_EXPIRED`，前端更换页面键重新引导新会话。客户端断网时保留尚未提交的文字草稿及重连入口；语音服务单独不可用时，在线文字对话仍可继续。

**可直接使用的初始化返回示例**

```json
{
  "v": 1,
  "session_id": "S1",
  "ws_path": "/ws/session",
  "scene_id": "default",
  "voice_style": "neutral",
  "features": {"asr": true, "tts": true, "vision": true, "memory": true}
}
```

`features` 表示服务器当前可提供的能力；浏览器结合本地麦克风、相机与音频播放状态决定是否开启。服务地址由当前页面推导，localhost 支持本机联调，跨设备使用 HTTPS/WSS。

## 2. HTTP 请求与结果

HTTP JSON 使用 UTF-8。记忆接口共享浏览器会话 Cookie，每次按当前用户过滤；修改校验同源请求。路径参数均为服务器分配的非空字符串。记忆 `kind` 取 `preference` 或 `follow_up`；`revision` 为从 1 开始的整数。下表中的路径参数以花括号标出。

| 方法与路径 | 请求 | 成功结果 |
| --- | --- | --- |
| `GET /` | 无请求体 | 200，HTML 角色页 |
| `GET /health` | 无请求体 | 200，`{"status":"ok","v":1}`。进程无法提供会话服务时返回 503。可选模型按 bootstrap 的能力开关表示 |
| `GET /api/session/bootstrap` | `X-Page-ID` | 200，见上例。绑定与读取已有背景在服务端完成 |
| `GET /api/memories` | 无请求体 | 200，`{"items":[{"memory_id":"M1","kind":"preference","text":"语速慢一点","revision":1}]}`。无生效项时返回空数组 |
| `PATCH /api/memories/{memory_id}` | `{"text":"按正常语速说","revision":1}` | 200，返回更新后的单个记忆对象，如 `{"memory_id":"M1","kind":"preference","text":"按正常语速说","revision":2}`。旧版本修改时返回 409，客户端重新读取列表 |
| `DELETE /api/memories/{memory_id}` | 无请求体 | 204，空响应。删除同时失效当前缓存及待写回的旧更新。界面“撤回”使用此接口 |
| `POST /api/memory-drafts/{draft_id}/confirm` | `{}` 使用草稿原文，或 `{"text":"下次继续练开场"}` | 首次 201，返回新记忆对象。相同草稿重复确认同一文本返回 200 和原对象，确认不同内容返回 409 |
| `DELETE /api/memory-drafts/{draft_id}` | 无请求体 | 204，空响应。丢弃待确认草稿；与确认发生竞态时只允许一种状态转换成功 |

记忆修改与草稿状态变更走 SQLite 事务。保存口头请求以服务端标识的来源回合与动作去重，确认草稿以草稿 ID 去重。PATCH 遇到超时先 GET 核对实际文本与 revision；DELETE 遇到超时可重试，随后 404 表示该用户已不可访问。来自他人的资源与不存在的资源统一返回 404。HTTP 失败示例为 `{"error":{"code":"REVISION_CONFLICT","message":"记忆已更新，请刷新后重试。","retryable":false}}`。400 表示字段/格式错误，401 表示缺少会话凭据，403 表示请求来源不允许，404 表示资源不可用，409 表示状态或版本冲突，503 表示组件暂不可用。服务异常向界面给出简短原因和可执行的恢复方式。

## 3. 双向事件

每帧是一条独立的 UTF-8 JSON 对象。公共字段为 `v:1`、`type` 和 `session_id`。其余业务字段与它们同层，字段区分大小写。v1 中允许增加可选字段，接收方忽略不认识的可选字段；不支持的事件类型、版本和缺少的必填字段按错误处理。身份、权限和规则始终由服务器控制。本文用“网页”“服务”分别指浏览器与 Flask。

| 事件 | 发送方 → 接收方 | 公共字段以外的必填字段与用法 |
| --- | --- | --- |
| `session.ready` | 网页 → 服务 | `audio_output` 布尔。连接首包及播放能力改变时上报 |
| `input.text` | 网页 → 服务 | `input_id,text` 字符串。一次提交使用一个 ID，去除首尾空白后应非空 |
| `input.audio` | 网页 → 服务 | `seq,t_ms,sample_rate,codec,data`。采集持续到用户关闭，播放时也继续收音 |
| `vision.event` | 网页 → 服务 | `seq,t_ms,event,confidence,quality`。`event` 为 `mouth_motion/head_turn/model_result/unavailable` 之一；`quality` 为 `good/low/unavailable`，`confidence` 是 0–1 有限数值。分类字段见下文 |
| `transcript` | 服务 → 网页 | `input_id,text,is_final`。同 ID 的临时转写覆盖显示，final 至多触发一次回应。重复发送已接纳的相同文本输入可用同 ID 的最终转写作接收回执 |
| `turn.state` | 服务 → 网页 | `turn_id,state,reason`。七态为 `ready/listening/waiting/preparing/speaking/interrupted/closing`。首次发布由用户输入产生的回合带 `input_id`；欢迎回合无此字段。可选 `expression` 见下文 |
| `reply.text` | 服务 → 网页 | `turn_id,sentence_id,text,voice_style`。发出的是审核通过的完整短句，`voice_style` 为 `neutral/gentle/practice/check_in`。语音模式先缓存，文字模式立即展示 |
| `reply.audio` | 服务 → 网页 | `turn_id,sentence_id,seq,sample_rate,codec,data,is_last`。按句编号和序号入队，末块的 `is_last=true` |
| `reply.playback` | 网页 → 服务 | `turn_id,sentence_id,state,t_ms`。`started` 为真实首音，`ended` 为整句实际播完，`cancelled` 为停止，`shown` 为文字模式实际展示 |
| `turn.cancel` | 双向 | `turn_id,reason`。例如 `barge_in`。网页收到或本机确定打断时先停声、闭口和清队列。服务收到后停止生成或丢弃提供方迟到的结果；首次处理网页取消时回送相同取消作为确认 |
| `scene.set` | 服务 → 网页 | `scene_id,transition_ms,reason`。`scene_id` 为 `default/quiet/practice`。与回复绑定的渐变附 `turn_id` 以便取消，长期场景设置省略此字段 |
| `memory.propose` | 服务 → 网页 | `draft_id,kind,text,source_turn_id`。草稿可改可弃，显式确认后才保存 |
| `memory.saved` | 服务 → 网页 | `memory_id,kind`。只在提交成功后发，按同一 memory_id 去重显示；用 GET 恢复完整数据 |
| `session.error` | 服务 → 网页 | `code,message,retryable`，可附 `turn_id,sentence_id` 指向失败对象。示例码 `INVALID_EVENT/UNSUPPORTED_VERSION/SESSION_EXPIRED/CONFLICT/AUDIO_GAP/ASR_UNAVAILABLE/TTS_UNAVAILABLE/MODEL_TIMEOUT` |

`t_ms`、`seq`、`sample_rate`、`transition_ms` 为整数，时间使用毫秒；`seq` 和时长非负。`is_final/is_last/retryable` 为 JSON 布尔。`reason` 为短字符串，客户端按状态和类型执行动作。七张角色表情对应 `neutral/attentive/concern/thoughtful/clarify/relief/interrupted`；缺少表情字段时前端按当前状态选择默认脸。

**视觉扩类。** `model_result` 同时携带非空 `model_version` 与非空 `labels`，例如 `"labels":[{"label_id":"unknown","score":0.42}]`。每项的 `score` 为 0–1 有限数值，`label_id` 来自当前权重的标签表。单标签模型传选中项，多标签模型可传多项。无可用画面时发 `event="unavailable",quality="unavailable",confidence=0`，前端停止使用旧检测结果；分类不确定但画面可用时可报告标签表定义的 `unknown`。摄像头关闭后只发一次不可用，浏览器清空画面缓存。Flask 的此接口只消费事件，训练素材按独立训练流程交付。

## 4. 标识、音频和超时

- `session_id` 标识一次页面会话。文本 `input_id` 由网页分配，语音 `input_id` 由服务在开始一段转写时分配，两种来源使用不同前缀或 UUID。正式输入 `I1` 被接纳时，服务创建 `turn_id=R1` 并持有 `I1 → R1` 映射，再运行规则、对话和记忆操作。网络重试相同 `input_id`、相同文本复用该回合；同 ID 不同正文回 `CONFLICT`。模型草稿的 `source_turn_id=R1` 通过映射指回用户实际输入。欢迎 `R0` 没有用户输入，不能作为个人记忆来源。
- `sentence_id` 在回合内唯一。服务器先发相应 `reply.text` 再发音频；有多个短句，依照首次收到各句文本的顺序串行播放。音量通道以浏览器的实际播放时钟驱动口型。有真实播音时以 `ended` 进入完整句历史，文字模式以 `shown` 入历史。播到一半只记被打断。重复回执不会重复增记。客户端先确认打断的场合立即清播放，服务端回执到达前即可继续收音。取消后可各接收一次相同回合的 turn.state（interrupted）与相应已播放句子的 reply.playback（cancelled），仅作确认与记录。网页已经开始下一回合时，这些回执不得改变当前角色、字幕或音频。
- `seq` 从 0 开始。上行音频与视觉在同一逻辑会话内分别递增，重连延续计数；下行音频按 `(turn_id,sentence_id)` 单独计数。浏览器时戳从本页初始化用单调时钟计，重连不清零。端到端时延用一端的采集和播放时戳计算。跨设备原始时钟不可直接相减。
- 音频 `codec="pcm_s16le"`，单声道有符号 16 位小端裸采样，通过标准 Base64 放进 `data`，不含 WAV 头。上行 16000 Hz，推荐完整块 20 ms（320 样本、640 字节）；结尾短块仍应是完整采样。下行由服务转成 16000 或 24000 Hz，同句采样率固定，播放侧按标注采样率解码。教材示例中的 `"AAA="` 只是一个静音采样。
- WebSocket 保序；接收方忽略同作用域已处理的重复序号。上行音频丢块时服务重置该段 ASR，回 `AUDIO_GAP`，重新收取下一段，避免拼错话。视觉只保留最新有效窗口。下行句子缺块、格式错误或音频服务超时时，保留已经审核的该句字幕并转文字，停止该句排队音频。若旧句已取消则直接丢弃。等待 TTS 首包先用 3 秒作联调超时，发生时由服务发带句子 ID 的 `TTS_UNAVAILABLE`；首包之后若连续 3 秒无新块由客户端停止该句、呈现文字。仅当字幕实际作为文字回复展示时回执 `shown`。已经取消的句子收到 TTS 错误或超时时不展示、不回执 `shown`。
- 上行音频积压建议最多 2 秒。满时丢弃未完成的旧段并提示重说；用新段第一个可用序号恢复。控制/取消事件优先于音频与视觉队列。断线立即在网页停声、闭口、清待播内容，服务将该连接未完成的回复标为取消。重连只提交未确认的文本（原 `input_id`），实时音频与旧回复不重播；bootstrap 与记忆 GET 可重新请求，在线状态根据 `session.ready` 恢复。
- ASR 不可用时提示改用同一会话的 `input.text`。模型超时或输出越界由规则模块选已审核短句。已取消回合的迟到字幕、音频和回合动画全部丢弃；用户明确请求且已经提交的记忆继续以数据库为准。切用户关闭旧连接和设备流，清理浏览器队列及临时状态，按新身份启动新会话。

## 5. 第一条联调路径

以下是一段最短的**文字模式**传输记录。发送方向见事件表，每行是独立帧。最后一帧到达后，欢迎句才计入已展示的历史。

```jsonl
{"v":1,"type":"session.ready","session_id":"S1","audio_output":false}
{"v":1,"type":"turn.state","session_id":"S1","turn_id":"R0","state":"preparing","reason":"greeting"}
{"v":1,"type":"reply.text","session_id":"S1","turn_id":"R0","sentence_id":"Q0","text":"你好，今天想从哪儿聊起？","voice_style":"neutral"}
{"v":1,"type":"reply.playback","session_id":"S1","turn_id":"R0","sentence_id":"Q0","state":"shown","t_ms":120}
```

继续发 `{"v":1,"type":"input.text","session_id":"S1","input_id":"I1","text":"记住，下次陪我练开场。"}`。服务返回的首个 R1 状态带 I1。明确保存成功后发 `memory.saved`，GET 可读回；随后 DELETE 再新开页面，GET 列表应无该项。语音模式另跑欢迎播放中用户插话，验实际停声、旧音频迟到、文本续说、重连跳过问候。现场标准按[原基线第 8 节](demo-development-baseline.md#8-demo-验收标准)计时和计数。
