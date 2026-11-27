// web/audio —— 王皓这一侧对陈跃权暴露的两个入口。
//
//   startCapture({onAudio, onInterrupt, getActiveTurn}) -> Promise<stopCapture>
//   createPlayer({onPlayback, onLevel}) -> player
//
// 这一层只负责“麦克风 ↔ 协议的 input.audio”和“协议的 reply.audio ↔ 扬声器”。
// 它不碰 WebSocket：帧发出去、收进来由陈尧武的传输层做。字段名与
// docs/weekend-wire.md 第 3 节一致。
//
// 两条约定值得写在这里：
//   * 时戳一律用 clock.js 的 nowMs()，页面初始化起算、重连不清零；
//   * 播放回执用“真实声音”的时刻——started 是第一个采样真正出声，
//     ended 是最后一块真的播完，不是收到数据的时刻。

import { nowMs } from "./clock.js";
import {
  Framer,
  Resampler,
  SAMPLE_RATE_IN,
  base64ToBytes,
  bytesToBase64,
  float32ToInt16,
  int16ToFloat32,
  rmsOf,
} from "./pcm.js";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ------------------------------------------------------------------ 采集 */

/**
 * 打开麦克风并持续产出 input.audio 帧。
 *
 * @param {object}   opts
 * @param {Function} opts.onAudio       每 20 ms 调一次，参数是 {seq,t_ms,sample_rate,codec,data}
 * @param {Function} opts.onInterrupt   确认插话时调一次，参数是 turnId
 * @param {Function} opts.getActiveTurn 返回当前准备/播报的 turnId，没有则 null
 * @param {object}   [opts.vad]         {threshold, holdMs, cooldownMs}
 * @returns {Promise<Function>}         stopCapture()：关麦、停节点、释放 AudioContext
 */
export async function startCapture({ onAudio, onInterrupt, getActiveTurn, vad } = {}) {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    throw new Error("这个浏览器不支持麦克风采集（需要 https 或 localhost）");
  }
  const cfg = vad || {};
  const threshold = cfg.threshold ?? 0.02; // 均方根阈值
  const holdMs = cfg.holdMs ?? 60; // 连续超过阈值多久算“开口”
  const cooldownMs = cfg.cooldownMs ?? 700; // 两次插话回调之间的最短间隔

  // 回声消除开着：笔记本外放时，自己的声音会从麦克风回来，
  // 不做这一步会把自己播的话判成人插话。
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });

  const Ctx = window.AudioContext || window.webkitAudioContext;
  const ctx = new Ctx();
  if (ctx.state === "suspended") await ctx.resume();

  const source = ctx.createMediaStreamSource(stream);
  const resampler = new Resampler(ctx.sampleRate, SAMPLE_RATE_IN);
  const framer = new Framer();

  let seq = 0;
  let closed = false;
  let loudMs = 0;
  let lastInterruptAt = -Infinity;

  function handleBlock(block) {
    if (closed) return;
    const resampled = resampler.process(block);
    for (const frame of framer.push(resampled)) {
      // 插话判定：只在确实有回合在进行时才算，否则用户正常说话会被误报。
      const level = rmsOf(frame);
      loudMs = level > threshold ? loudMs + 20 : 0;
      if (loudMs >= holdMs) {
        const t = nowMs();
        if (t - lastInterruptAt > cooldownMs) {
          const turnId = typeof getActiveTurn === "function" ? getActiveTurn() : null;
          if (turnId && typeof onInterrupt === "function") {
            lastInterruptAt = t;
            onInterrupt(turnId);
          }
        }
        loudMs = 0;
      }

      if (typeof onAudio === "function") {
        const i16 = float32ToInt16(frame);
        onAudio({
          seq: seq++,
          t_ms: nowMs(),
          sample_rate: SAMPLE_RATE_IN,
          codec: "pcm_s16le",
          data: bytesToBase64(new Uint8Array(i16.buffer)),
        });
      }
    }
  }

  let node;
  let silent;
  if (ctx.audioWorklet) {
    await ctx.audioWorklet.addModule(new URL("./capture-worklet.js", import.meta.url));
    node = new AudioWorkletNode(ctx, "capture-forwarder", {
      numberOfInputs: 1,
      numberOfOutputs: 0,
    });
    node.port.onmessage = (e) => handleBlock(e.data);
  } else {
    // 老浏览器兜底。ScriptProcessor 只有接到 destination 才会被驱动，
    // 所以串一个静音增益节点出去。
    node = ctx.createScriptProcessor(1024, 1, 1);
    node.onaudioprocess = (e) => {
      const ch = e.inputBuffer.getChannelData(0);
      handleBlock(new Float32Array(ch));
    };
    silent = ctx.createGain();
    silent.gain.value = 0;
    node.connect(silent);
    silent.connect(ctx.destination);
  }
  source.connect(node);

  let stopped = false;
  async function stopCapture() {
    if (stopped) return;
    stopped = true;
    closed = true;
    try {
      source.disconnect();
    } catch {}
    try {
      node.disconnect();
      if (node.port) node.port.onmessage = null;
    } catch {}
    try {
      silent?.disconnect();
    } catch {}
    for (const track of stream.getTracks()) track.stop();
    try {
      await ctx.close();
    } catch {}
    // 尾巴补成完整一块，接收端按 320 样本切时不会错位。
    const tail = framer.flush();
    if (tail && typeof onAudio === "function") {
      const i16 = float32ToInt16(tail);
      onAudio({
        seq: seq++,
        t_ms: nowMs(),
        sample_rate: SAMPLE_RATE_IN,
        codec: "pcm_s16le",
        data: bytesToBase64(new Uint8Array(i16.buffer)),
      });
    }
  }

  return stopCapture;
}

