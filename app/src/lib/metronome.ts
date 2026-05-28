// Web Audio 节拍器:跟随 <audio> 元素的 currentTime 调度 click 声。
//
// 实现要点(参考 audio_player.py:_build_click_waveform):
// - 主拍 (downbeat / "is_first"): 1800 Hz, 65 ms, gain 0.12
// - 副拍 (upbeat):                1100 Hz, 55 ms, gain 0.075
// - 波形:square (sign of sine) × 几何下降包络 (1.0 → 0.003)
//
// 调度策略 (参考 Chris Wilson "A Tale of Two Clocks"):
// - 25 ms 节拍循环,提前 100 ms 用 AudioBufferSourceNode.start(when) 排程
// - <audio> currentTime 与 AudioContext.currentTime 之间偏差靠每 tick 重采样吸收
// - seek 时清空已排程指针,让下次 tick 从新位置查找

export interface BeatMarker {
  t: number; // 秒
  isFirst: boolean; // 主拍(label 以 ".1" 结尾)
}

const LOOK_AHEAD_SEC = 0.1; // 提前 100ms 排
const TICK_INTERVAL_MS = 25;
const STALE_BEAT_TOLERANCE_SEC = 0.05; // 落后 50ms 以上的拍直接跳过

export class Metronome {
  private ctx: AudioContext;
  private master: GainNode;
  private strongBuf: AudioBuffer;
  private weakBuf: AudioBuffer;
  private beats: BeatMarker[] = [];
  private nextBeatIdx = 0;
  private timer: number | null = null;
  // 持续追踪 audio 元素,tick 时读 currentTime + paused
  private audioEl: HTMLAudioElement | null = null;

  constructor() {
    const Ctor =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext })
        .webkitAudioContext;
    this.ctx = new Ctor();
    this.master = this.ctx.createGain();
    this.master.gain.value = 1.5; // 默认 150%
    this.master.connect(this.ctx.destination);
    this.strongBuf = this.buildClick(true);
    this.weakBuf = this.buildClick(false);
  }

  setBeats(beats: BeatMarker[]): void {
    // 排序兜底,虽然 CSV 通常是有序的
    this.beats = [...beats].sort((a, b) => a.t - b.t);
    // 重新对齐到当前播放位置(无 audio 时从 0 开始)
    const t = this.audioEl?.currentTime ?? 0;
    this.advanceTo(t);
  }

  /** v ∈ [0, 3+],对应 0%-300%。线性映射,不做 dB 曲线。 */
  setVolume(v: number): void {
    const safe = Math.max(0, Number.isFinite(v) ? v : 0);
    this.master.gain.setValueAtTime(safe, this.ctx.currentTime);
  }

  /** 绑定 audio 元素并开始 25ms 循环。idempotent(重复调相当于换 audio + 重启)。 */
  start(audio: HTMLAudioElement): void {
    this.stop();
    this.audioEl = audio;
    // 用户手势(toggle 按钮 / play 事件)调进来,可以 resume
    this.ctx.resume().catch(() => {});
    this.advanceTo(audio.currentTime);
    this.timer = window.setInterval(() => this.tick(), TICK_INTERVAL_MS);
  }

  /** 停止调度但保留 audio 绑定 + buffers,后续 start 不需重建。 */
  stop(): void {
    if (this.timer != null) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  /** seek 时调:让下次 tick 从新位置开始查找。 */
  onSeek(audioTime: number): void {
    this.advanceTo(audioTime);
  }

  /** 释放 AudioContext。组件卸载时调一次。 */
  dispose(): void {
    this.stop();
    this.audioEl = null;
    this.ctx.close().catch(() => {});
  }

  // ---------- 内部 ----------

  private advanceTo(t: number): void {
    let i = 0;
    // 跳过所有 < 当前时间(留 10ms 余量,刚过的拍立刻响)
    while (i < this.beats.length && this.beats[i].t < t - 0.01) i++;
    this.nextBeatIdx = i;
  }

  private tick(): void {
    if (!this.audioEl || this.audioEl.paused) return;
    const audioT = this.audioEl.currentTime;
    const lookEnd = audioT + LOOK_AHEAD_SEC;
    while (
      this.nextBeatIdx < this.beats.length &&
      this.beats[this.nextBeatIdx].t <= lookEnd
    ) {
      const beat = this.beats[this.nextBeatIdx];
      const dt = beat.t - audioT;
      if (dt < -STALE_BEAT_TOLERANCE_SEC) {
        // 落后太多(可能 seek 时遗漏的小区间) 跳过
        this.nextBeatIdx++;
        continue;
      }
      const when = this.ctx.currentTime + Math.max(0, dt);
      const buf = beat.isFirst ? this.strongBuf : this.weakBuf;
      const src = this.ctx.createBufferSource();
      src.buffer = buf;
      src.connect(this.master);
      src.start(when);
      this.nextBeatIdx++;
    }
  }

  private buildClick(isFirst: boolean): AudioBuffer {
    const sr = this.ctx.sampleRate;
    const freq = isFirst ? 1800 : 1100;
    const dur = isFirst ? 0.065 : 0.055;
    const peakGain = isFirst ? 0.12 : 0.075;
    const n = Math.max(1, Math.round(dur * sr));
    const buf = this.ctx.createBuffer(1, n, sr);
    const data = buf.getChannelData(0);
    // 几何衰减包络:1.0 → 0.003 over n 样本
    const envEnd = 0.003;
    const envFactor = n > 1 ? Math.pow(envEnd, 1 / (n - 1)) : 1;
    let env = 1.0;
    const omega = 2 * Math.PI * freq;
    for (let i = 0; i < n; i++) {
      const sample = Math.sign(Math.sin(omega * (i / sr)));
      data[i] = sample * env * peakGain;
      env *= envFactor;
    }
    return buf;
  }
}
