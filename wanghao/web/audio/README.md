# web/audio · 采集与播放

王皓这一侧交给陈跃权（页面）和陈尧武（传输层）的两个入口。协议字段见
[周末联调文档](../../../docs/weekend-wire.md) 第 3 节，接口约定见
[六人模块接口](../../../docs/weekend-modules.md) 第 3 节。仓库根的 `web/audio/`
是一行转发，页面照旧 `import ... from "/web/audio/index.js"`。

```
web/audio/
  index.js            startCapture / createPlayer
  pcm.js              base64、Int16↔Float32、流式重采样、20ms 切块（不碰 DOM）
  capture-worklet.js  AudioWorklet：只把麦克风的块转给主线程
  clock.js            页面单调时钟（t_ms 的来源，重连不清零）
  selftest.html       自测页：麦克风电平、按协议喂音频、打断
  smoke.html          无头冒烟测试：pcm 往返、重采样、切块、player 接口
```

## 给页面用的两个入口

```js
import { startCapture, createPlayer } from "/web/audio/index.js";

const stopCapture = await startCapture({
  onAudio(frame) { send({ v: 1, type: "input.audio", session_id, ...frame }); },
  onInterrupt(turnId) { player.stopTurn(turnId); closeMouth(); sendCancel(turnId); },
  getActiveTurn: () => ui.activeTurn,          // 准备/播报中的 turn_id，否则 null
  vad: { threshold: 0.02, holdMs: 60, cooldownMs: 700 },
});

const player = createPlayer({
  onPlayback: (turnId, sentenceId, state, tMs) => ui.applyPlayback(turnId, sentenceId, state, tMs),
  onLevel:    (turnId, sentenceId, level, tMs) => mouth.setLevel(level),
});

player.queueAudio(event);   // 收到 reply.audio 原样传入
player.stopTurn(turnId);    // turn.cancel 或本地插话
await player.dispose();     // 页面卸载
```

页面 `dispose()` 的顺序按模块文档：先 `stopCapture()`，再 `player.dispose()`，
最后停相机、解绑监听。

## 帧字段（这层负责填的部分）

**上行 `input.audio`** —— 每个 20 ms 块一帧：

| 字段 | 值 |
| --- | --- |
| `seq` | 从 0 递增，重连延续；本模块负责 |
| `t_ms` | `clock.js` 的页面单调时钟 |
| `sample_rate` | 固定 `16000` |
| `codec` | `pcm_s16le` |
| `data` | 320 样本 = 640 字节的 base64，不含 WAV 头 |

**下行 `reply.audio`** —— 这层消费：按 `turn_id + sentence_id` 归队，
`sample_rate` 支持 16000 / 24000，同一句固定；`is_last` 标记末块。

## 回执的语义

| 回执 | 什么时候发 |
| --- | --- |
| `started` | **第一个采样真正出声**的时刻（按排期时间计算，不是收到数据的时刻） |
| `ended` | 末块真的播完（`AudioBufferSourceNode.onended`） |
| `cancelled` | `stopTurn` 时该句已经开播，报一次；重复取消不会重复报 |
| `shown` | 不属于这层——文字模式下由页面在展示字幕时上报 |

`onLevel` 用 `AnalyserNode` 量的是**真实输出能量**，0–1，按 `requestAnimationFrame`
约 60 次/秒上报，并按播放时间线归到当前句，供口型使用。

## 自测

```bash
python3 -m http.server 8000     # 在仓库根目录
# 浏览器打开 http://localhost:8000/web/audio/selftest.html
```

页面上：① 打开麦克风看电平与 `input.audio` 帧；② 选一段 `assets/voice/final`
里的音频，按 20 ms 一块喂给播放器，看 `started / ended` 与口型电平；
③ 播放中点「播放中打断」，验证 `cancelled` 与停声。

## 已知限制

- **自动播放策略**：`AudioContext` 需要在用户手势里恢复。页面在第一次点击时调用
  `player.unlock()`（`queueAudio` 也会尝试 `resume`，但静默失败时仍要靠手势）。
- **回声**：采集开了 `echoCancellation / noiseSuppression / autoGainControl`。
  外放音量很大时仍可能误判插话，`vad.threshold` 可以按机器调；`cooldownMs`
  防止连续误报。
- **AudioWorklet** 需要 secure context（localhost 或 https）；否则自动退到
  `ScriptProcessorNode`，功能一致、延迟略高。
- 浏览器把 `AudioContext` 采样率定在 48 kHz 左右，重采样在这里做，不依赖设备设置。
- 这一层不做重连、不做 `AUDIO_GAP` 判定——那是传输层与 ASR 的事。
