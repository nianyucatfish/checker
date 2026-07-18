// 多轨同步混音引擎(参考 mix_console.py:MixPlaybackWorker)。
//
// 思路:
// - 每条轨道用 AudioBufferSourceNode + 自己的 GainNode(用于 mute/solo)
// - 所有轨 GainNode 汇到一个 masterGain 再到 destination
// - 同步:同一个 ctx.currentTime 锚点 + offset, src.start(when, offset) 一致排程
//   Web Audio 内部对样本级时间精确,各轨自动样本对齐
// - mute/solo:gain 0/1 切换;有 solo 时未 solo 的轨 gain=0
// - seek:停掉旧 sources,从新 offset 重启所有(轨道是一次性的 source,seek = 重排)
// - resampling:decodeAudioData 自动把任意采样率重采样到 ctx.sampleRate
//   (老版用 librosa.resample 到 44.1k mono,这里走浏览器原生 ~48k stereo)

export type MixState = "stopped" | "playing" | "paused";

export interface MixTrackData {
  path: string;
  name: string;
  buffer: AudioBuffer;
  durationSec: number;
  // 预算的 peaks(主线程一次性算,避免每帧重计算)
  peaks: { mins: Float32Array; maxs: Float32Array };
  muted: boolean;
  soloed: boolean;
}

const PEAKS_COLS = 2000;

function computePeaks(buffer: AudioBuffer, columns: number): {
  mins: Float32Array;
  maxs: Float32Array;
} {
  const nFrames = buffer.length;
  const channels = buffer.numberOfChannels;
  const cols = Math.min(columns, Math.max(1, nFrames));
  const mins = new Float32Array(cols);
  const maxs = new Float32Array(cols);
  // 复制 channel 引用避免每个 i 都调用 getChannelData
  const chans: Float32Array[] = [];
  for (let c = 0; c < channels; c++) chans.push(buffer.getChannelData(c));
  const samplesPerCol = nFrames / cols;
  for (let col = 0; col < cols; col++) {
    const start = Math.floor(col * samplesPerCol);
    const end = Math.min(nFrames, Math.floor((col + 1) * samplesPerCol));
    let mn = 1, mx = -1;
    for (let i = start; i < end; i++) {
      let s = chans[0][i];
      for (let c = 1; c < channels; c++) s += chans[c][i];
      s /= channels;
      if (s < mn) mn = s;
      if (s > mx) mx = s;
    }
    mins[col] = mn;
    maxs[col] = mx;
  }
  return { mins, maxs };
}

export class MixEngine {
  private ctx: AudioContext;
  private masterGain: GainNode;

  // 路径 → 轨道,按插入顺序保留;UI 显示用字典序由组件层决定。
  private tracks = new Map<string, MixTrackData>();
  private trackGains = new Map<string, GainNode>();
  private liveSources = new Map<string, AudioBufferSourceNode>();

  private state: MixState = "stopped";
  // 当前正在播放的"源"参考: ctx.currentTime 在 startedAtCtxTime 时刻对应
  // 轨道时间为 startOffsetSec(以下简记 t = startOffsetSec + (ctx.now - startedAtCtxTime))
  private startedAtCtxTime = 0;
  private startOffsetSec = 0;
  // 暂停/停止时记下的 t,下次 play 接着这里走
  private restingPosSec = 0;

  private positionListener: (sec: number, max: number) => void = () => {};
  private rafId: number | null = null;

