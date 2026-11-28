class SpeechMeter extends AudioWorkletProcessor {
  blocks = 0;
  firstSignal = null;
  lastSignal = null;
  squares = 0;
  samples = 0;

  process(inputs, outputs) {
    const channel = inputs[0]?.[0];
    const output = outputs[0][0];
    if (channel) output.set(channel);
    for (let index = 0; index < output.length; index += 1) {
      const sample = output[index];
      this.squares += sample * sample;
      if (Math.abs(sample) < 0.001) continue;
      this.firstSignal ??= (currentFrame + index) / sampleRate;
      this.lastSignal = (currentFrame + index + 1) / sampleRate;
    }
    this.samples += output.length;
    this.blocks += 1;
    if (this.blocks === 4) {
      this.port.postMessage({
        firstSignal: this.firstSignal,
        lastSignal: this.lastSignal,
        observedTo: (currentFrame + output.length) / sampleRate,
        level: Math.min(1, 4 * Math.sqrt(this.squares / this.samples)),
      });
      this.blocks = this.squares = this.samples = 0;
      this.firstSignal = this.lastSignal = null;
    }
    return true;
  }
}

registerProcessor("avatar-speech-meter", SpeechMeter);
