# 六人模块交接与独立联调

用于 2026 年 11 月 28 日、29 日集中开发。以下是周末要实现的内部调用约定。浏览器与 Flask 之间使用[网络协议](weekend-wire.md)。每个 WebSocket 消息都是平铺的 JSON，含 `v`、`type` 和 `session_id`。文中接口名是周末开发的目标入口，替身只用于并行联调，演示时标明实际服务与模型版本。

| 负责人 | 提供给谁 | 周六先交什么 | 周日与谁验什么 |
| --- | --- | --- | --- |
| 施孔烨 · 对话、规则与记忆 | 陈尧武、王皓 | 开场、审核回复、记忆入口及固定回答 | 陈尧武验规则至少 16 例、记忆至少 8 例。王皓核对字幕和实际播报 |
| 陈尧武 · Flask 集成 | 全组 | 启动入口、bootstrap、WebSocket、带替身的首轮对话 | 请另一人从干净目录复跑。实机走开场、插话、修改记忆与回访 |
| 王皓 · 语音 | 陈尧武、陈跃权 | ASR/TTS 和浏览器播放入口、样音与停声回调 | 陈跃权验声画。实际普通接话和有效打断各至少 20 次，另测短应和与外放回声 |
| 陈跃权 · 2D 角色与页面 | 王皓、王杰、陈尧武 | 页面挂载点、角色与场景资源、按事件驱动的状态页 | 实机验 7 表情、3 眼睑、5 嘴型、7 对话态、声画同步、无声使用 |
| 王杰 · 实时 CV | 陈跃权、陈尧武 | 浏览器观察器及有效/不可用两类样例 | 陈尧武复跑至少 24 段视觉对照。王杰独立验收候选权重并接入 |
| 郭子毅 · 专职扩类训练 | 王杰 | 真实数据与类别盘点、按人划分、八类基线及推理样例 | 交两版结果、模型与推理命令、逐类统计及两个错误案例。部署由王杰负责 |

## 1. 施孔烨 → 陈尧武、王皓

提供 `conversation/service.py`。参数中的 `ctx` 是仅在服务端使用的字典，含 `user_id: str`、`session_id: str`、`turn_id: str`、`input_id: str | None`、`background: dict` 和 `history: list[dict]`。每条历史含 `role, text`。用户输入只记正式提交，角色历史收录已播完或文字模式实际展示的句子。`cancelled` 是无参数、返回布尔值的回调。

| 签名 | 调用方与返回值 | 错误及重试 |
| --- | --- | --- |
| `opening(ctx) -> ApprovedReply` | Flask 收到首次就绪后调用。角色可主动说“你好。今天想从哪件事聊起？”。有已确认语速偏好就应用 | 背景未到先用通用开场。Flask 负责会话内去重 |
| `respond(ctx, text: str, cancelled) -> ApprovedReply` | 收到正式转写或键盘输入后，先路由，再生成 1–2 句，审核最终字幕与拟播文本。王皓只接审核后的同一文本 | 候选结构错、越界或供应商失败用预审短句。已取消回合放弃输出 |
| `list_memories(user_id: str) -> list[dict]` | 返回当前用户有效、未过期的记录 | 新用户返回空数组。数据库故障交集成层提示重试 |
| `apply_memory(user_id: str, cmd: dict) -> dict` | 根据下表执行，成功返回草稿 ID、完整记忆记录或被删除的 ID | 陈尧武给内部命令带上服务端 `request_id`。显式保存按来源回合与动作去重，确认按草稿去重，更新校验 `expected_revision`。事务及唯一键防并发重复写 |

`ApprovedReply` 示例。`intent` 取 `listen / reflect / clarify / offer_option / practice`。`voice_style` 取 `neutral / gentle / practice / check_in`。`memory_draft` 取 null 或含 `kind, text` 的候选。复核诊断、用药、无依据附和、编造记忆和忽略用户纠正等情形。审核发生在字幕与合成前。

