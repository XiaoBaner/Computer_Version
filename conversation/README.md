# 对话规则与记忆模块交接

施孔烨维护 `conversation`。陈尧武的 Flask 接收正式输入、生成回合 ID、传入已送达的历史，再把审核结果发给页面和王皓的语音模块。一个回复包含 `intent`、`sentences`、`voice_style`、`memory_draft` 四个字段。其中每条 `sentences[i]` 原样用于同一句字幕和 TTS。角色可以说一至两句，每轮最多问一个澄清问题。

## 从仓库根目录运行

在将要联调的 **Computer_Version 项目工作树**中运行下面命令。`git rev-parse --show-toplevel` 可核实当前根目录，`git rev-parse --short HEAD` 可记录实际测试版本。与队友共享的是该版本的模块、测试和契约；桌面交接包及独立验证用工作树是各自的副本。下面的 `python` 指项目虚拟环境中已安装项目测试依赖的解释器。

```powershell
python -X utf8 -m conversation.demo
python -X utf8 -m pytest -q tests/conversation
python -X utf8 tools/replay_contract.py
```

第一条是可直接运行的离线模块演示。它用虚构身份、固定回复和临时文件型 SQLite 演示开场、保存前核实事项、改口、重新打开库、撤回和取消，结束后清理临时库。第二条跑模块回归。第三条校验仓库自带的四组 v1 消息示例。三条均可在无模型密钥时运行。Flask 请求、浏览器回执、扬声器输出和实体插话停声另做联调。

## 服务启动与关闭

由 Flask 在每个服务进程启动时创建 store 并调用一次 `configure`。进程内的请求共享该配置，身份由每次调用的 `ctx` 或 `user_id` 指定。重新配置会替换当前运行时，上一个 store 仍由宿主负责关闭。关停或更换 store 前先停止接收请求并等待在途任务结束。多个 worker 共用一个 SQLite 文件时，应在预期并发下另测锁等待与可重试的 503。

```python
from pathlib import Path
from conversation import service
from conversation.memory import MemoryStore

def configure_dialogue(instance_path):
    directory = Path(instance_path)
    directory.mkdir(parents=True, exist_ok=True)
    store = MemoryStore(directory / "conversation.sqlite")
    service.configure(store=store)
    return store  # 服务关闭时调用 store.close()
```

该配置使用文件库和项目内置回复。接真实模型时，由宿主从服务端配置创建 `conversation.provider.ChatProvider`，在同一次启动配置中传入 `generator=provider.generate, reviewer=provider.review`。适配器读取 `CONVERSATION_ENDPOINT`（完整的兼容 chat/completions 的地址）、`CONVERSATION_MODEL`，以及按实际服务需要配置的 `CONVERSATION_REVIEW_MODEL`、`CONVERSATION_TOKEN`、`CONVERSATION_TIMEOUT`。不要将凭据放进仓库、浏览器或共享日志。

`service.health()` 可检查规则版本、存储注入情况与危机话术核准标志。未注入 store 时记忆列表与写入返回可重试的 503；测试若需要易失存储，可显式注入 `DictMemoryStore`。文件库跨重开保留数据，线上回复服务、语音服务以及危机话术的实际可用性由对应负责人验证。

升级已有文件库前，停止使用该库的服务并备份完整的 SQLite 数据库。新版首次打开旧库会在事务中补充输入的接纳顺序（`accepted_order`），整理可识别的偏好类别，并使尚未消费的旧直接保存授权失效。升级后若仍需保存这类事项，请用户重新明确授权或确认有效草稿。需要回退时停服务，用升级前的程序与数据库备份一起恢复。

## 每轮怎样交给 Flask 和语音

1. **首次就绪。** Flask 按已绑定身份和逻辑会话为第一次 `session.ready` 分配欢迎回合。构造 `ctx`（`user_id, session_id, turn_id="R0", input_id=None, background, history`）后调用 `service.opening(ctx)`。同会话重连或播放能力改变，沿用原会话并去重。
2. **受理输入。** 键盘使用 `input.text`，语音使用 ASR 的 `on_transcript(input_id, text, is_final)`。临时转写可以显示。每个正式输入使用新 ID，同一逻辑会话重连重发原输入时沿用原 ID。Flask 将一个正式输入绑定到一个回复回合，并以原话登记 `service.record_source(ctx, text)`。数据库按 `user_id + session_id + input_id` 去重。欢迎轮、摄像头观察和模型输出都没有用户输入来源。
3. **生成回复。** 后台执行 `service.respond(ctx, text, cancelled)`。`cancelled` 是可反复调用的无参回调，只读取取消标志并返回布尔值。收到 `TurnCancelled` 时结束该回合并丢弃迟到结果。生成器和审核器可分别注入。未通过审核、失败或不确定时由服务选择相应的预审回复。
4. **逐句交付。** Flask 为每句分配 `sentence_id`，先发 `reply.text`。有声模式把同一句文字原样交给 `voice.tts.synthesize(sentence, reply["voice_style"], cancelled, speech_rate=service.speech_rate_factor(ctx, text))`，将返回的 PCM 和采样率封装成该句的 `reply.audio`。欢迎轮省略倍率函数的 `text` 参数。`voice_style` 表示语调；`speech_rate(ctx, text)` 给出慢、正常、快三档名称，数值函数提供 0.85、1.0、1.15。向 TTS 的 `speech_rate` 参数传数值。
5. **回执与恢复。** `ctx.history` 的每项为 `{"role": "user" 或 "assistant", "text": "原话"}`。Flask 保存正式用户输入，再按 `turn_id + sentence_id` 把实际整句播完的 `reply.playback.state=ended` 或文字模式实际呈现的 `shown` 计入历史一次。只收到 `started`、`cancelled` 或断线不代表整句送达。断线后取消旧回合，丢弃迟到音频；新会话重新读取同一用户已授权且有效的记忆。旧会话未经确认的草稿、过期观察及半句播放均不得当成既有事实。

