import { applyFrame, createReplay } from "./replay.mjs";
import { mountScene } from "./scene.mjs";
import { createConnection } from "./connection.mjs";
import { bindDevices } from "./devices.mjs";
import { createLivePanel } from "./live-panel.mjs";
import { mount as mountSession } from "./live-session.mjs";

const manifestUrl = new URL("/assets/avatar/manifest.json", location.href);
const sceneUrl = new URL("/assets/scene/manifest.json", location.href);
const labels = {
  state: {
    ready: "准备开聊", listening: "倾听", waiting: "留白", preparing: "准备回应",
    speaking: "回应", interrupted: "被打断", closing: "收尾",
  },
  expression: {
    neutral: "自然", attentive: "专注", concern: "克制关切", thoughtful: "思考",
    clarify: "澄清", relief: "轻微舒展", interrupted: "收住",
  },
  eyelid: { open: "睁眼", half: "半闭", closed: "闭眼" },
  mouth: { closed: "闭口", slightly_open: "微开", wide: "宽口", round: "圆口", pressed: "抿唇" },
  scene: { default: "默认", quiet: "安静", practice: "练习" },
  anchor: {
    head: "头部", left_eye: "左眼", right_eye: "右眼",
    left_mouth_corner: "左嘴角", right_mouth_corner: "右嘴角",
  },
};

const controls = {
  state: document.querySelector("#state"),
  expression: document.querySelector("#expression"),
  eyelid: document.querySelector("#eyelid"),
  mouth: document.querySelector("#mouth"),
  scene: document.querySelector("#scene"),
};
const scenario = document.querySelector("#scenario");
const anchors = document.querySelector("#anchors");
const coordinates = document.querySelector("#anchor-coordinates");
const reference = document.querySelector("#reference");
const sheet = document.querySelector("#expression-sheet");
const manualControls = document.querySelector("#manual-controls");
const replayControls = document.querySelector("#replay-controls");
const play = document.querySelector("#play");
const step = document.querySelector("#step");
const reset = document.querySelector("#reset");
const caption = document.querySelector("#caption");
const notice = document.querySelector("#notice");
const progress = document.querySelector("#progress");
const trace = document.querySelector("#trace");
const stage = document.querySelector("#stage");
const stageStatus = document.querySelector("#stage-status");
const assetsStatus = document.querySelector("#assets-status");
const canvas = document.querySelector("#avatar");
const context = canvas.getContext("2d");
const images = new Map();

let manifest;
let cases;
let sceneView;
let view = createReplay();
let cursor = 0;
let mismatches = 0;
let timer = null;
let liveMode = document.body.dataset.mode === "live" || new URLSearchParams(location.search).get("live") === "1";
let liveView = { ...createReplay(), mouth: "closed" };
let livePanel;
let liveSession;
let connection;
let devices;
let lastDrawing;

async function readJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`读取 ${new URL(url, location.href).pathname} 失败（HTTP ${response.status}）`);
  return response.json();
}

async function loadAssets() {
  const paths = new Set([
    ...Object.values(manifest.base_layers),
    ...Object.values(manifest.eyelids),
    ...Object.values(manifest.mouths),
  ]);
  for (const { reference: image, layers, eyelids = {}, mouths = {} } of Object.values(manifest.expressions)) {
    paths.add(image);
    for (const layer of [layers, eyelids, mouths]) {
      for (const path of Object.values(layer)) paths.add(path);
    }
  }
  paths.delete(null);
  if (!paths.size) {
    assetsStatus.textContent = "素材占位 · 可检查锚点与状态";
    return;
  }

  const problems = [];
  await Promise.all([...paths].map(async (path) => {
    try {
      const url = new URL(path, manifestUrl);
      if (url.origin !== location.origin || !url.pathname.startsWith("/assets/avatar/")) {
        throw new Error("请使用角色目录里的素材");
      }
      const image = new Image();
      image.src = url.href;
      await image.decode();
      if (image.naturalWidth !== canvas.width || image.naturalHeight !== canvas.height) {
        throw new Error(`画布为 ${image.naturalWidth}×${image.naturalHeight}`);
      }
      images.set(path, image);
    } catch (error) {
      problems.push(`${path}（${error.message}）`);
    }
  }));

  const loaded = `已载入 ${images.size} 张素材`;
  assetsStatus.textContent = problems.length ? `${loaded} · 检查 ${problems.join("、")}` : loaded;
}

