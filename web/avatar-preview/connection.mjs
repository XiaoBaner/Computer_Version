function valid(value, type) {
  if (type.const !== undefined) return value === type.const;
  if (type.enum) return type.enum.includes(value);
  if (type.type === "string") return typeof value === "string" && value.length >= (type.minLength ?? 1);
  if (type.type === "boolean") return typeof value === "boolean";
  return Number.isSafeInteger(value) && value >= (type.minimum ?? 0);
}

function serverEvent(event, catalog) {
  if (!event || typeof event !== "object") return false;
  const definition = catalog.events[event.type];
  if (!definition?.from?.includes("server")) return false;
  const required = { ...catalog.common, ...definition.required };
  return Object.entries(required).every(([key, type]) => valid(event[key], catalog.types[type]))
    && Object.entries(definition.optional ?? {}).every(
      ([key, type]) => !Object.hasOwn(event, key) || valid(event[key], catalog.types[type]),
    );
}

function websocketUrl(path) {
  const url = new URL(path, location.href);
  if (url.origin !== location.origin || url.pathname !== "/ws/session") {
    throw new Error("会话地址无效");
  }
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url;
}

export function createConnection({ catalog, mount, onStatus, onSend, onUnconfirmed }) {
  const pending = new Map();
  let pageId = crypto.randomUUID();
  let bootstrap;
  let socket;
  let session;
  let retryTimer;
  let retryCount = 0;
  let request;
  let disposed = false;

  function send(event) {
    if (socket?.readyState !== WebSocket.OPEN) return false;
    if (event.type === "input.audio" && socket.bufferedAmount > 90_000) return false;
    socket.send(JSON.stringify(event));
    onSend(event);
    return true;
  }

  async function open() {
    if (disposed || request || (socket && socket.readyState !== WebSocket.CLOSED)) return;
    clearTimeout(retryTimer);
    onStatus("connecting");
    if (!bootstrap) {
      const controller = new AbortController();
      request = controller;
      const timeout = setTimeout(() => controller.abort(), 6000);
      try {
        const response = await fetch("/api/session/bootstrap", {
          headers: { "X-Page-ID": pageId }, cache: "no-store", signal: controller.signal,
        });
        if (!response.ok) throw new Error("会话初始化失败");
        const data = await response.json();
        if (disposed) return;
        if (data.v !== 1 || typeof data.session_id !== "string" || !data.session_id
          || !catalog.types.scene.enum.includes(data.scene_id) || !data.features) {
          throw new Error("会话初始化响应不符合 v1 协议");
        }
        websocketUrl(data.ws_path);
        session = mount(data, send);
        bootstrap = data;
      } catch {
        if (!disposed) onStatus("offline", "会话初始化失败，可重试。");
        return;
      } finally {
        request = undefined;
        clearTimeout(timeout);
      }
    }

    const connection = new WebSocket(websocketUrl(bootstrap.ws_path));
    socket = connection;
    connection.addEventListener("open", () => {
      if (disposed || socket !== connection) return;
      retryCount = 0;
      session.ready();
      onStatus("online");
      for (const [input_id, text] of pending) session.emit("input.text", { input_id, text });
    });
    connection.addEventListener("message", ({ data }) => {
      if (disposed || socket !== connection || typeof data !== "string") return;
      let event;
      try { event = JSON.parse(data); } catch { return; }
      if (!serverEvent(event, catalog) || event.session_id !== bootstrap.session_id) return;
      if (event.code === "SESSION_EXPIRED" && event.type === "session.error") {
        socket = undefined;
        connection.close();
        session.disconnect();
        session.dispose();
        bootstrap = undefined;
        pageId = crypto.randomUUID();
        onUnconfirmed([...pending.values()]);
        pending.clear();
        open();
        return;
      }
      if (event.type === "turn.state" || (event.type === "transcript" && event.is_final)) {
        pending.delete(event.input_id);
      }
      session.applyEvent(event);
    });
    connection.addEventListener("close", () => {
      if (disposed || socket !== connection) return;
      session.disconnect();
      const delay = Math.min(5000, 350 * 2 ** retryCount++);
      onStatus("offline", "连接已断开，正在重连。");
      retryTimer = setTimeout(open, delay);
    });
  }

  return {
    open,
    submit(value) {
      const text = value.trim();
      if (!text || socket?.readyState !== WebSocket.OPEN) return false;
      session.interrupt("input_text");
      const input_id = crypto.randomUUID();
      pending.set(input_id, text);
      session.emit("input.text", { input_id, text });
      return true;
    },
    dispose() {
      disposed = true;
      clearTimeout(retryTimer);
      request?.abort();
      socket?.close();
      session?.dispose();
      pending.clear();
    },
  };
}
