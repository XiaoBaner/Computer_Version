# 实时接入与本机验收

## 页面接入

页面在同源 Flask 下调用 `GET /api/session/bootstrap`，再用会话 Cookie 连接 `/ws/session`。收到审核后的 `reply.text` 时按回合和句子顺序排队；有声状态由播放器起播回调启动字幕和口型，音频按照消息标注的 16 kHz 或 24 kHz 采样率播放。文本模式发送 `shown` 回执。切断时清除该回合的音频和字幕，服务端迟到的音频块按回合 ID 丢弃。断线重连沿用会话，重发尚未确认的文本。

小组集成时由 Flask 提供 bootstrap、同源 WebSocket 和静态素材。使用已有的 HTML 结构，保持 `data-mode="preview"`，由团队的传输层导入 `preview.mjs`，调用 `mount(bootstrap, send, { createPlayer, startCapture, startVision })`。在连接打开后调用返回对象的 `ready()`，服务端已校验事件传给 `applyEvent(event)`，断线时调用返回对象的 `disconnect()`。页面卸载时调用 `dispose()`。

播放器可沿用本地的 Web Audio 实现，也可注入小组的 `createPlayer({onPlayback,onLevel})`。后者按约定提供 `queueAudio(frame)`、`stopTurn(turnId)` 和 `dispose()`。接入可选的 `startCapture` 与 `startVision` 可开启麦克风和摄像头。若要在时间轴上复测小组播放器，播放器还需上报实际音频输出的停止时戳，格式可参考本地 `player.mjs` 的 `onTiming`。

## 本机结果

2026 年 11 月 26 日在当前 Windows 工作机运行隔离的 Microsoft Edge 158.0.4370.33。Flask-Sock 经实际 WebSocket 发送 24 kHz 单声道 PCM，测试台词由 eSpeak NG 中文声线生成。三次打断由网页按钮触发。[原始浏览器读数](live-verification.json)

| 用例 | 结果 |
| --- | --- |
| 正常开口并播放完成 | 2 次，通过 `started` 和 `ended` 回执验证 |
| 播报中打断 | 3 次，均闭口并丢弃迟到的旧音频 |
| 按对话切换场景 | 1 次，默认切换到练习。灯光和环境声音量持续过渡 650 ms |
| 静音与摄像头不可用 | 开场与后续文字均以字幕显示，键盘对话继续 |
| 重连 | 复用原会话，问候仅收到一次，重连后可以继续输入 |

| 打断回合 | 停声与闭口时差上界 | 丢弃的迟到音频块 |
| --- | ---: | ---: |
| R3 | 44.4 ms | 88 |
| R4 | 41.6 ms | 88 |
| R5 | 45.0 ms | 88 |

三次上界都达到 ≤ 120 ms 的目标。音频端使用 AudioWorklet 观测实际语音输出流中的最后一个有效样本，并经浏览器输出时钟换算为页面时间。口型端由闭口重绘后的两个动画帧界定，表中的时差采用该帧区间的上界。[Web Audio 输出时钟](https://www.w3.org/TR/webaudio/#dom-audiocontext-getoutputtimestamp)

正式联调需要陈尧武的 Flask 入口、王皓的播放器与收音模块，并在指定演示电脑上测自然插话和声画同步。这份记录覆盖本机功能和时序验证。