```json
{"intent":"clarify","sentences":["你最想先练哪一句？"],"voice_style":"check_in","memory_draft":null}
```

| cmd.op | 额外输入 | 成功结果 |
| --- | --- | --- |
| `propose` | `kind, text, source_turn_id` | 生成 `draft_id`，仅作可编辑草稿 |
| `confirm` | 当前用户的 `draft_id`，可带编辑后的 `value` | 同一事务中消费草稿、生成已确认记录，返回包含 `memory_id, kind, text, revision` 的对象 |
| `save` | `kind, value, source_turn_id`。服务端从来源回合核实明确保存请求或获准的偏好配置 | 返回完整记录 `{memory_id, kind, text, revision}`。实际提交后截取前两项通知页面 |
| `update / delete` | 当前用户的 `memory_id`。更新另带 `value, expected_revision` | 更新返回完整新记录 `{memory_id, kind, text, revision}`，旧版本报冲突。删除返回被删 ID，两类操作立即失效对应缓存 |
| `discard_draft` | 当前用户的 `draft_id` | 丢弃待确认草稿。与确认竞争时只提交一个结果 |

`kind` 取 `preference / follow_up`。HTTP 请求中的 `text` 映射到内部 `value`，PATCH 的 `revision` 映射到 `expected_revision`。保存、确认、更新均返回完整的新记录，`revision` 从 1 开始递增。

Flask 在服务端按用户分区生成可重复的 `request_id`。聊天写入用 `(session_id,source_turn_id,op)`。确认或丢弃用 `(draft_id,op)`。PATCH 绑定 `(memory_id,op,expected_revision)`。DELETE 绑定 `(memory_id,op)`。每次先检查归属。聊天来源重放只写一次。同草稿同确认正文返回原记录，确认不同正文报 409。删除和丢弃成功对应 HTTP 204，重试也可返回 204，对已不可访问的资源返回 404。已确认后才丢弃、已丢弃后才确认均报状态冲突。PATCH 总是先校验版本，超时后客户端 GET 核对，携旧版本再改报 409。

服务端为正式输入 `I1` 分配回复回合 `R1`，记忆的 `source_turn_id=R1` 指回用户原话。欢迎回合没有对应用户输入。LLM 的自拟总结进入草稿。相机观察只用于当前交互，不能作为个人事实入库。**替身**用固定预审句和按用户隔离的内存字典。**交接**用真实 SQLite 验“记住，下次继续练答辩”→重开后召回→删除→重开后清除，并验证另一身份不能读写。

## 2. 陈尧武 → 全组

`app/` 拥有 HTTP、WebSocket、会话身份、输入与回合映射、后台任务和单连接发送队列。路由从服务端绑定取得用户身份。进入页面到发声的顺序如下。

1. 页面读取 bootstrap，装好输入及播放监听器后发 `session.ready`。服务端每个新会话只生成一次欢迎回合。浏览器自动播放受阻时先显示同一句，用户仍可立即输入。
2. `input.audio` 解包后交会话 ASR，键盘输入直接受理。每个正式输入只分配一次回合。临时转写辅助展示和话轮判定。麦克风接收循环在生成和播放期间持续运行。
3. 后台调用 `respond`，给审核短句分配 `sentence_id`。发送 `reply.text` 和有声模式的 `reply.audio` 前均检查取消状态。处理记忆请求时按来源输入去重，重发音频或重复回执不触发写库。
4. 好的视觉线索可参与留白、神态和场景决策。低质量、过期或关闭摄像头时沿用文本与语音决策。服务故障按网络协议输出，释放被取消或已断线的任务、队列和设备资源。

**替身**接固定转写、固定审核句、PCM 短样音及两条视觉观察。**交接**在主动欢迎尚未播完时插话，再投递一块旧音频。核对旧回合停声、闭口、迟到块丢弃。刷新后可恢复偏好，用户仍能打字继续。

## 3. 王皓 → 陈尧武、陈跃权

