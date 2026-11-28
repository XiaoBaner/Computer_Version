/** Run the live demo scenarios in an isolated Chromium or Edge browser (Node 22+). */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, readFile, realpath, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { isAbsolute, join, relative } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";

const url = process.argv[2] ?? "http://127.0.0.1:5052";
const browser = process.argv[3] ?? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
assert.equal(new URL(url).hostname, "127.0.0.1", "Use a loopback demo URL");

async function waitFor(check, label, timeout = 10000) {
  const deadline = performance.now() + timeout;
  while (performance.now() < deadline) {
    const value = await check();
    if (value) return value;
    await sleep(24);
  }
  throw new Error("Timed out waiting for " + label);
}

async function openCdp(endpoint) {
  const ws = new WebSocket(endpoint);
  await once(ws, "open");
  let sequence = 0;
  const pending = new Map();
  ws.addEventListener("message", ({ data }) => {
    const packet = JSON.parse(data);
    const callback = pending.get(packet.id);
    if (callback) callback(packet);
  });
  return {
    call(method, params = {}) {
      return new Promise((resolve, reject) => {
        const id = ++sequence;
        const timer = setTimeout(() => {
          pending.delete(id);
          reject(new Error("DevTools request timed out: " + method));
        }, 5000);
        pending.set(id, ({ result, error }) => {
          clearTimeout(timer);
          pending.delete(id);
          if (error) reject(new Error(error.message));
          else resolve(result);
        });
        ws.send(JSON.stringify({ id, method, params }));
      });
    },
    close: () => ws.close(),
  };
}

// Store live media element/gain handles and the WebSocket session ID from the browser.
function observePage() {
  window.testTracks = [];
  window.testReady = [];
  window.testGreetings = 0;
  window.testFinalAudio = new Set();
  const createSource = AudioContext.prototype.createMediaElementSource;
  AudioContext.prototype.createMediaElementSource = function (media) {
    const source = createSource.call(this, media);
    const connect = source.connect.bind(source);
    source.connect = (...args) => {
      window.testTracks.push({ media, gain: args[0].gain });
      return connect(...args);
    };
    return source;
  };
  const NativeSocket = window.WebSocket;
  window.WebSocket = class extends NativeSocket {
    constructor(...args) {
      super(...args);
      window.testSocket = this;
      this.addEventListener("message", ({ data }) => {
        const event = JSON.parse(data);
        if (event.type === "reply.text" && event.turn_id === "R0") window.testGreetings += 1;
        if (event.type === "reply.audio" && event.is_last) window.testFinalAudio.add(event.turn_id);
      });
    }
    send(payload) {
      const event = JSON.parse(payload);
      if (event.type === "session.ready") window.testReady.push(event);
      super.send(payload);
    }
  };
}

const temporaryRoot = await realpath(tmpdir());
const profile = await mkdtemp(join(temporaryRoot, "avatar-edge-"));
const child = spawn(browser, [
  "--headless=new", "--no-first-run", "--no-default-browser-check", "--disable-background-networking",
  "--disable-extensions", "--disable-background-timer-throttling", "--window-size=1200,900",
  "--remote-debugging-port=0", "--user-data-dir=" + profile, "about:blank",
], { windowsHide: true, stdio: "ignore" });
let startupError;
child.on("error", (error) => { startupError = error; });
let cdp;

