// PCM 与采样率的基础件。
//
// 协议里的音频是裸的 pcm_s16le：上行 16000 Hz、20 ms 一块（320 样本、640 字节），
// 下行 16000 或 24000 Hz、同一句采样率固定。这里只做“字节 / 浮点 / 重采样 / 切块”
// 这几件事，不碰 DOM，方便单独测。

export const SAMPLE_RATE_IN = 16000; // 上行固定 16 kHz
export const FRAME_MS = 20;
export const FRAME_SAMPLES_IN = (SAMPLE_RATE_IN * FRAME_MS) / 1000; // 320

/** Int16Array → Float32Array（-1..1）。 */
export function int16ToFloat32(i16) {
  const out = new Float32Array(i16.length);
  for (let i = 0; i < i16.length; i++) out[i] = i16[i] / 32768;
  return out;
}

/** Float32Array（-1..1）→ Int16Array，先夹再取整，避免削波时溢出成反相。 */
export function float32ToInt16(f32) {
  const out = new Int16Array(f32.length);
  for (let i = 0; i < f32.length; i++) {
    const v = Math.max(-1, Math.min(1, f32[i]));
    out[i] = v < 0 ? Math.round(v * 32768) : Math.round(v * 32767);
  }
  return out;
}

/** 均方根，0..1，用来做电平与插话阈值。 */
export function rmsOf(f32) {
  if (!f32.length) return 0;
  let sum = 0;
  for (let i = 0; i < f32.length; i++) sum += f32[i] * f32[i];
  return Math.sqrt(sum / f32.length);
}

/** Uint8Array → base64。分块拼接，避免大数组时 apply 爆栈。 */
export function bytesToBase64(bytes) {
  let bin = "";
  const step = 0x8000;
  for (let i = 0; i < bytes.length; i += step) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + step));
  }
  return btoa(bin);
}

/** base64 → Uint8Array。 */
export function base64ToBytes(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

/**
 * 流式重采样（线性插值，带相位记忆）。
 * 允许块与块之间连续重采样，不会在块边界产生咔哒声。
 */
export class Resampler {
  constructor(srcRate, dstRate = SAMPLE_RATE_IN) {
    this.step = srcRate / dstRate;
    this.pos = 0;
    this.leftover = new Float32Array(0);
  }

  process(input) {
    if (!input || !input.length) return new Float32Array(0);
    const x = new Float32Array(this.leftover.length + input.length);
    x.set(this.leftover, 0);
    x.set(input, this.leftover.length);

    const out = [];
    let pos = this.pos;
    while (pos + 1 < x.length) {
      const i = Math.floor(pos);
      const f = pos - i;
      out.push(x[i] * (1 - f) + x[i + 1] * f);
      pos += this.step;
    }
    const consumed = Math.min(Math.floor(pos), x.length);
    this.leftover = x.slice(consumed);
    this.pos = pos - consumed;
    return Float32Array.from(out);
  }
}

/**
 * 把任意长度的浮点流切成固定大小的块（默认 20 ms）。
 * flush() 会把尾巴补零成完整的一块——协议要求结尾短块仍是完整采样，
 * 否则接收端按 320 样本切会错位。
 */
export class Framer {
  constructor(samples = FRAME_SAMPLES_IN) {
    this.size = samples;
    this.pending = new Float32Array(0);
  }

  push(input) {
    if (input && input.length) {
      const merged = new Float32Array(this.pending.length + input.length);
      merged.set(this.pending, 0);
      merged.set(input, this.pending.length);
      this.pending = merged;
    }
    const frames = [];
    let off = 0;
    while (this.pending.length - off >= this.size) {
      frames.push(this.pending.slice(off, off + this.size));
      off += this.size;
    }
    if (off) this.pending = this.pending.slice(off);
    return frames;
  }

  flush() {
    if (!this.pending.length) return null;
    const frame = new Float32Array(this.size);
    frame.set(this.pending.subarray(0, Math.min(this.pending.length, this.size)));
    this.pending = new Float32Array(0);
    return frame;
  }
}