代码在 `wanghao/` 下（按人分目录，便于最后统一组装）。下表的导入路径不变：仓库根
`voice/`、`web/audio/` 是一行转发，`from voice.asr import open_stream` 与
`/web/audio/index.js` 照旧可用。

| 周末实现入口 | 参数、结果与使用方式 |
| --- | --- |
| `voice.asr.open_stream(session_id, on_transcript) -> stream` | 服务端每会话持有一份状态。`stream.feed(pcm: bytes, seq: int, t_ms: int)` 接收有序 16 kHz 单声道有符号 16 位小端裸 PCM，`close()` 清状态。回调 `on_transcript(input_id: str, text: str, is_final: bool)`，同一语音段共用一个 ID。语音 ID 由服务端生成，与键盘输入区分 |
| `voice.tts.synthesize(text, voice_style, cancelled) -> Iterator[tuple[bytes,int]]` | 参数为已审核单句、约定语调及取消回调。每项为裸 PCM 和实际采样率，同句采样率一致。陈尧武适配网络采样约定、分片编号、末块标志及 Base64。合成失败交集成层以已审核字幕继续 |
| `web/audio/` 提供 `startCapture({onAudio,onInterrupt,getActiveTurn}) -> Promise<stopCapture>` | 采集回调提供 `input.audio` 字段。`getActiveTurn()` 返回当前准备/播报回合或 null。确认插话触发 `onInterrupt(turnId)`。陈跃权在卸载或切设备时调用返回的 `stopCapture()`，王皓实现关闭麦克风轨道及采集节点 |
| `web/audio/` 提供 `createPlayer({onPlayback,onLevel}) -> player` | 页面装载时注册 `onPlayback(turnId,sentenceId,state,tMs)` 与 `onLevel(turnId,sentenceId,level,tMs)`，实际播放能量 0–1。页面调用 `player.queueAudio(frame)`、`player.stopTurn(turnId)`。陈跃权在页面卸载时调用 `player.dispose()`。王皓实现停声、清播放节点和队列、解除回调 |

有效插话在浏览器先停声音、清旧队列并通知闭口，再上报取消。每个迟到块先检查回合。ASR 中间结果不产生新的生成任务。ASR 故障保留键盘，TTS 故障或自动播放受阻转文字。音色候选三种各八句，定稿一位角色音色、四种语调，样本和版本交陈跃权联调。

**替身**让同一 ID 的两次临时转写和一次最终转写走真实回调，播放端用两块短样音，第二块标末块。**交接**在实际播放后打断再投递末块，捕获音频实际停止时间和口型归位时间。

## 4. 陈跃权 → 页面、音频与视觉

`web/` 提供 `mount(bootstrap, send)`、`applyEvent(event)`、`dispose()`。`send` 由页面传输层发送消息，`applyEvent` 消费已校验的网络事件。先装监听再发就绪，按权限并行拉起王皓的音频与王杰的观察器。页面 `dispose()` 由陈跃权实现，调用 `stopCapture()`、`player.dispose()` 与视觉观察器返回的 `stop()`，最后停止页面持有的相机轨道并解绑监听。

| 输入 | 页面执行 | 交给谁验 |
| --- | --- | --- |
| 初始化与 `turn.state` | 映射 `ready / listening / waiting / preparing / speaking / interrupted / closing`，可叠加七种表情。没有声音时用字幕完成开场 | 陈尧武验首次主动问候、重连去重 |
| `reply.text / reply.audio` | 按 `turn_id + sentence_id` 匹配字幕与音频。有声时从真正播放起显示字幕，静音时直接显示审核句并上报 `shown` | 王皓验嘴型、字幕与实际播放 |
| `turn.cancel` 或本地插话 | 立即 `stopTurn`、闭口、清该回合待播字幕及动画。重复取消结果一致 | 王皓验被打断的旧句 |
| `scene.set` | `default / quiet / practice` 同房间布光和环境声渐变，底声独立音轨，尊重用户的场景锁定及静音 | 王杰、陈尧武复看同一条件的结果 |
| `memory.propose / memory.saved` | 便签可编辑确认，已保存可撤回，走网络协议的记忆 HTTP 接口。失败保留可重试状态 | 施孔烨验改口、删除和刷新 |