/* ------------------------------------------------------------------ 播放 */

/**
 * 下行音频播放器。
 *
 * @param {object}   opts
 * @param {Function} opts.onPlayback (turnId, sentenceId, state, tMs)  state: started|ended|cancelled
 * @param {Function} opts.onLevel    (turnId, sentenceId, level, tMs)  0..1 实际播放能量
 */
export function createPlayer({ onPlayback = () => {}, onLevel = () => {} } = {}) {
  let ctx = null;
  let master = null;
  let analyser = null;
  let levelTimer = 0;
  let nextTime = 0;

  /** 每句话的状态：同一 turn+sentence 的所有块共用。 */
  const sentences = new Map(); // key -> {turnId, sentenceId, started, ended, cancelled, sources:Set, timers:Set, last:null}
  /** 播放时间线，用来把分析器的能量归到具体句子上。 */
  const timeline = []; // {start, end, key, turnId, sentenceId}

  const keyOf = (t, s) => `${t}|${s}`;

  function ensureContext() {
    if (ctx) return ctx;
    const Ctx = window.AudioContext || window.webkitAudioContext;
    ctx = new Ctx({ latencyHint: "interactive" });
    master = ctx.createGain();
    master.gain.value = 1;
    analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.6;
    master.connect(analyser);
    analyser.connect(ctx.destination);
    return ctx;
  }

  /** 必须在用户手势里调用一次（点“开始”、按按钮），否则浏览器不让出声。 */
  async function unlock() {
    const c = ensureContext();
    if (c.state === "suspended") await c.resume();
    return c.state;
  }

  function sentence(key, turnId, sentenceId) {
    let s = sentences.get(key);
    if (!s) {
      s = {
        turnId,
        sentenceId,
        started: false,
        ended: false,
        cancelled: false,
        sources: new Set(),
        timers: new Set(),
      };
      sentences.set(key, s);
    }
    return s;
  }

  function later(seconds, fn) {
    const id = setTimeout(fn, Math.max(0, seconds * 1000));
    return id;
  }

  /** 收一块 reply.audio。字段名与协议一致。 */
  function queueAudio(frame) {
    if (!frame || frame.codec !== "pcm_s16le") return;
    const turnId = frame.turn_id;
    const sentenceId = frame.sentence_id;
    if (!turnId || !sentenceId) return;

    const c = ensureContext();
    if (c.state === "suspended") c.resume().catch(() => {});

    const bytes = base64ToBytes(frame.data);
    const samples = Math.floor(bytes.byteLength / 2);
    const i16 = new Int16Array(bytes.buffer, bytes.byteOffset, samples);
    const f32 = int16ToFloat32(i16);
    const rate = frame.sample_rate || 24000;

    const buffer = c.createBuffer(1, f32.length, rate);
    buffer.copyToChannel(f32, 0);

    const key = keyOf(turnId, sentenceId);
    const s = sentence(key, turnId, sentenceId);
    if (s.cancelled) return; // 已经停掉的句子，迟到的块直接丢

    const src = c.createBufferSource();
    src.buffer = buffer;
    src.connect(master);

    // 连续排期：同一句的块接在上一块后面，不同句之间也顺次排，避免重叠。
    const start = Math.max(c.currentTime + 0.06, nextTime);
    src.start(start);
    nextTime = start + buffer.duration;

    s.sources.add(src);
    timeline.push({ start, end: nextTime, key, turnId, sentenceId });

    // started：按真实出声时刻报，不是收到块的时刻。
    if (!s.started) {
      s.started = true;
      const id = later(start - c.currentTime, () => {
        if (!s.cancelled) onPlayback(turnId, sentenceId, "started", nowMs());
      });
      s.timers.add(id);
    }

    if (frame.is_last) {
      src.onended = () => {
        s.sources.delete(src);
        if (s.cancelled || s.ended) return;
        s.sources.clear(); // 同一句的其余块也都结束了，别让集合一直攥着
        s.ended = true;
        onPlayback(turnId, sentenceId, "ended", nowMs());
        prune();
      };
    }

    startLevelLoop();
    return src;
  }

  /** 打断：停声、清该回合所有待播块，报一次 cancelled。 */
  function stopTurn(turnId) {
    const now = nowMs();
    for (const [key, s] of sentences) {
      if (s.turnId !== turnId) continue;
      if (s.cancelled || s.ended) continue;
      s.cancelled = true;
      for (const t of s.timers) clearTimeout(t);
      s.timers.clear();
      for (const src of s.sources) {
        try {
          src.onended = null;
          src.stop();
        } catch {}
        try {
          src.disconnect();
        } catch {}
      }
      s.sources.clear();
      if (s.started) onPlayback(turnId, s.sentenceId, "cancelled", now);
    }
    // 这一回合的音频已经从时间线上撤掉，重排后续排期。
    for (let i = timeline.length - 1; i >= 0; i--) {
      if (timeline[i].turnId === turnId && !sentences.get(timeline[i].key)?.ended) {
        timeline.splice(i, 1);
      }
    }
    if (ctx) nextTime = ctx.currentTime;
    if (!timeline.length) stopLevelLoop();
  }

  function stopAll() {
    for (const turnId of new Set([...sentences.values()].map((s) => s.turnId))) stopTurn(turnId);
  }

  function prune() {
    if (!ctx) return;
    const t = ctx.currentTime;
    for (let i = timeline.length - 1; i >= 0; i--) {
      if (timeline[i].end < t - 0.5) timeline.splice(i, 1);
    }
    for (const [key, s] of sentences) {
      if (s.ended || s.cancelled) {
        if (!s.sources.size && !s.timers.size) sentences.delete(key);
      }
    }
    if (!timeline.length) stopLevelLoop();
  }

  function startLevelLoop() {
    if (levelTimer || !analyser) return;
    const buf = new Float32Array(analyser.fftSize);
    const tick = () => {
      if (!ctx) return;
      analyser.getFloatTimeDomainData(buf);
      const level = Math.min(1, rmsOf(buf) * 3); // 语音正常音量大约落在 0.2–0.6
      const t = ctx.currentTime;
      const seg = timeline.find((x) => t >= x.start && t < x.end);
      if (seg) onLevel(seg.turnId, seg.sentenceId, level, nowMs());
      levelTimer = requestAnimationFrame(tick);
    };
    levelTimer = requestAnimationFrame(tick);
  }

  function stopLevelLoop() {
    if (levelTimer) cancelAnimationFrame(levelTimer);
    levelTimer = 0;
  }

  /** 页面卸载时调。停声、清队列、解除回调、关 AudioContext。 */
  async function dispose() {
    stopAll();
    stopLevelLoop();
    for (const s of sentences.values()) for (const t of s.timers) clearTimeout(t);
    sentences.clear();
    timeline.length = 0;
    try {
      if (ctx && ctx.state !== "closed") await ctx.close();
    } catch {}
    ctx = null;
    master = null;
    analyser = null;
  }

  return { queueAudio, stopTurn, stopAll, dispose, unlock, get currentTime() { return ctx ? ctx.currentTime : 0; } };
}

export { nowMs };
export { SAMPLE_RATE_IN, FRAME_SAMPLES_IN } from "./pcm.js";
