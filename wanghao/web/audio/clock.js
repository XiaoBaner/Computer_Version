// 页面级单调时钟。
//
// 协议要求：t_ms 用本页初始化起的单调时钟计，断线重连不清零，END-TO-END 时延
// 用同一端的采集/播放时戳相减。所以这里只在模块加载时取一次原点，之后不再重置
// ——重新连 WebSocket 不会重新 import 这个模块，原点自然保持。

const ORIGIN =
  typeof performance !== "undefined" && typeof performance.now === "function"
    ? performance.now()
    : Date.now();

/** 当前页面的毫秒时戳，整数，单调不减。 */
export function nowMs() {
  const t =
    typeof performance !== "undefined" && typeof performance.now === "function"
      ? performance.now()
      : Date.now();
  return Math.round(t - ORIGIN);
}
