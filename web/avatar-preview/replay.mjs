const stateExpressions = {
  ready: "neutral",
  listening: "attentive",
  waiting: "thoughtful",
  preparing: "thoughtful",
  speaking: "neutral",
  interrupted: "interrupted",
  closing: "relief",
};

export function createReplay() {
  return {
    turn: null,
    state: "ready",
    expression: "neutral",
    scene: "default",
    transitionMs: 0,
    playback: null,
    caption: "",
    notice: "",
    cancelled: new Set(),
    sentences: new Map(),
  };
}

function setState(view, state, expression = stateExpressions[state]) {
  view.state = state;
  view.expression = expression;
}

function sentenceKey(message) {
  return JSON.stringify([message.turn_id, message.sentence_id]);
}

function applyPlayback(view, message) {
  if (message.turn_id !== view.turn) return;
  const key = sentenceKey(message);
  if (message.state === "started") {
    view.playback = key;
    view.caption = view.sentences.get(key) ?? "";
    setState(view, "speaking");
  } else if (message.state === "shown") {
    view.caption = view.sentences.get(key) ?? "";
    setState(view, "listening");
  } else if (message.state === "ended" && view.playback === key) {
    view.playback = null;
    setState(view, "listening");
  }
}

export function applyFrame(view, frame) {
  const message = frame.message;
  const turn = message.turn_id;
  if (view.cancelled.has(turn)) {
    switch (message.type) {
      case "turn.cancel": return "already_cancelled";
      case "reply.text":
      case "reply.audio":
      case "scene.set": return "drop_cancelled";
      case "turn.state": return message.state === "interrupted" ? "handle" : "drop_cancelled";
      case "reply.playback": return message.state === "cancelled" ? "handle" : "drop_cancelled";
      default: return "handle";
    }
  }

  switch (message.type) {
    case "turn.state":
      if (message.state !== "speaking" || (view.turn === turn && view.playback)) {
        view.turn = turn;
        setState(view, message.state, message.expression);
      }
      break;
    case "reply.text":
      view.sentences.set(sentenceKey(message), message.text);
      break;
    case "reply.playback":
      applyPlayback(view, message);
      break;
    case "turn.cancel":
      view.cancelled.add(turn);
      if (view.turn === turn) {
        view.playback = null;
        view.caption = "";
        setState(view, "interrupted");
      }
      break;
    case "scene.set":
      view.scene = message.scene_id;
      view.transitionMs = message.transition_ms;
      break;
    case "input.text":
    case "transcript":
      if (!view.playback) setState(view, "listening");
      view.notice = "";
      break;
    case "session.error":
      view.notice = message.message;
      break;
  }
  return "handle";
}
