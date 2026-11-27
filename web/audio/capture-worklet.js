// 只做一件事：把麦克风的浮点块原样转给主线程。
//
// 重采样和切块放在主线程的 pcm.js 里做，这样同一套逻辑在 AudioWorklet 不可用时
// 也能被 ScriptProcessor 复用，而且重采样状态只有一份。

class CaptureForwarder extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    if (input && input[0] && input[0].length) {
      // 复制一份：input 的底层缓冲会被下一次 process 复用。
      this.port.postMessage(new Float32Array(input[0]));
    }
    return true; // 保持处理器存活直到断开
  }
}

registerProcessor("capture-forwarder", CaptureForwarder);
