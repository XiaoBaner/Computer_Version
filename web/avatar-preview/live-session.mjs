import { createPlayer as createBrowserPlayer } from "./player.mjs";
import { applyFrame, createReplay } from "./replay.mjs";

const keyOf = (turn, sentence) => JSON.stringify([turn, sentence]);
const playbackTimeout = 3000;

export function mount(bootstrap, send, {
  createPlayer = createBrowserPlayer,
  onView = () => {},
  onEvent = () => {},
  onTiming = () => {},
} = {}) {
  const view = createReplay();
  const queue = new Map();
  const receivedText = new Set();
  const spoken = new Set();
  const receipts = new Set();
  let audioOutput = false;
  let connected = false;
  let disposed = false;
  let voiceRequest = 0;
  let timeout;
  let mouth = "closed";
  let transcript = "";
  let sceneBase = bootstrap.scene_id;
  let sceneTurn;
  let sceneLocked = false;
  let speakingExpression;
  view.scene = sceneBase;

  const player = createPlayer({ onPlayback, onLevel, onError: fallback, onTiming });
  const head = () => queue.values().next().value;
  const publish = () => onView({ ...view, mouth, transcript, connected });
  const reduce = (message) => applyFrame(view, { message });
  const message = (type, fields) => ({ ...fields, v: 1, type, session_id: bootstrap.session_id });
  const emit = (type, fields) => connected && !disposed && send(message(type, fields)) !== false;

  function receipt(turn, sentence, state, time = performance.now()) {
    const token = JSON.stringify([turn, sentence, state]);
    if (receipts.has(token)) return;
    receipts.add(token);
    emit("reply.playback", { turn_id: turn, sentence_id: sentence, state, t_ms: Math.max(0, Math.round(time)) });
  }

  function complete(entry, state, time) {
    queue.delete(entry.key);
    clearTimeout(timeout);
    mouth = "closed";
    reduce(message("reply.playback", { turn_id: entry.turn, sentence_id: entry.sentence, state }));
    view.playback = null;
    view.sentences.delete(entry.key);
    publish();
    receipt(entry.turn, entry.sentence, state, time);
    pump();
  }

  function onPlayback(turn, sentence, state, time) {
    if (disposed) return;
    const key = keyOf(turn, sentence);
    if (state === "cancelled") {
      if (spoken.delete(key)) receipt(turn, sentence, state, time);
      return;
    }
    const entry = head();
    if (entry?.key !== key || view.cancelled.has(turn)) return;
    if (state === "started") {
      spoken.add(key);
      mouth = "slightly_open";
      reduce(message("reply.playback", { turn_id: turn, sentence_id: sentence, state }));
      if (speakingExpression) view.expression = speakingExpression;
      publish();
      receipt(turn, sentence, state, time);
    } else if (state === "ended") {
      spoken.delete(key);
      complete(entry, state, time);
    }
  }

  function onLevel(turn, sentence, level) {
    if (disposed || view.playback !== keyOf(turn, sentence)) return;
    const next = level >= 0.48 ? "wide" : level >= 0.035 ? "slightly_open" : "closed";
    if (mouth === next) return;
    mouth = next;
    publish();
  }

  function fallback(turn, sentence, reason) {
    const entry = queue.get(keyOf(turn, sentence));
    if (!entry) return;
    entry.fallback = reason;
    if (entry === head()) pump();
  }

  function pump() {
    clearTimeout(timeout);
    if (!connected || disposed) return;
    const entry = head();
    if (!entry) return;
    if (!entry.voice || entry.fallback) {
      if (entry.fallback) {
        view.notice = entry.fallback;
        if (player.stopSentence) player.stopSentence(entry.turn, entry.sentence);
        else player.stopTurn(entry.turn);
      }
      complete(entry, "shown");
      return;
    }
    try {
      while (head() === entry && entry.frames.length) player.queueAudio(entry.frames.shift());
    } catch {
      fallback(entry.turn, entry.sentence, "语音播放失败，已显示字幕。");
      return;
    }
    if (head() === entry && !entry.final) {
      timeout = setTimeout(
        () => fallback(entry.turn, entry.sentence, "语音等待超时，已显示字幕。"),
        playbackTimeout,
      );
    }
  }

  function cancelTurn(turn, reason, notify) {
    if (!turn || view.cancelled.has(turn)) return "already_cancelled";
    const result = reduce(message("turn.cancel", { turn_id: turn, reason }));
    const changedHead = head()?.turn === turn;
    for (const [key, entry] of queue) {
      if (entry.turn !== turn) continue;
      queue.delete(key);
      view.sentences.delete(key);
    }
    if (view.turn === turn) mouth = "closed";
    if (sceneTurn === turn) {
      sceneTurn = undefined;
      if (!sceneLocked) view.scene = sceneBase;
    }
    player.stopTurn(turn);
    publish();
    if (notify) emit("turn.cancel", { turn_id: turn, reason });
    if (changedHead) pump();
    return result;
  }

  function applyEvent(event) {
    if (disposed || !connected || event.v !== 1 || event.session_id !== bootstrap.session_id) return;
    if (event.type === "turn.cancel") {
      const result = cancelTurn(event.turn_id, event.reason, false);
      onEvent(event, result);
      return;
    }
    const turn = event.turn_id;
    const key = keyOf(turn, event.sentence_id);
    if (view.cancelled.has(turn) && event.type === "session.error") {
      onEvent(event, "drop_cancelled");
      return;
    }
    if (event.type === "scene.set" && sceneLocked) {
      if (!turn) sceneBase = event.scene_id;
      onEvent(event, "scene_locked");
      return;
    }
    if (event.type === "turn.state" && !view.cancelled.has(turn)) {
      if (event.state === "speaking") {
        if (turn === view.turn) speakingExpression = event.expression;
      } else if (event.state !== "interrupted" && turn !== view.turn) {
        if (head()?.turn === view.turn) cancelTurn(view.turn, "next_turn", false);
        speakingExpression = undefined;
        view.notice = "";
      }
    }
    if (event.type === "reply.text" && receivedText.has(key)) return;
    const result = reduce(event);
    onEvent(event, result);
    if (result !== "handle") return;

    switch (event.type) {
      case "reply.text": {
        receivedText.add(key);
        const wasEmpty = queue.size === 0;
        queue.set(key, {
          key, turn, sentence: event.sentence_id, voice: audioOutput, nextSeq: 0, frames: [],
        });
        if (wasEmpty) pump();
        break;
      }
      case "reply.audio": {
        const entry = queue.get(key);
        if (!entry?.voice || entry.fallback || entry.final || event.seq < entry.nextSeq) break;
        if (event.seq !== entry.nextSeq || (entry.rate && entry.rate !== event.sample_rate)) {
          fallback(turn, event.sentence_id, "语音块不完整，已显示字幕。");
          break;
        }
        entry.nextSeq += 1;
        entry.rate = event.sample_rate;
        entry.final = event.is_last;
        entry.frames.push(event);
        if (entry === head()) pump();
        break;
      }
      case "session.error":
        if (["TTS_UNAVAILABLE", "AUDIO_GAP"].includes(event.code) && event.sentence_id) {
          fallback(turn, event.sentence_id, event.message);
        }
        break;
      case "transcript":
        transcript = event.text;
        break;
      case "scene.set":
        sceneTurn = turn;
        if (!turn) sceneBase = event.scene_id;
        break;
    }
    publish();
  }

  function interrupt(reason = "barge_in", turn = getActiveTurn()) {
    if (turn === view.turn) cancelTurn(turn, reason, true);
  }

  function getActiveTurn() {
    return head()?.turn ?? (view.state === "preparing" ? view.turn : null);
  }

  function disconnect() {
    connected = false;
    for (const turn of new Set([view.turn, ...Array.from(queue.values(), (entry) => entry.turn)])) {
      cancelTurn(turn, "connection_closed", false);
    }
    view.state = "ready";
    view.expression = "neutral";
    publish();
  }

  return {
    applyEvent, disconnect, interrupt, getActiveTurn, emit,
    ready() {
      connected = true;
      emit("session.ready", { audio_output: audioOutput });
      publish();
    },
    async setVoice(value) {
      if (value && !bootstrap.features.tts) return false;
      const request = ++voiceRequest;
      if (value) {
        try {
          await player.enable?.();
        } catch {
          if (disposed || request !== voiceRequest) return audioOutput;
          view.notice = "浏览器暂时无法播放语音，已启用字幕。";
          publish();
          return false;
        }
      }
      if (disposed || request !== voiceRequest) return audioOutput;
      audioOutput = value;
      emit("session.ready", { audio_output: audioOutput });
      if (!value && head()?.voice) {
        for (const entry of queue.values()) if (entry.voice) entry.fallback = "对白已静音，已显示字幕。";
        pump();
      }
      return audioOutput;
    },
    setSceneLock(value) { sceneLocked = value; },
    dispose() {
      if (disposed) return;
      disposed = true;
      clearTimeout(timeout);
      player.dispose();
    },
  };
}
