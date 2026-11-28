export function createLivePanel({ submit, interrupt, reconnect, setVoice, setSceneLock }) {
  const input = document.querySelector("#conversation-input");
  const send = document.querySelector("#send-text");
  const stop = document.querySelector("#interrupt");
  const retry = document.querySelector("#reconnect");
  const speech = document.querySelector("#speech-output");
  const lock = document.querySelector("#scene-lock");
  const status = document.querySelector("#connection-status");
  const transcript = document.querySelector("#transcript");
  const trace = document.querySelector("#live-trace");
  const latency = document.querySelector("#timing-output");
  const timings = new Map();
  let rowNumber = 0;
  let tts = false;
  let online = false;
  let voiceRequest = 0;

  document.querySelector("#conversation-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (submit(input.value)) input.value = "";
  });
  stop.addEventListener("click", interrupt);
  retry.addEventListener("click", reconnect);
  speech.addEventListener("change", async () => {
    const request = ++voiceRequest;
    speech.disabled = true;
    const enabled = await setVoice(speech.checked);
    if (request !== voiceRequest) return;
    speech.checked = enabled;
    speech.disabled = !tts || !online;
  });
  lock.addEventListener("change", () => setSceneLock(lock.checked));

  function append(data, label) {
    const line = document.createElement("li");
    Object.assign(line.dataset, data, { row: ++rowNumber });
    line.textContent = label;
    trace.append(line);
    if (trace.children.length > 180) trace.firstElementChild.remove();
  }

  return {
    setup(bootstrap) {
      tts = Boolean(bootstrap.features.tts);
      speech.checked = lock.checked = false;
      voiceRequest += 1;
      timings.clear();
    },
    connection(state, message) {
      online = state === "online";
      status.dataset.state = state;
      status.textContent = message ?? (online ? "会话已连接" : "正在连接会话");
      retry.hidden = state !== "offline";
      send.disabled = !online;
      speech.disabled = !online || !tts;
    },
    view(view, activeTurn) {
      transcript.textContent = view.transcript ? "你的输入 · " + view.transcript : "";
      transcript.hidden = !view.transcript;
      stop.disabled = !view.connected || !activeTurn;
    },
    recoverDraft(texts) {
      if (texts.length) input.value = [...texts, input.value].filter(Boolean).join(" ").slice(0, input.maxLength);
    },
    event(direction, { type, turn_id, sentence_id, state, scene_id, audio_output, t_ms }, result) {
      if (type === "reply.audio" && result === "handle") return;
      const name = [direction, type, turn_id, state ?? scene_id, result].filter(Boolean).join(" · ");
      append({
        direction, type, turn: turn_id ?? "", sentence: sentence_id ?? "", state: state ?? "",
        scene: scene_id ?? "", result: result ?? "", audio: audio_output ?? "", ms: t_ms ?? "",
      }, name);
    },
    timing(mark) {
      const record = timings.get(mark.turnId) ?? {};
      record[mark.type] = mark;
      timings.set(mark.turnId, record);
      const audio = record["audio-stopped"];
      const mouth = record["mouth-closed"];
      if (!audio || !mouth) return;
      timings.delete(mark.turnId);
      const delta = Math.max(Math.abs(audio.tMs - mouth.frameStartMs), Math.abs(audio.tMs - mouth.tMs));
      const gap = Math.round(delta * 10) / 10;
      const accepted = audio.measured && gap <= 120;
      latency.value = audio.measured ? `停声与闭口时差上界 ${gap} ms` : `停声与闭口估算 ${gap} ms`;
      append({
        type: "stop-timing", turn: mark.turnId, gapMs: gap, audioStopMs: audio.tMs,
        mouthFirstFrameMs: mouth.frameStartMs, mouthFrameMs: mouth.tMs,
        measured: audio.measured, accepted,
      }, latency.value);
    },
  };
}