function ellipse(x, y, rx, ry, color) {
  context.beginPath();
  context.ellipse(x, y, rx, ry, 0, 0, 2 * Math.PI);
  context.fillStyle = color;
  context.fill();
}

function line(x1, y1, x2, y2) {
  context.beginPath();
  context.moveTo(x1, y1);
  context.lineTo(x2, y2);
  context.stroke();
}

function drawPlaceholder(pose) {
  const { head, left_eye, right_eye, left_mouth_corner, right_mouth_corner } = manifest.anchors;
  const unit = canvas.width / 480;
  ellipse(head.x, head.y + 218 * unit, 138 * unit, 112 * unit, "#739488");
  ellipse(head.x, head.y + 82 * unit, 21 * unit, 35 * unit, "#ddbea0");
  ellipse(head.x, head.y - 10 * unit, 102 * unit, 122 * unit, "#3b544f");
  ellipse(head.x, head.y + 2 * unit, 87 * unit, 104 * unit, "#efd5b9");
  ellipse(head.x, head.y - 88 * unit, 76 * unit, 29 * unit, "#3b544f");

  const browOffsets = {
    neutral: [0, 0], attentive: [-5, -5], concern: [3, 3], thoughtful: [4, -2],
    clarify: [0, -7], relief: [-2, -2], interrupted: [2, 2],
  };
  context.strokeStyle = "#45534e";
  context.lineWidth = 3 * unit;
  context.lineCap = "round";
  [left_eye, right_eye].forEach((eye, index) => {
    const brow = eye.y + (browOffsets[pose.expression][index] - 19) * unit;
    line(eye.x - 13 * unit, brow, eye.x + 13 * unit, brow);
    const height = { open: 5, half: 2.5, closed: 1.1 }[pose.eyelid];
    ellipse(eye.x, eye.y, 10 * unit, height * unit, "#33423d");
  });

  const x = (left_mouth_corner.x + right_mouth_corner.x) / 2;
  const y = (left_mouth_corner.y + right_mouth_corner.y) / 2;
  const halfWidth = Math.abs(right_mouth_corner.x - left_mouth_corner.x) / 2 || 15 * unit;
  const mouthShape = {
    closed: [0.72, 1.5], slightly_open: [0.65, 4], wide: [1, 6.5],
    round: [0.36, 8], pressed: [0.42, 2.4],
  }[pose.mouth];
  ellipse(x, y, halfWidth * mouthShape[0], mouthShape[1] * unit, "#784a47");
}

function drawAvatar(pose) {
  context.clearRect(0, 0, canvas.width, canvas.height);
  const expression = manifest.expressions[pose.expression];
  const referenceImage = reference.checked && images.get(expression.reference);
  if (referenceImage) {
    context.drawImage(referenceImage, 0, 0);
  } else {
    const layers = { ...manifest.base_layers, ...expression.layers };
    if (!images.has(layers.head)) drawPlaceholder(pose);
    if (pose.eyelid !== "open") delete layers.pupils;
    const eyelid = expression.eyelids?.[pose.eyelid] ?? manifest.eyelids[pose.eyelid];
    const mouth = expression.mouths?.[pose.mouth] ?? manifest.mouths[pose.mouth];
    const paths = [...Object.values(layers), eyelid, mouth];
    for (const path of paths) {
      const layer = images.get(path);
      if (layer) context.drawImage(layer, 0, 0);
    }
  }

  if (!anchors.checked) return;
  const unit = canvas.width / 480;
  for (const { x, y } of Object.values(manifest.anchors)) {
    ellipse(x, y, 4 * unit, 4 * unit, "#fff5d8");
    ellipse(x, y, 2 * unit, 2 * unit, "#276351");
  }
}

