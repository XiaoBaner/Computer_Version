export function mountScene({ room, toggle, status, config, manifestUrl }) {
  const url = (path) => new URL(path, manifestUrl).href;
  room.style.backgroundImage = [config.window, config.room].map((path) => "url(" + url(path) + ")").join(", ");
  for (const [name, preset] of Object.entries(config.presets)) {
    const overlay = document.createElement("div");
    overlay.className = "stage-light";
    overlay.dataset.light = name;
    overlay.style.backgroundImage = "url(" + url(preset.lighting) + ")";
    room.append(overlay);
  }

  const tracks = new Map();
  let context;
  let selected = "default";
  let pauseTimer;
  let soundRequest = 0;

  function fade(duration) {
    if (!context) return;
    const now = context.currentTime;
    for (const [name, { gain }] of tracks) {
      const target = toggle.checked && name === selected ? config.presets[name].gain : 0;
      const current = gain.gain.value;
      gain.gain.cancelScheduledValues(now);
      gain.gain.setValueAtTime(current, now);
      gain.gain.linearRampToValueAtTime(target, now + Math.max(duration, 20) / 1000);
    }
  }

  async function start() {
    if (!context) {
      context = new AudioContext();
      for (const [name, preset] of Object.entries(config.presets)) {
        const audio = new Audio(url(preset.ambient));
        audio.loop = true;
        const gain = context.createGain();
        gain.gain.value = 0;
        context.createMediaElementSource(audio).connect(gain).connect(context.destination);
        tracks.set(name, { audio, gain });
      }
    }
    await Promise.all([context.resume(), ...[...tracks.values()].map(({ audio }) => audio.play())]);
  }

  toggle.addEventListener("change", async () => {
    const request = ++soundRequest;
    clearTimeout(pauseTimer);
    status.hidden = true;
    if (!toggle.checked) {
      fade(160);
      pauseTimer = setTimeout(() => {
        for (const { audio } of tracks.values()) audio.pause();
      }, 185);
      return;
    }
    try {
      await start();
      if (request === soundRequest) fade(240);
    } catch {
      if (request !== soundRequest) return;
      toggle.checked = false;
      fade(20);
      for (const { audio } of tracks.values()) audio.pause();
      status.textContent = "环境声暂时无法播放";
      status.hidden = false;
    }
  });
  toggle.disabled = false;

  return {
    update(scene, duration) {
      if (scene === selected) return;
      selected = scene;
      fade(duration);
    },
  };
}