## 语音接线条件

| 交接方 | 交付时验证 |
| --- | --- |
| ASR → Flask | 陈尧武为每次有效连接分配内部代号，重连先使旧代失效。仅接纳当前身份、会话和连接代号的最终转写。王皓核实正在识别时断线能释放后端。向旧连接补投最终转写，新连接产生零条回复、零次记忆写入。 |
| TTS → Flask | 王皓区分成功、失败和取消。陈尧武在提交文本前、转发各块前检查取消。仅在确认整句合成成功时发 `is_last=true`。首包等满 3 秒、流中出错或无法确认完整时，活跃句发送带回合与句子 ID 的 `TTS_UNAVAILABLE`，改用已审核字幕。取消的回合丢弃迟到块与错误。 |
| Flask → 页面 | 陈跃权在音频入队前按 `turn_id + sentence_id + seq` 去重。取消或断线时立即停声并记住该回合，即使首包尚未到达也丢弃迟到块。活跃句缺块或首包后连续 3 秒无新块，停止该句，实际展示全文后回执 `shown`。整句实际播完才发 `ended`。 |

陈尧武为一轮接话设总时限，覆盖生成、审核和等待首声。`CONVERSATION_TIMEOUT` 约束单次模型请求。到达回合时限时结束等待、忽略迟到结果，活跃回合使用预审短句或转文字。

## 授权保存与记忆 HTTP 映射

Flask 从已认证用户的正式输入中识别授权，绑定到 `session_id`、`source_turn_id`、`input_id` 和一个完整事项。原话“记住语速慢一点，别记今天失眠。”只能登记 `save_authorized=True, authorized_text="语速慢一点"`。随后 `save` 的 `value` 与该片段经 NFKC 归一化、忽略空白后须完全相等。保留否定词、限制语及标点。该例中自行增加句号会返回 403，把“别记今天失眠”登记为授权片段会返回 400。需要改写、归纳或区分多个事项时，提交可编辑草稿。所有者可以通过 `confirm` 明确确认有效草稿，即使来源回合没有直接保存授权。

模块提供 `list_memories(user_id)` 和 `apply_memory(user_id, cmd)`。HTTP 适配层用 `apply_memory_with_status(user_id, cmd)` 取得 `(结果, 状态码)`。每个命令携带服务端生成的稳定 `request_id`。聊天来源须包含 `session_id, source_turn_id`，记忆类型仅限 `preference` 和 `follow_up`。

| 场景 | 调用或字段映射 | 页面结果 |
| --- | --- | --- |
| 读取已生效事项 | `GET /api/memories` → `list_memories` | 200，`{"items":[...]}`；无记录时为空数组 |
| 用户明确授权保存 | `save(kind, value)` 带来源；相同来源重放保持幂等 | 事务提交后才发 `memory.saved`，只携带 `memory_id, kind` |
| 提议及确认 | `propose(kind, text)` 带来源；`POST /api/memory-drafts/{id}/confirm` 将可选 `text` 映射到 `confirm.value` | 草稿可编辑。首次确认 201，同文重试 200，异文冲突 409 |
| 改口或编辑 | `PATCH /api/memories/{id}` 将 `text, revision` 映射为 `update.value, expected_revision` | 成功返回最新记录与版本；超时先 GET，旧版本 409。只能在同一偏好类别内改写（把“语速慢一点”改成“叫我老王”返回 400，需另行提交草稿）；正文限 1–300 字 |
| 撤回或丢弃 | `DELETE /api/memories/{id}` → `delete`；`DELETE /api/memory-drafts/{id}` → `discard_draft` | 成功 204 空体。删除后失效相关缓存，确认与丢弃冲突返回 409 |

`DomainError` 携带 `status, code, message, retryable`，由 Flask 映射 HTTP。缺少身份返回 401，未获授权的直接保存返回 403，他人资源和不存在的资源同样返回 404。页面恢复时重新读取有效记录与 revision，已删除的偏好和陈旧的 bootstrap 缓存不得再次传入 `ctx.background`。

同一来源回合内，同一操作携带不同事项重试会返回 409。第二件事通过新授权或有效草稿确认。内置偏好类别为 `voice_speed`、`address_name`、`language`、`sentence_length`、`subtitle`。自定义偏好需要连续改口并替换同一条记录时，由服务端传稳定的 `preference_key`。

## 实机联调前准备三项

1. **话术核准。** 由团队负责人核准现场危机话术与联系方式，在 `conversation/policy.yml` 的 `safety.approval` 登记核准人和日期，再核对 `service.health()["crisis_text_approved"]`。
2. **真实服务配置。** 配好可用的生成与审核端点、模型和所需凭据，以及王皓的真实 ASR/TTS，先用一条虚构输入核对转写、审核句与播报文字。
3. **正式页面与展示机。** 在预定电脑启动 Flask 与浏览器页面，接麦克风和扬声器。普通接话与有效插话各测至少 20 次，按实际首声和停声记录模型与设备、中位数、P95、样本数及失败次数。目标为普通结束到首声 P95 ≤ 3 秒、插话开口到停声 P95 ≤ 500 毫秒，漏停单列为失败。离线结果单列。[现场验收基线](../docs/demo-development-baseline.md#8-demo-验收标准)

完整消息字段、取消时序与负责人分工见[周末网络契约](../docs/weekend-wire.md)和[模块对接表](../docs/weekend-modules.md)。
