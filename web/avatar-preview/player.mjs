const BUFFER_SECONDS = 0.035;
const STOP_PROBE_SECONDS = 0.012;

function decodePcm(context, { data, sample_rate: rate }) {
  const bytes = atob(data);
  if (!bytes.length || bytes.length % 2 || ![16000, 24000].includes(rate)) {
    throw new Error("音频块格式无效");
  }
  const audio = context.createBuffer(1, bytes.length / 2, rate);
  const samples = audio.getChannelData(0);
  for (let index = 0; index < samples.length; index += 1) {
    const value = bytes.charCodeAt(index * 2) + bytes.charCodeAt(index * 2 + 1) * 256;
    samples[index] = (value >= 32768 ? value - 65536 : value) / 32768;
  }
  return audio;
}

export function createPlayer({ onPlayback, onLevel, onError, onTiming = () => {} }) {
  let context;
  let meter;
  let meterReady;
  let interval;
  let active;
  let disposed = false;
  const stopped = new Set();

  function outputClock() {
    const time = performance.now();
    const stamp = context.getOutputTimestamp?.();
    if (stamp?.performanceTime > 0) {
      return {
        at: stamp.contextTime + Math.max(0, time - stamp.performanceTime) / 1000,
        toWall: (seconds) => stamp.performanceTime + (seconds - stamp.contextTime) * 1000,
        device: true,
      };
    }
    const at = context.currentTime - context.baseLatency - (context.outputLatency ?? 0);
    return { at, toWall: (seconds) => time + (seconds - at) * 1000, device: false };
  }

  function onSamples({ data: block }) {
    if (active && block.observedTo > active.startAt) {
      if (block.firstSignal !== null && block.firstSignal >= active.startAt) {
        active.firstSignal ??= block.firstSignal;
        active.lastSignal = block.lastSignal;
      }
      active.levels.push(block);
    }

    for (const stop of stopped) {
      if (block.observedTo <= stop.requestAt) continue;
      const nextSpeech = active && block.observedTo > active.startAt;
      if (block.lastSignal !== null && !nextSpeech) {
        stop.lastSignal = block.lastSignal;
      }
      const silence = block.observedTo - Math.max(stop.requestAt, stop.lastSignal ?? 0) >= STOP_PROBE_SECONDS;
      if (!silence && !nextSpeech && block.observedTo < stop.requestAt + 0.3) continue;
      stopped.delete(stop);
      const clock = outputClock();
      const recentSignal = stop.lastSignal !== null && stop.lastSignal >= stop.requestAt - 0.070;
      const stopMs = clock.toWall(recentSignal ? stop.lastSignal : stop.requestAt);
      onTiming({
        type: "audio-stopped",
        turnId: stop.turn,
        sentenceId: stop.sentence,
        tMs: stopMs,
        measured: clock.device && recentSignal && silence && !nextSpeech,
      });
      onPlayback(stop.turn, stop.sentence, "cancelled", stopMs);
    }
  }

  function tick() {
    const sentence = active;
    if (!sentence) return;
    const clock = outputClock();
    if (sentence.firstSignal !== null && !sentence.started && clock.at >= sentence.firstSignal) {
      sentence.started = true;
      onPlayback(sentence.turn, sentence.sentence, "started", clock.toWall(sentence.firstSignal));
    }
    if (active !== sentence) return;
    let level;
    while (sentence.levels.length && sentence.levels[0].observedTo <= clock.at) {
      level = sentence.levels.shift().level;
    }
    if (sentence.started && level !== undefined) {
      onLevel(sentence.turn, sentence.sentence, level, performance.now());
    }
    if (sentence.final && clock.at >= sentence.endAt + 0.015) {
      active = null;
      if (sentence.started) {
        onPlayback(sentence.turn, sentence.sentence, "ended", clock.toWall(sentence.endAt));
      } else {
        onError(sentence.turn, sentence.sentence, "语音没有可播放的声音");
      }
    }
  }

  async function enable() {
    if (disposed) throw new Error("播放器已关闭");
    if (!context) {
      context = new AudioContext({ latencyHint: "interactive" });
      meterReady = context.audioWorklet.addModule(new URL("./speech-meter.mjs", import.meta.url));
    }
    await Promise.all([context.resume(), meterReady]);
    if (disposed || context.state !== "running") throw new Error("浏览器未启用声音");
    if (!meter) {
      meter = new AudioWorkletNode(context, "avatar-speech-meter", { outputChannelCount: [1] });
      meter.port.onmessage = onSamples;
      meter.connect(context.destination);
      context.addEventListener("statechange", () => {
        if (context.state === "running" || !active) return;
        const { turn, sentence } = active;
        stopSentence(turn, sentence);
        onError(turn, sentence, "声音播放已暂停，已显示字幕。");
      });
      interval = setInterval(tick, 16);
    }
  }

  function queueAudio(frame) {
    if (context?.state !== "running" || !meter) throw new Error("浏览器未启用声音");
    const buffer = decodePcm(context, frame);
    const next = context.currentTime + BUFFER_SECONDS;
    active ??= {
      turn: frame.turn_id, sentence: frame.sentence_id, startAt: next, endAt: next,
      sources: new Set(), levels: [], firstSignal: null, lastSignal: null, started: false,
    };
    if (active.turn !== frame.turn_id || active.sentence !== frame.sentence_id) {
      throw new Error("前一句语音仍在播放");
    }
    const sentence = active;
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(meter);
    sentence.sources.add(source);
    source.onended = () => {
      sentence.sources.delete(source);
      source.disconnect();
    };
    const startAt = sentence.endAt > context.currentTime ? sentence.endAt : next;
    source.start(startAt);
    sentence.endAt = startAt + buffer.duration;
    sentence.final = frame.is_last;
  }

  function stopSentence(turn, sentence) {
    if (!active || active.turn !== turn || (sentence !== undefined && active.sentence !== sentence)) return;
    const item = active;
    active = null;
    if (item.started) {
      stopped.add({
        turn, sentence: item.sentence, requestAt: context.currentTime, lastSignal: item.lastSignal,
      });
    }
    for (const source of item.sources) source.stop();
  }

  return {
    enable,
    queueAudio,
    stopSentence,
    stopTurn: stopSentence,
    dispose() {
      disposed = true;
      if (active) stopSentence(active.turn);
      stopped.clear();
      clearInterval(interval);
      meter?.disconnect();
      context?.close().catch(() => {});
    },
  };
}