  constructor() {
    const Ctor =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext })
        .webkitAudioContext;
    this.ctx = new Ctor();
    this.masterGain = this.ctx.createGain();
    this.masterGain.gain.value = 1.0;
    this.masterGain.connect(this.ctx.destination);
  }

  setPositionListener(cb: (sec: number, max: number) => void): void {
    this.positionListener = cb;
  }

  setMasterVolume(v: number): void {
    const safe = Math.max(0, Number.isFinite(v) ? v : 0);
    this.masterGain.gain.setValueAtTime(safe, this.ctx.currentTime);
  }

  /** 拉文件 → decode → cache。重复 path 跳过。 */
  async loadTrack(path: string, fileUrl: string): Promise<MixTrackData> {
    const existing = this.tracks.get(path);
    if (existing) return existing;
    const resp = await fetch(fileUrl);
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status} 拉取失败`);
    }
    const arrayBuf = await resp.arrayBuffer();
    // decodeAudioData 在某些实现下会"消费"输入 buffer,稳妥起见 slice 出副本
    const audioBuf = await this.ctx.decodeAudioData(arrayBuf.slice(0));
    const name = path.split(/[\\/]/).pop() || path;
    const peaks = computePeaks(audioBuf, PEAKS_COLS);
    const track: MixTrackData = {
      path,
      name,
      buffer: audioBuf,
      durationSec: audioBuf.duration,
      peaks,
      muted: false,
      soloed: false,
    };
    this.tracks.set(path, track);
    const g = this.ctx.createGain();
    g.gain.value = 1.0;
    g.connect(this.masterGain);
    this.trackGains.set(path, g);
    this.applyTrackGains();
    return track;
  }

  removeTrack(path: string): void {
    this.stopSourceFor(path);
    const g = this.trackGains.get(path);
    if (g) {
      try { g.disconnect(); } catch { /* noop */ }
      this.trackGains.delete(path);
    }
    this.tracks.delete(path);
    // 如果删完了仍在播,顺手停掉
    if (this.tracks.size === 0 && this.state === "playing") {
      this.stop();
    }
    this.notifyPosition();
  }

  setMuted(path: string, muted: boolean): void {
    const t = this.tracks.get(path);
    if (!t) return;
    t.muted = muted;
    if (muted) t.soloed = false;
    this.applyTrackGains();
  }

  setSoloed(path: string, soloed: boolean): void {
    const t = this.tracks.get(path);
    if (!t) return;
    t.soloed = soloed;
    this.applyTrackGains();
  }

  getTracks(): MixTrackData[] {
    return Array.from(this.tracks.values());
  }

  getState(): MixState { return this.state; }

  maxDuration(): number {
    let m = 0;
    for (const t of this.tracks.values()) {
      if (t.durationSec > m) m = t.durationSec;
    }
    return m;
  }

  currentPosition(): number {
    if (this.state === "playing") {
      const elapsed = this.ctx.currentTime - this.startedAtCtxTime;
      return Math.min(this.startOffsetSec + elapsed, this.maxDuration());
    }
    return this.restingPosSec;
  }

  play(): void {
    if (this.state === "playing") return;
    if (this.tracks.size === 0) return;
    this.ctx.resume().catch(() => {});
    this.startSourcesFromOffset(this.restingPosSec);
    this.state = "playing";
    this.startTick();
  }

  pause(): void {
    if (this.state !== "playing") return;
    const pos = this.currentPosition();
    this.stopAllSources();
    this.restingPosSec = pos;
    this.state = "paused";
    this.stopTick();
    this.notifyPosition();
  }

  stop(): void {
    this.stopAllSources();
    this.restingPosSec = 0;
    this.state = "stopped";
    this.stopTick();
    this.notifyPosition();
  }

  /** 跳到 toSec;播放中保持播放,停止/暂停时只更新待播位置。 */
  seek(toSec: number): void {
    const max = this.maxDuration();
    const clamped = Math.max(0, Math.min(toSec, max));
    if (this.state === "playing") {
      this.startSourcesFromOffset(clamped);
    } else {
      this.restingPosSec = clamped;
      this.state = clamped > 0 ? "paused" : "stopped";
    }
    this.notifyPosition();
  }

  dispose(): void {
    this.stopAllSources();
    this.stopTick();
    for (const g of this.trackGains.values()) {
      try { g.disconnect(); } catch { /* noop */ }
    }
    this.trackGains.clear();
    this.tracks.clear();
    try { this.masterGain.disconnect(); } catch { /* noop */ }
    this.ctx.close().catch(() => {});
  }

  // ---------- internal ----------

  private startSourcesFromOffset(fromSec: number): void {
    this.stopAllSources();
    this.startedAtCtxTime = this.ctx.currentTime;
    this.startOffsetSec = fromSec;
    this.applyTrackGains();
    for (const [path, t] of this.tracks) {
      if (fromSec >= t.durationSec) continue;
      const g = this.trackGains.get(path);
      if (!g) continue;
      const src = this.ctx.createBufferSource();
      src.buffer = t.buffer;
      src.connect(g);
      try {
        src.start(this.startedAtCtxTime, fromSec);
      } catch (e) {
        console.warn(`[mix] start ${path} 失败`, e);
        continue;
      }
      this.liveSources.set(path, src);
    }
  }

  private stopSourceFor(path: string): void {
    const s = this.liveSources.get(path);
    if (s) {
      try { s.stop(); } catch { /* 已停 */ }
      try { s.disconnect(); } catch { /* noop */ }
      this.liveSources.delete(path);
    }
  }

  private stopAllSources(): void {
    for (const path of Array.from(this.liveSources.keys())) {
      this.stopSourceFor(path);
    }
  }

  private applyTrackGains(): void {
    let anySolo = false;
    for (const t of this.tracks.values()) {
      if (t.soloed) { anySolo = true; break; }
    }
    const now = this.ctx.currentTime;
    for (const [path, t] of this.tracks) {
      const g = this.trackGains.get(path);
      if (!g) continue;
      let target = 1;
      if (t.muted) target = 0;
      else if (anySolo && !t.soloed) target = 0;
      g.gain.setValueAtTime(target, now);
    }
  }

  private startTick(): void {
    if (this.rafId != null) return;
    const tick = () => {
      const max = this.maxDuration();
      const pos = this.currentPosition();
      this.positionListener(pos, max);
      // 到尾自动停
      if (this.state === "playing" && pos >= max && max > 0) {
        this.stop();
        return;
      }
      this.rafId = requestAnimationFrame(tick);
    };
    this.rafId = requestAnimationFrame(tick);
  }

  private stopTick(): void {
    if (this.rafId != null) {
      cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }
  }

  private notifyPosition(): void {
    this.positionListener(this.currentPosition(), this.maxDuration());
  }
}
