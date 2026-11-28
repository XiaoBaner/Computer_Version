export function bindDevices(bootstrap, session, { startCapture, startVision }) {
  const microphone = document.querySelector("#microphone");
  const camera = document.querySelector("#camera");
  const status = document.querySelector("#device-status");
  const video = document.querySelector("#observation-video");
  const listeners = new AbortController();
  const canCapture = bootstrap.features.asr && Boolean(startCapture);
  const canObserve = bootstrap.features.vision && Boolean(startVision) && Boolean(navigator.mediaDevices);
  let microphoneRequest = 0;
  let cameraRequest = 0;
  let stopCapture;
  let stopVision;
  let cameraStream;
  let unavailable = false;
  let audioSeq = 0;
  let visionSeq = 0;
  let reason;

  function updateStatus() {
    status.textContent = reason ?? [
      stopCapture ? "麦克风已开启" : "可用键盘输入",
      stopVision ? "摄像头已开启" : canObserve ? "摄像头已关闭" : "摄像头不可用",
    ].join(" · ");
  }

  function observe(event) {
    if (event.event === "unavailable" && unavailable) return;
    if (session.emit("vision.event", { ...event, seq: visionSeq++ })) {
      unavailable = event.event === "unavailable";
    }
  }

  function cameraUnavailable() {
    observe({ t_ms: Math.round(performance.now()), event: "unavailable", quality: "unavailable", confidence: 0 });
  }

  function stopCamera() {
    stopVision?.();
    stopVision = undefined;
    cameraStream?.getTracks().forEach((track) => track.stop());
    cameraStream = undefined;
    video.srcObject = null;
  }

  document.querySelector("#microphone-option").hidden = !canCapture;
  document.querySelector("#camera-option").hidden = !canObserve;
  microphone.checked = camera.checked = false;
  updateStatus();
  microphone.addEventListener("change", async () => {
    const request = ++microphoneRequest;
    stopCapture?.();
    stopCapture = undefined;
    reason = undefined;
    if (microphone.checked) {
      try {
        const stop = await startCapture({
          onAudio: (frame) => {
            if (request === microphoneRequest) session.emit("input.audio", { ...frame, seq: audioSeq++ });
          },
          onInterrupt: (turn) => {
            if (request === microphoneRequest) session.interrupt("barge_in", turn);
          },
          getActiveTurn: session.getActiveTurn,
        });
        if (request !== microphoneRequest) return stop();
        stopCapture = stop;
      } catch {
        if (request !== microphoneRequest) return;
        microphone.checked = false;
        reason = "麦克风不可用，可继续键盘输入。";
      }
    }
    updateStatus();
  }, { signal: listeners.signal });
  camera.addEventListener("change", async () => {
    const request = ++cameraRequest;
    stopCamera();
    reason = undefined;
    if (!camera.checked) {
      cameraUnavailable();
      updateStatus();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true });
      if (request !== cameraRequest) return stream.getTracks().forEach((track) => track.stop());
      cameraStream = stream;
      video.srcObject = stream;
      await video.play();
      if (request !== cameraRequest) return;
      const stop = await startVision(video, observe);
      if (request !== cameraRequest) return stop();
      stopVision = stop;
      for (const track of stream.getVideoTracks()) {
        track.addEventListener("ended", () => {
          if (request !== cameraRequest) return;
          camera.checked = false;
          stopCamera();
          cameraUnavailable();
          updateStatus();
        }, { signal: listeners.signal });
      }
    } catch {
      if (request !== cameraRequest) return;
      camera.checked = false;
      stopCamera();
      cameraUnavailable();
      reason = "摄像头不可用，仍可继续对话。";
    }
    updateStatus();
  }, { signal: listeners.signal });
  return {
    ready() {
      if (!camera.checked) cameraUnavailable();
    },
    dispose() {
      microphoneRequest += 1;
      cameraRequest += 1;
      listeners.abort();
      stopCapture?.();
      stopCamera();
    },
  };
}