**替身**将样例网络帧依次交 `applyEvent`，用可手动推进的播放时钟验证状态页。素材清单沿用 `assets/avatar/manifest.json`。常规工程验证用真实声音和角色资产。

## 5. 王杰 → 页面、Flask 和训练端

`vision/runtime/` 提供浏览器入口 `startVision(videoElement,onObservation) -> Promise<stop>`。页面拥有摄像头轨道，传入已获准的视频元素。`stop()` 停止观察，清理视觉窗口。页面统一释放轨道。回调提供观察字段，传输层补协议头和连续序号。使用浏览器单调时钟，从 10–15 FPS、0.5–1 秒窗口在展示机实测。以下是模块内回调值。

```json
{"t_ms":850,"event":"mouth_motion","quality":"good","confidence":0.82}
```

低质画面可给 `quality=low`。相机关闭或持续无脸报 `event=unavailable, quality=unavailable, confidence=0`。扩类推理有可用结果时发 `event=model_result`，同时提供 `model_version` 和非空 `labels`（每项 `label_id, score`）。未知结果不触发分类反馈。原始视频保留在浏览器本次观察窗口。仅有 Python 权重时先运行离线对照。王杰负责评估浏览器兼容产物和实测耗时，具备条件后接入。

**替身**先发上例，随后切不可用，验证页面和接话还能继续。**交接**用同一音频测试视觉可用、低质及关闭，记录抢话、错等、等待时长和展示所用模型版本。

## 6. 郭子毅 → 王杰

训练端交 `vision/training/predict.py`。王杰在训练负责人给出的环境按下例更换基线或候选清单。标准输出一条 UTF-8 JSON，日志写标准错误。缺文件、输入不符或加载失败以非零状态退出。正式文件随模块交付。

```sh
python -m vision.training.predict --manifest vision/training/bundle/manifest.json --input vision/training/samples/demo.jpg
```

每个模型包包含模型版本、版本化标签表、划分清单、权重或下载地址与 SHA-256、允许分发的固定输入与预期输出、测评结果及训练/推理命令。清单写明输入类型、裁剪、尺寸、通道顺序、归一化、阈值、标签顺序。视频另记帧率、采样和窗口。单标签表情与可共存动作分别登记判定方式。

下面是**可复制的推理替身**。王杰可以先用它处理未知类。正式验收再换实际权重与评测结果。

```json
{"schema_version":1,"model_version":"fixture-only","label_version":"fixture-v1","status":"unknown","labels":[]}
```

真实输出 `status=ok` 时 `labels` 非空，例如元素形状为 `{"label_id":"<标签表中的真实ID>","score":0.81}`，分值范围 0–1。保留原八类的 ID。每个新增 ID 登记判定依据、正负样例、标注单位及样本量。数据集名称和八类名称待核对原始标签表后填入。

下面是**标注／划分记录格式**，虚构 ID 用来展示字段。正式清单用原始标注和可核实的匿名主体 ID。同一人的所有图像、视频及增强样本固定在同一划分。

```json
{"annotation_version":"demo-format-v1","subject_id":"demo-person-01","sample_id":"demo-image-03","split":"train","unit":"image","label_ids":["example-original-id"]}
```

若原数据缺少可核实的主体 ID，记录限制，并对可辨认主体的补充样本单列评估。单图按图片标注。带时间标注的片段才评估起止。交付时报告相同保留人、相同样本上两版原八类的宏 F1、逐类精确率／召回率、支持量与混淆矩阵，新增类另报。王杰复跑两套权重并核对类别顺序和设备耗时。达到原八类及原功能基线、且有可复验改善，才采用候选版本。其余结果保留供课程汇报。