try {
  const port = await waitFor(async () => {
    if (startupError) throw startupError;
    if (child.exitCode !== null) throw new Error("The browser exited before starting DevTools");
    try {
      const data = await readFile(join(profile, "DevToolsActivePort"), "utf8");
      return data.split(/\r?\n/)[0];
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
      return null;
    }
  }, "DevTools");
  const targets = await (await fetch("http://127.0.0.1:" + port + "/json/list")).json();
  cdp = await openCdp(targets.find((target) => target.type === "page").webSocketDebuggerUrl);
  await cdp.call("Page.enable");
  await cdp.call("Page.addScriptToEvaluateOnNewDocument", { source: "(" + observePage + ")()" });
  const version = await cdp.call("Browser.getVersion");
  await cdp.call("Page.navigate", { url });

  async function run(fn, ...args) {
    const expression = "(" + fn + ")(" + args.map((argument) => JSON.stringify(argument)).join(",") + ")";
    const reply = await cdp.call("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (reply.exceptionDetails) {
      throw new Error(reply.exceptionDetails.exception?.description ?? reply.exceptionDetails.text);
    }
    return reply.result.value;
  }

  function trace(type, state = "", turn = "") {
    return run(
      (name, mode, id) => [...document.querySelectorAll("#live-trace li")]
        .map(({ dataset }) => ({ ...dataset }))
        .filter((line) => line.type === name && (!mode || line.state === mode) && (!id || line.turn === id)),
      type, state, turn,
    );
  }

  function stage() {
    return run(() => ({ ...document.querySelector("#stage").dataset }));
  }

  async function click(selector) {
    const point = await run((value) => {
      const control = document.querySelector(value);
      control.scrollIntoView({ block: "center" });
      const box = control.getBoundingClientRect();
      return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
    }, selector);
    await cdp.call("Input.dispatchMouseEvent", { ...point, type: "mousePressed", button: "left", clickCount: 1 });
    await cdp.call("Input.dispatchMouseEvent", { ...point, type: "mouseReleased", button: "left", clickCount: 1 });
  }

  async function submit(text) {
    const previous = (await stage()).turn;
    await run((value) => {
      document.querySelector("#conversation-input").value = value;
      document.querySelector("#conversation-form").requestSubmit();
    }, text);
    return waitFor(async () => {
      const pose = await stage();
      return pose.turn && pose.turn !== previous && pose.turn;
    }, "a new turn");
  }

  function tracks() {
    return run(() => Object.fromEntries(window.testTracks.map(({ media, gain }) => [
      new URL(media.src).pathname.split("/").at(-1).replace(".wav", ""),
      { elapsed: media.currentTime, paused: media.paused, gain: gain.value },
    ])));
  }

  const report = { browser: version.product, platform: process.platform, normal: [], interruptions: [] };
  await waitFor(() => run(() => document.querySelector("#connection-status")?.dataset.state === "online"), "WebSocket");
  await waitFor(async () => (await trace("reply.playback", "shown", "R0")).length === 1, "the silent greeting");
  assert.match(await run(() => document.querySelector("#caption").textContent), /你好/);
  assert.equal((await trace("vision.event")).length, 1);
  report.silentGreeting = true;
  report.cameraUnavailable = true;

  await click("#speech-output");
  await waitFor(async () => (await trace("session.ready")).some((line) => line.audio === "true"), "voice enabled");

  for (const text of ["我想聊聊今天的安排", "我想练习怎么开场"]) {
    const practice = text.includes("练习");
    if (practice) {
      await click("#ambient");
      await waitFor(async () => {
        const audio = (await tracks()).default;
        return audio && !audio.paused && audio.elapsed > 0.1 && audio.gain > 0.001;
      }, "default room ambience");
    }
    const turn = await submit(text);
    const start = await waitFor(async () => (await trace("reply.playback", "started", turn))[0], "played speech");
    assert.match(await run(() => document.querySelector("#caption").textContent), practice ? /练一句/ : /我在听/);
    if (practice) {
      const transition = await waitFor(async () => {
        const { default: before, practice: after } = await tracks();
        const opacity = await run(() => Number(getComputedStyle(document.querySelector('[data-light="practice"]')).opacity));
        return opacity > 0 && opacity < 1 && before?.gain > 0 && after?.gain > 0
          ? { opacity, beforeGain: before.gain, afterGain: after.gain }
          : null;
      }, "room lighting and ambience crossfade", 2500);
      await waitFor(async () => {
        const { default: before, practice: after } = await tracks();
        return before.gain < 0.0001 && after.gain > 0 && after.elapsed > 0.1;
      }, "practice room ambience");
      assert.equal((await stage()).scene, "practice");
      report.scene = { from: "default", to: "practice", durationMs: 650, observedDuringFade: transition };
      await click("#ambient");
    }
    const end = await waitFor(async () => (await trace("reply.playback", "ended", turn))[0], "the completed sentence");
    assert.equal((await stage()).mouth, "closed");
    assert.equal((await stage()).state, "listening");
    assert.ok(Number(end.ms) > Number(start.ms));
    report.normal.push({ turn, startMs: Number(start.ms), endMs: Number(end.ms) });
  }

  for (let sample = 0; sample < 3; sample += 1) {
    const turn = await submit("打断实测");
    await waitFor(async () => {
      const pose = await stage();
      return pose.turn === turn && pose.mouth !== "closed";
    }, "an open mouth during real PCM output");
    await click("#interrupt");
    const measurement = await waitFor(
      async () => (await trace("stop-timing", "", turn))[0],
      "a speech-meter and painted-frame reading",
    );
    assert.equal(measurement.measured, "true", "Use a recent signal from the browser output clock");
    assert.ok(Number(measurement.gapMs) <= 120, "Stop-to-mouth time exceeds 120 ms");
    assert.equal((await stage()).mouth, "closed");
    assert.equal((await stage()).state, "interrupted");
    await waitFor(() => run((id) => window.testFinalAudio.has(id), turn), "the last late PCM frame");
    const late = await waitFor(async () =>
      (await trace("reply.audio", "", turn)).filter((line) => line.result === "drop_cancelled").length,
    "discarded late PCM");
    assert.equal((await trace("reply.playback", "started", turn)).length, 1);
    assert.equal((await trace("reply.playback", "cancelled", turn)).length, 1);
    report.interruptions.push({
      turn, source: "interrupt_button", gapUpperMs: Number(measurement.gapMs),
      audioStopMs: Number(measurement.audioStopMs), mouthFirstFrameMs: Number(measurement.mouthFirstFrameMs),
      mouthFrameMs: Number(measurement.mouthFrameMs), droppedLateFrames: late,
    });
  }

  await click("#speech-output");
  const silent = await submit("我用文字继续");
  await waitFor(async () => (await trace("reply.playback", "shown", silent)).length === 1, "a muted reply");
  assert.equal((await stage()).mouth, "closed");
  assert.equal((await trace("reply.playback", "started", silent)).length, 0);
  report.mutedReply = true;

  const beforeReconnect = await run(() => window.testReady.length);
  await run(() => window.testSocket.close());
  await waitFor(() => run((count) => window.testReady.length > count, beforeReconnect), "session reconnect");
  assert.equal(await run(() => window.testReady[0].session_id === window.testReady.at(-1).session_id), true);
  const continued = await submit("继续对话");
  await waitFor(async () => (await trace("reply.playback", "shown", continued)).length === 1, "text after reconnect");
  assert.equal(await run(() => window.testGreetings), 1);
  report.reconnectWithoutRepeatedGreeting = true;

  console.log(JSON.stringify(report, null, 2));
} finally {
  if (cdp) {
    await cdp.call("Browser.close").catch(() => {});
    cdp.close();
  }
  if (child.exitCode === null) {
    await Promise.race([once(child, "exit"), sleep(800)]);
    if (child.exitCode === null) child.kill();
  }
  const inside = relative(temporaryRoot, await realpath(profile));
  if (!inside || inside.startsWith("..") || isAbsolute(inside)) throw new Error("Unexpected test profile path");
  await rm(profile, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
}