function render() {
  const example = !liveMode && cases.get(scenario.value);
  const displayed = liveMode ? liveView : view;
  const pose = liveMode || example
    ? {
        ...displayed, eyelid: "open",
        mouth: liveMode ? liveView.mouth : displayed.playback ? "slightly_open" : "closed",
      }
    : Object.fromEntries(Object.entries(controls).map(([key, control]) => [key, control.value]));

  manualControls.hidden = liveMode || Boolean(example);
  replayControls.hidden = !example;
  coordinates.hidden = !anchors.checked;
  const previousMouth = stage.dataset.mouth;
  stage.dataset.turn = pose.turn ?? "";
  for (const key of Object.keys(controls)) stage.dataset[key] = pose[key];
  const sceneDuration = liveMode || example ? displayed.transitionMs : 240;
  stage.style.transitionDuration = `${sceneDuration}ms`;
  stage.style.setProperty("--scene-transition", `${sceneDuration}ms`);
  sceneView?.update(pose.scene, sceneDuration);
  stageStatus.textContent = [
    labels.state[pose.state], labels.expression[pose.expression], labels.scene[pose.scene],
  ].join(" · ");
  caption.textContent = displayed.caption || "等待字幕";
  notice.hidden = !displayed.notice;
  notice.textContent = displayed.notice;
  const drawing = [pose.expression, pose.eyelid, pose.mouth, anchors.checked, reference.checked, images.size].join("/");
  if (drawing !== lastDrawing) {
    drawAvatar(pose);
    lastDrawing = drawing;
  }
  if (liveMode && pose.state === "interrupted" && pose.mouth === "closed"
    && previousMouth && previousMouth !== "closed") {
    requestAnimationFrame((frameStartMs) => {
      requestAnimationFrame((tMs) => livePanel.timing({ type: "mouth-closed", turnId: pose.turn, frameStartMs, tMs }));
    });
  }

  if (example) {
    const total = example.frames.length;
    progress.textContent = mismatches
      ? `${cursor} / ${total} 帧 · ${mismatches} 帧结果不符`
      : `${cursor} / ${total} 帧${cursor === total ? " · 全部符合样例" : ""}`;
    step.disabled = timer !== null || cursor === total;
    play.textContent = timer !== null ? "暂停" : cursor === total ? "重新回放" : "自动回放";
  }
}

function setupLivePanel() {
  if (livePanel) return;
  document.querySelector("#live-controls").hidden = false;
  document.querySelector("#offline-mode").hidden = true;
  document.querySelector("#reference-option").hidden = true;
  anchors.checked = reference.checked = false;
  livePanel = createLivePanel({
    submit(value) {
      if (connection) return connection.submit(value);
      const text = value.trim();
      if (!text || !liveSession) return false;
      liveSession.interrupt("input_text");
      return liveSession.emit("input.text", { input_id: crypto.randomUUID(), text });
    },
    interrupt: () => liveSession?.interrupt(),
    reconnect: () => connection?.open(),
    setVoice: (value) => liveSession?.setVoice(value),
    setSceneLock: (value) => liveSession?.setSceneLock(value),
  });
}

export function mount(bootstrap, send, adapters = {}) {
  devices?.dispose();
  liveSession?.dispose();
  pause();
  liveMode = true;
  setupLivePanel();
  livePanel.setup(bootstrap);
  liveView = { ...createReplay(), scene: bootstrap.scene_id, mouth: "closed" };
  liveSession = mountSession(bootstrap, send, {
    createPlayer: adapters.createPlayer,
    onView(next) {
      liveView = next;
      if (!connection) livePanel.connection(next.connected ? "online" : "offline");
      livePanel.view(next, liveSession.getActiveTurn());
      if (next.connected) devices.ready();
      render();
    },
    onEvent: (event, result) => livePanel.event("收", event, result),
    onTiming: (mark) => livePanel.timing(mark),
  });
  devices = bindDevices(bootstrap, liveSession, adapters);
  render();
  return liveSession;
}

export function applyEvent(event) {
  liveSession?.applyEvent(event);
}

export function dispose() {
  connection?.dispose();
  connection = undefined;
  devices?.dispose();
  liveSession?.dispose();
  const ambient = document.querySelector("#ambient");
  ambient.checked = false;
  ambient.dispatchEvent(new Event("change"));
}

function pause() {
  clearInterval(timer);
  timer = null;
}

function restart() {
  pause();
  view = createReplay();
  cursor = 0;
  mismatches = 0;
  trace.replaceChildren();
  render();
}

function stepFrame() {
  const example = cases.get(scenario.value);
  const frame = example.frames[cursor];
  if (!frame) return;
  const result = applyFrame(view, frame);
  const row = document.createElement("li");
  const message = frame.message;
  const parts = [frame.from, message.type, message.turn_id, message.state, result].filter(Boolean);
  row.textContent = parts.join(" · ");
  if (result !== frame.expect) {
    row.className = "mismatch";
    row.textContent += `（预期 ${frame.expect}）`;
    mismatches += 1;
  }
  trace.append(row);
  trace.scrollTop = trace.scrollHeight;
  cursor += 1;
  if (cursor === example.frames.length) pause();
  render();
}

async function initialize() {
  const [catalog, examples, assets, scenes] = await Promise.all([
    readJson("/contracts/events.v1.json"),
    readJson("/contracts/examples.v1.json"),
    readJson(manifestUrl),
    readJson(sceneUrl),
  ]);
  if (liveMode) setupLivePanel();
  manifest = assets;
  cases = new Map(examples.map((example) => [example.id, example]));
  sceneView = mountScene({
    room: stage.querySelector(".stage-room"),
    toggle: document.querySelector("#ambient"),
    status: document.querySelector("#ambient-status"),
    config: scenes,
    manifestUrl: sceneUrl,
  });
  if (manifest.expression_sheet) {
    sheet.href = new URL(manifest.expression_sheet, manifestUrl).href;
    sheet.hidden = false;
  }
  canvas.width = manifest.canvas.width;
  canvas.height = manifest.canvas.height;
  for (const [name, { x, y }] of Object.entries(manifest.anchors)) {
    const row = document.createElement("div");
    const term = document.createElement("dt");
    const value = document.createElement("dd");
    term.textContent = labels.anchor[name] ?? name;
    value.textContent = `(${x}, ${y})`;
    row.append(term, value);
    coordinates.append(row);
  }

  const values = {
    state: catalog.types.turn_state.enum,
    expression: catalog.types.expression.enum,
    eyelid: Object.keys(manifest.eyelids),
    mouth: Object.keys(manifest.mouths),
    scene: catalog.types.scene.enum,
  };
  for (const [name, control] of Object.entries(controls)) {
    for (const value of values[name]) control.add(new Option(`${labels[name][value]} · ${value}`, value));
    control.addEventListener("change", render);
    control.disabled = false;
  }
  for (const example of cases.values()) scenario.add(new Option(example.title, example.id));
  scenario.addEventListener("change", restart);
  scenario.disabled = false;
  anchors.addEventListener("change", render);
  reference.addEventListener("change", render);
  step.addEventListener("click", stepFrame);
  reset.addEventListener("click", restart);
  play.addEventListener("click", () => {
    if (timer !== null) {
      pause();
      render();
      return;
    }
    if (cursor === cases.get(scenario.value).frames.length) restart();
    timer = setInterval(stepFrame, 380);
    stepFrame();
  });
  render();
  await loadAssets();
  render();
  if (liveMode) {
    connection = createConnection({
      catalog, mount,
      onStatus: (state, message) => livePanel.connection(state, message),
      onSend: (event) => livePanel.event("发", event),
      onUnconfirmed: (texts) => livePanel.recoverDraft(texts),
    });
    connection.open();
    window.addEventListener("pagehide", dispose, { once: true });
  }
}

try {
  await initialize();
} catch (error) {
  assetsStatus.textContent = `预览加载失败 · ${error.message}`;
  console.error(error);
}
