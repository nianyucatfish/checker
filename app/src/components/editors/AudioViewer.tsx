import { useEffect, useMemo, useRef, useState } from "react";
import { Loader2, AlertCircle, FileAudio, ZoomIn, ZoomOut, RotateCcw, Play, Pause, Volume2 } from "lucide-react";
import { getAudioMetadata, getAudioPeaks, rawFileUrl, readCsv } from "../../api";
import type { AudioMetadataOut, AudioPeaksOut } from "../../api";
import { Metronome, type BeatMarker } from "../../lib/metronome";
import { useDarkTheme, setupWaveformCanvas } from "../../lib/waveform";
import { clsx, appAlert } from "../../utils";
import type { PlaybackToggleDetail, PlaybackToggleResult } from "../../lib/playback";

interface Props {
  path: string;
}

interface StructureMarker {
  t: number;
  label: string;
}

interface SongPaths {
  songFolder: string;
  songName: string;
  beatCsv: string;
  structureCsv: string;
  sep: string;
}

const MIN_ZOOM = 1;
const MAX_ZOOM = 200;
const FOLLOW_RIGHT_RATIO = 0.9;       // 播放头超过窗口 90% 处时触发自动跟随
const FOLLOW_PLACE_RATIO = 0.3;       // 跟随后把播放头放在窗口 30% 处
const DRAG_PIXEL_THRESHOLD = 3;        // 拖动距离 < 3px 视为单击

// 从 wav 路径推断歌曲文件夹和歌曲名,定位 csv 路径。
// wav 路径形如 <root>/<扒谱师>_<歌曲名>_<其他>/<目录>/X.wav
// 老版规则:song folder = parent of parent;name 取 song folder 名按 "_" split 的中间一组。
// 不匹配返回 null(只露出按钮但 disable)。
function inferSongPaths(wavPath: string): SongPaths | null {
  if (!wavPath) return null;
  const sep = wavPath.includes("\\") ? "\\" : "/";
  const parts = wavPath.split(/[\\/]/);
  if (parts.length < 3) return null;
  const songFolderName = parts[parts.length - 3];
  const m = songFolderName.match(/^(.+?)_(.+?)_(.+?)$/);
  if (!m) return null;
  const songName = m[2];
  const songFolder = parts.slice(0, parts.length - 2).join(sep);
  return {
    songFolder,
    songName,
    beatCsv: `${songFolder}${sep}csv${sep}${songName}_Beat.csv`,
    structureCsv: `${songFolder}${sep}csv${sep}${songName}_Structure.csv`,
    sep,
  };
}

// Beat.csv:第一行表头(必须含 TIME / LABEL),其余数据行;
// LABEL 末尾为 ".1" 视为主拍(downbeat)。容错:大小写、前后空白、空行。
function parseBeatRows(rows: string[][]): BeatMarker[] {
  if (rows.length === 0) return [];
  const header = rows[0].map((h) => (h ?? "").trim().toUpperCase());
  const tIdx = header.indexOf("TIME");
  const lIdx = header.indexOf("LABEL");
  if (tIdx < 0 || lIdx < 0) {
    throw new Error("Beat CSV 表头需要 TIME / LABEL 两列");
  }
  const out: BeatMarker[] = [];
  for (let i = 1; i < rows.length; i++) {
    const r = rows[i];
    if (!r || r.length === 0) continue;
    const tStr = (r[tIdx] ?? "").trim();
    if (!tStr) continue;
    const t = parseFloat(tStr);
    if (!Number.isFinite(t)) continue;
    const label = (r[lIdx] ?? "").trim();
    out.push({ t, isFirst: label.endsWith(".1") });
  }
  return out;
}

// Structure.csv:第 1 行 = 标签数组,第 2 行 = 时间戳数组(MM:SS 或 MM:SS.f)
// 第 3 行起忽略(老版只读两行)。
function parseStructureRows(rows: string[][]): StructureMarker[] {
  if (rows.length < 2) return [];
  const labels = rows[0];
  const times = rows[1];
  if (labels.length !== times.length) {
    throw new Error(`Structure CSV 行长度不一致: 标签 ${labels.length} vs 时间 ${times.length}`);
  }
  const out: StructureMarker[] = [];
  for (let i = 0; i < labels.length; i++) {
    const ts = (times[i] ?? "").trim();
    const label = (labels[i] ?? "").trim();
    if (!ts || !label) continue;
    const parts = ts.split(":");
    if (parts.length !== 2) continue;
    const mm = parseInt(parts[0], 10);
    const ss = parseFloat(parts[1]);
    if (!Number.isFinite(mm) || !Number.isFinite(ss)) continue;
    out.push({ t: mm * 60 + ss, label });
  }
  // 时间排序兜底
  out.sort((a, b) => a.t - b.t);
  return out;
}

function formatDuration(sec: number): string {
  const total = Math.round(sec);
  const mm = Math.floor(total / 60);
  const ss = total % 60;
  return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}

function formatHz(hz: number): string {
  if (hz >= 1000) return `${(hz / 1000).toFixed(hz % 1000 === 0 ? 0 : 1)} kHz`;
  return `${hz} Hz`;
}

function formatSubtype(subtype: string): string {
  if (subtype.startsWith("PCM_")) return `${subtype.slice(4)} bit`;
  if (subtype === "FLOAT") return "32 bit float";
  if (subtype === "DOUBLE") return "64 bit float";
  return subtype;
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs uppercase tracking-wide text-fg-subtle">{label}</span>
      <span className="text-fg font-mono text-sm">{value}</span>
    </div>
  );
}

interface View {
  duration: number;
  offsetSec: number;
  visibleSec: number;
  currentSec: number;
}

function drawWaveform(
  canvas: HTMLCanvasElement,
  peaks: AudioPeaksOut,
  view: View,
  dark: boolean,
) {
  const w = setupWaveformCanvas(canvas, dark, 4);
  if (!w) return;
  const { ctx, width, height, dpr, centerY, ampHalf } = w;

  const n = peaks.columns;
  if (n === 0 || view.duration <= 0 || view.visibleSec <= 0) return;

  // 可见时间范围 → peaks 索引切片
  const t0 = view.offsetSec;
  const t1 = view.offsetSec + view.visibleSec;
  const i0 = Math.max(0, Math.floor((t0 / view.duration) * n));
  const i1 = Math.min(n - 1, Math.ceil((t1 / view.duration) * n));
  const slice = Math.max(1, i1 - i0);

  // 播放头 X(限定在 [-1, width+1])
  let playheadX = -1;
  if (view.currentSec >= t0 && view.currentSec <= t1) {
    playheadX = Math.floor(((view.currentSec - t0) / view.visibleSec) * width);
  } else if (view.currentSec > t1) {
    playheadX = width + 1;
  }

  const drawSegment = (xStart: number, xEnd: number, color: string) => {
    if (xStart >= xEnd) return;
    ctx.strokeStyle = color;
    ctx.beginPath();
    for (let x = xStart; x < xEnd; x++) {
      const idx = Math.min(n - 1, i0 + Math.floor((x / width) * slice));
      const mn = peaks.mins[idx];
      const mx = peaks.maxs[idx];
      const y1 = centerY + mn * ampHalf;
      const y2 = centerY + mx * ampHalf;
      ctx.moveTo(x + 0.5, y1);
      ctx.lineTo(x + 0.5, y2);
    }
    ctx.stroke();
  };

  // 已播 / 未播 分段
  const playedEnd = Math.max(0, Math.min(width, playheadX));
  drawSegment(0, playedEnd, dark ? "#3794ff" : "#007acc");
  drawSegment(playedEnd, width, dark ? "#6a6a6a" : "#9ca3af");

  // 播放头线
  if (playheadX >= 0 && playheadX <= width) {
    ctx.strokeStyle = "#ff3b30";
    ctx.lineWidth = 1 * dpr;
    ctx.beginPath();
    ctx.moveTo(playheadX + 0.5, 0);
    ctx.lineTo(playheadX + 0.5, height);
    ctx.stroke();
  }
}

// 节拍线叠层:主拍粗实线、副拍细半透明线。
function drawBeatOverlay(
  canvas: HTMLCanvasElement,
  beats: BeatMarker[],
  view: View,
  dark: boolean,
) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.width;
  const height = canvas.height;
  if (view.visibleSec <= 0) return;
  const t0 = view.offsetSec;
  const t1 = view.offsetSec + view.visibleSec;

  // 副拍先画(底),主拍后画(顶)
  ctx.save();
  ctx.strokeStyle = dark ? "rgba(148,163,184,0.45)" : "rgba(100,116,139,0.55)";
  ctx.lineWidth = 1 * dpr;
  ctx.beginPath();
  for (const b of beats) {
    if (b.isFirst) continue;
    if (b.t < t0 || b.t > t1) continue;
    const x = Math.floor(((b.t - t0) / view.visibleSec) * width);
    ctx.moveTo(x + 0.5, 0);
    ctx.lineTo(x + 0.5, height);
  }
  ctx.stroke();

  ctx.strokeStyle = dark ? "#a78bfa" : "#7c3aed";
  ctx.lineWidth = 2 * dpr;
  ctx.beginPath();
  for (const b of beats) {
    if (!b.isFirst) continue;
    if (b.t < t0 || b.t > t1) continue;
    const x = Math.floor(((b.t - t0) / view.visibleSec) * width);
    ctx.moveTo(x + 0.5, 0);
    ctx.lineTo(x + 0.5, height);
  }
  ctx.stroke();
  ctx.restore();
}

// 段落 marker 叠层:绿色虚线 + 顶部标签条。
function drawStructureOverlay(
  canvas: HTMLCanvasElement,
  markers: StructureMarker[],
  view: View,
  dark: boolean,
) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.width;
  const height = canvas.height;
  if (view.visibleSec <= 0) return;
  const t0 = view.offsetSec;
  const t1 = view.offsetSec + view.visibleSec;

  ctx.save();
  ctx.font = `${11 * dpr}px ui-sans-serif, system-ui, sans-serif`;
  ctx.textBaseline = "top";

  for (const m of markers) {
    if (m.t < t0 || m.t > t1) continue;
    const x = Math.floor(((m.t - t0) / view.visibleSec) * width);

    // 竖虚线
    ctx.strokeStyle = dark ? "rgba(52,211,153,0.95)" : "rgba(5,150,105,0.95)";
    ctx.lineWidth = 1.5 * dpr;
    ctx.setLineDash([5 * dpr, 4 * dpr]);
    ctx.beginPath();
    ctx.moveTo(x + 0.5, 0);
    ctx.lineTo(x + 0.5, height);
    ctx.stroke();
    ctx.setLineDash([]);

    // 顶部标签胶囊
    if (m.label) {
      const padX = 5 * dpr;
      const padY = 3 * dpr;
      const txtY = 4 * dpr;
      const measure = ctx.measureText(m.label);
      const txtW = measure.width;
      const txtH = 11 * dpr;
      const boxX = x + 2 * dpr;
      const boxW = txtW + padX * 2;
      const boxH = txtH + padY * 2;
      ctx.fillStyle = dark ? "rgba(52,211,153,0.92)" : "rgba(5,150,105,0.92)";
      ctx.beginPath();
      const r = 3 * dpr;
      // 圆角矩形(兼容老 Canvas API,用 path 拼)
      ctx.moveTo(boxX + r, txtY);
      ctx.lineTo(boxX + boxW - r, txtY);
      ctx.quadraticCurveTo(boxX + boxW, txtY, boxX + boxW, txtY + r);
      ctx.lineTo(boxX + boxW, txtY + boxH - r);
      ctx.quadraticCurveTo(boxX + boxW, txtY + boxH, boxX + boxW - r, txtY + boxH);
      ctx.lineTo(boxX + r, txtY + boxH);
      ctx.quadraticCurveTo(boxX, txtY + boxH, boxX, txtY + boxH - r);
      ctx.lineTo(boxX, txtY + r);
      ctx.quadraticCurveTo(boxX, txtY, boxX + r, txtY);
      ctx.fill();
      ctx.fillStyle = "#ffffff";
      ctx.fillText(m.label, boxX + padX, txtY + padY);
    }
  }
  ctx.restore();
}

export function AudioViewer({ path }: Props) {
  const [meta, setMeta] = useState<AudioMetadataOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [peaks, setPeaks] = useState<AudioPeaksOut | null>(null);
  const [peaksLoading, setPeaksLoading] = useState(false);
  const [peaksError, setPeaksError] = useState<string | null>(null);
  const [currentSec, setCurrentSec] = useState(0);
  const [duration, setDuration] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [offsetSec, setOffsetSec] = useState(0);

  // 渲染节奏 / 结构
  const [beatRender, setBeatRender] = useState(false);
  const [beats, setBeats] = useState<BeatMarker[]>([]);
  const [structureRender, setStructureRender] = useState(false);
  const [structure, setStructure] = useState<StructureMarker[]>([]);
  const [metronomeVolPct, setMetronomeVolPct] = useState(150);
  const [renderToggleBusy, setRenderToggleBusy] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [volume, setVolume] = useState(() => {
    const saved = Number(localStorage.getItem("audio_qc.audio_volume"));
    return Number.isFinite(saved) && saved >= 0 && saved <= 1 ? saved : 1;
  });

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const dragStateRef = useRef<{
    startX: number;
    startOffset: number;
    moved: boolean;
  } | null>(null);
  const metronomeRef = useRef<Metronome | null>(null);
  const dark = useDarkTheme();

  const songPaths = useMemo(() => inferSongPaths(path), [path]);

  const visibleSec = useMemo(
    () => (duration > 0 ? duration / zoom : 0),
    [duration, zoom],
  );

  // 拉元信息 + 文件 URL
  useEffect(() => {
    let cancelled = false;
    setMeta(null);
    setError(null);
    setAudioUrl(null);
    setPeaks(null);
    setPeaksError(null);
    setCurrentSec(0);
    setDuration(0);
    setZoom(1);
    setOffsetSec(0);
    // 切文件时关掉渲染状态(节拍数据失效)
    setBeatRender(false);
    setBeats([]);
    setStructureRender(false);
    setStructure([]);
    if (metronomeRef.current) {
      metronomeRef.current.dispose();
      metronomeRef.current = null;
    }
    Promise.all([getAudioMetadata(path), rawFileUrl(path)])
      .then(([m, url]) => {
        if (cancelled) return;
        setMeta(m);
        setAudioUrl(url);
        setDuration(m.duration_seconds);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
      });
    return () => {
      cancelled = true;
      // 释放 <audio> 占用的句柄(sidecar /files/raw 端的文件锁),否则切文件 / 外部
      // 文件操作可能撞到 Win 文件锁。pause + src 清空 + load() 是浏览器侧的标准释放路径。
      const a = audioRef.current;
      if (a) {
        try {
          a.pause();
          a.removeAttribute("src");
          a.load();
        } catch { /* ignore */ }
      }
    };
  }, [path]);

  // 全局 audio:release 事件 —— Explorer 在文件写操作前广播,所有 AudioViewer 释放句柄,
  // 避免 Win 文件锁阻塞 rename / delete / move 等。
  useEffect(() => {
    const onRelease = () => {
      const a = audioRef.current;
      if (a) {
        try {
          a.pause();
          a.removeAttribute("src");
          a.load();
        } catch { /* ignore */ }
      }
    };
    window.addEventListener("audio:release", onRelease);
    return () => window.removeEventListener("audio:release", onRelease);
  }, []);

  // 服务端算 peaks
  useEffect(() => {
    let cancelled = false;
    setPeaksLoading(true);
    setPeaksError(null);
    getAudioPeaks(path, 4000)
      .then((p) => {
        if (cancelled) return;
        setPeaks(p);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setPeaksError(e.message || String(e));
      })
      .finally(() => {
        if (!cancelled) setPeaksLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [path]);

  // 重绘波形 + overlays
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !peaks) return;
    const view: View = { duration, offsetSec, visibleSec, currentSec };
    const draw = () => {
      drawWaveform(canvas, peaks, view, dark);
      if (beatRender && beats.length > 0) drawBeatOverlay(canvas, beats, view, dark);
      if (structureRender && structure.length > 0) {
        drawStructureOverlay(canvas, structure, view, dark);
      }
    };
    draw();
    const obs = new ResizeObserver(draw);
    obs.observe(canvas);
    return () => obs.disconnect();
  }, [
    peaks, duration, offsetSec, visibleSec, currentSec, dark,
    beatRender, beats, structureRender, structure,
  ]);

  // 监听 audio 播放进度 + 自动跟随
  useEffect(() => {
    const a = audioRef.current;
    if (!a) return;
    const onTime = () => {
      const t = a.currentTime;
      setCurrentSec(t);
      // 播放头超出可见窗口右侧 → 滚动跟随
      if (!a.paused && visibleSec > 0 && duration > 0) {
        const t0 = offsetSec;
        const t1 = offsetSec + visibleSec;
        if (t > t1 - visibleSec * (1 - FOLLOW_RIGHT_RATIO) || t < t0) {
          const newOffset = clamp(
            t - visibleSec * FOLLOW_PLACE_RATIO,
            0,
            Math.max(0, duration - visibleSec),
          );
          setOffsetSec(newOffset);
        }
      }
    };
    a.addEventListener("timeupdate", onTime);
    a.addEventListener("seeked", onTime);
    return () => {
      a.removeEventListener("timeupdate", onTime);
      a.removeEventListener("seeked", onTime);
    };
  }, [audioUrl, offsetSec, visibleSec, duration]);

  // metronome 与 audio 元素的生命周期同步
  // beatRender 切 ON 时:已播放则立即 start,未播放则等 play 事件
  // play / pause / seeked 事件路由到 metronome
  useEffect(() => {
    const a = audioRef.current;
    if (!a) return;
    const onPlay = () => {
      setIsPlaying(true);
      if (beatRender && metronomeRef.current) {
        metronomeRef.current.start(a);
      }
    };
    const onPause = () => {
      setIsPlaying(false);
      metronomeRef.current?.stop();
    };
    const onSeeked = () => {
      metronomeRef.current?.onSeek(a.currentTime);
    };
    a.addEventListener("play", onPlay);
    a.addEventListener("pause", onPause);
    a.addEventListener("seeked", onSeeked);
    a.addEventListener("ended", onPause);
    return () => {
      a.removeEventListener("play", onPlay);
      a.removeEventListener("pause", onPause);
      a.removeEventListener("seeked", onSeeked);
      a.removeEventListener("ended", onPause);
    };
  }, [beatRender, audioUrl]);

  // audio 元素的 volume 同步 + 持久化
  useEffect(() => {
    const a = audioRef.current;
    if (a) a.volume = volume;
    localStorage.setItem("audio_qc.audio_volume", String(volume));
  }, [volume]);

  // play/pause toggle
  const togglePlay = () => {
    const a = audioRef.current;
    if (!a) return;
    if (a.paused) a.play().catch((e) => console.warn("[audio] play failed:", e));
    else a.pause();
  };

  // 全局空格 → play/pause(本编辑器聚焦时)。在输入框/textarea/可编辑节点内不拦,
  // 避免抢用户的输入。AudioViewer 卸载即注销。
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.code !== "Space" && e.key !== " ") return;
      const t = e.target as HTMLElement | null;
      if (t) {
        const tag = t.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
        if (t.isContentEditable) return;
      }
      e.preventDefault();
      togglePlay();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // metronome 音量跟随 slider
  useEffect(() => {
    metronomeRef.current?.setVolume(metronomeVolPct / 100);
  }, [metronomeVolPct]);

  // 卸载时释放 metronome
  useEffect(() => {
    return () => {
      metronomeRef.current?.dispose();
      metronomeRef.current = null;
    };
  }, []);

  // 滚轮缩放(以光标位置为锚点)
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || duration <= 0) return;
    const handler = (e: WheelEvent) => {
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const u = clamp((e.clientX - rect.left) / rect.width, 0, 1);
      const factor = e.deltaY < 0 ? 1.2 : 1 / 1.2;
      setZoom((prev) => {
        const next = clamp(prev * factor, MIN_ZOOM, MAX_ZOOM);
        if (next === prev) return prev;
        const visOld = duration / prev;
        const visNew = duration / next;
        const timeAtCursor = offsetSec + u * visOld;
        const newOffset = clamp(
          timeAtCursor - u * visNew,
          0,
          Math.max(0, duration - visNew),
        );
        setOffsetSec(newOffset);
        return next;
      });
    };
    canvas.addEventListener("wheel", handler, { passive: false });
    return () => canvas.removeEventListener("wheel", handler);
  }, [duration, offsetSec]);

  const onMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (e.button !== 0) return;
    dragStateRef.current = {
      startX: e.clientX,
      startOffset: offsetSec,
      moved: false,
    };
  };

  const onMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const state = dragStateRef.current;
    if (!state) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dx = e.clientX - state.startX;
    if (Math.abs(dx) > DRAG_PIXEL_THRESHOLD) state.moved = true;
    if (!state.moved) return;
    const dt = -(dx / canvas.clientWidth) * visibleSec;
    setOffsetSec(
      clamp(state.startOffset + dt, 0, Math.max(0, duration - visibleSec)),
    );
  };

  const onMouseUp = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const state = dragStateRef.current;
    dragStateRef.current = null;
    if (!state || state.moved) return;
    // 视为单击 → seek
    const a = audioRef.current;
    const canvas = canvasRef.current;
    if (!a || !canvas) return;
    const rect = canvas.getBoundingClientRect();
    const u = clamp((e.clientX - rect.left) / rect.width, 0, 1);
    const t = offsetSec + u * visibleSec;
    a.currentTime = clamp(t, 0, duration);
  };

  const onMouseLeave = () => {
    dragStateRef.current = null;
  };

  const zoomBy = (factor: number) => {
    if (duration <= 0) return;
    setZoom((prev) => {
      const next = clamp(prev * factor, MIN_ZOOM, MAX_ZOOM);
      if (next === prev) return prev;
      // 以播放头/视图中心为锚点
      const center = currentSec >= offsetSec && currentSec <= offsetSec + visibleSec
        ? currentSec
        : offsetSec + visibleSec / 2;
      const visNew = duration / next;
      setOffsetSec(
        clamp(center - visNew / 2, 0, Math.max(0, duration - visNew)),
      );
      return next;
    });
  };

  const resetZoom = () => {
    setZoom(1);
    setOffsetSec(0);
  };

  // 返回 true = 达到了期望的开/关状态;false = 被 guard 拦下或渲染失败。
  // UI 按钮不看返回值,agent 事件桥(下面的 useEffect)靠它回执真实结果。
  const toggleBeat = async (): Promise<boolean> => {
    if (renderToggleBusy) return false;
    if (beatRender) {
      // OFF
      setBeatRender(false);
      setBeats([]);
      metronomeRef.current?.stop();
      // 不 dispose,留给下次 ON 复用 buffer
      return true;
    }
    if (!songPaths) return false;
    setRenderToggleBusy(true);
    try {
      const csv = await readCsv(songPaths.beatCsv);
      const parsed = parseBeatRows(csv.rows);
      if (parsed.length === 0) {
        await appAlert("Beat CSV 解析后无有效拍数据");
        return false;
      }
      setBeats(parsed);
      setBeatRender(true);
      // 准备 metronome
      if (!metronomeRef.current) metronomeRef.current = new Metronome();
      metronomeRef.current.setBeats(parsed);
      metronomeRef.current.setVolume(metronomeVolPct / 100);
      const a = audioRef.current;
      if (a && !a.paused) {
        metronomeRef.current.start(a);
      }
      return true;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      await appAlert(`渲染节奏失败: ${msg}`);
      return false;
    } finally {
      setRenderToggleBusy(false);
    }
  };

  const toggleStructure = async (): Promise<boolean> => {
    if (renderToggleBusy) return false;
    if (structureRender) {
      setStructureRender(false);
      setStructure([]);
      return true;
    }
    if (!songPaths) return false;
    setRenderToggleBusy(true);
    try {
      const csv = await readCsv(songPaths.structureCsv);
      const parsed = parseStructureRows(csv.rows);
      if (parsed.length === 0) {
        await appAlert("Structure CSV 解析后无有效段落");
        return false;
      }
      setStructure(parsed);
      setStructureRender(true);
      return true;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      await appAlert(`渲染结构失败: ${msg}`);
      return false;
    } finally {
      setRenderToggleBusy(false);
    }
  };

  // agent 的 playback_toggle_beat_render / _structure_render 最终走到这。
  // App 把 main → renderer IPC 转成 cancelable CustomEvent(契约见 lib/playback.ts):
  // 这里 preventDefault() 表示"AudioViewer 在场,接住了",并往 detail.result 塞
  // 执行结果 Promise,App 桥接层 await 后回执 main,agent 拿到真实成败。
  // idempotent:已处于期望状态就不动。用 ref 保引用,免 listener 每次 rebind。
  const toggleBeatRef = useRef(toggleBeat);
  const toggleStructureRef = useRef(toggleStructure);
  const beatRenderRef = useRef(beatRender);
  const structureRenderRef = useRef(structureRender);
  const songPathsRef = useRef(songPaths);
  const renderToggleBusyRef = useRef(renderToggleBusy);
  toggleBeatRef.current = toggleBeat;
  toggleStructureRef.current = toggleStructure;
  beatRenderRef.current = beatRender;
  structureRenderRef.current = structureRender;
  songPathsRef.current = songPaths;
  renderToggleBusyRef.current = renderToggleBusy;
  useEffect(() => {
    const handle = (e: Event, kind: "beat" | "structure") => {
      const ce = e as CustomEvent<PlaybackToggleDetail>;
      e.preventDefault();
      const want = !!ce.detail?.on;
      ce.detail.result = (async (): Promise<PlaybackToggleResult> => {
        if (renderToggleBusyRef.current) {
          return { ok: false, code: "BUSY", message: "上一次渲染切换还在进行中,稍后再调一次" };
        }
        const cur = kind === "beat" ? beatRenderRef.current : structureRenderRef.current;
        if (want === cur) {
          return { ok: true, message: want ? "已是开启状态" : "已是关闭状态" };
        }
        if (want && !songPathsRef.current) {
          return {
            ok: false,
            code: "NO_SONG_STRUCTURE",
            message: "当前 wav 不在 {歌手}_{歌曲}_{扒曲人}/<子目录>/ 结构里,定位不到同歌 csv",
          };
        }
        const done = await (kind === "beat"
          ? toggleBeatRef.current()
          : toggleStructureRef.current());
        return done
          ? { ok: true }
          : { ok: false, code: "RENDER_FAILED", message: "CSV 读取或解析失败(UI 已弹窗提示细节)" };
      })();
    };
    const onBeat = (e: Event) => handle(e, "beat");
    const onStruct = (e: Event) => handle(e, "structure");
    window.addEventListener("playback:toggle:beat", onBeat);
    window.addEventListener("playback:toggle:structure", onStruct);
    return () => {
      window.removeEventListener("playback:toggle:beat", onBeat);
      window.removeEventListener("playback:toggle:structure", onStruct);
    };
  }, []);

  if (error) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-danger gap-2 px-6">
        <AlertCircle size={20} />
        <span className="text-sm">读取失败</span>
        <span className="text-xs text-fg-muted text-center break-all">{error}</span>
      </div>
    );
  }

  if (!meta || !audioUrl) {
    return (
      <div className="flex-1 flex items-center justify-center text-fg-muted gap-2">
        <Loader2 size={16} className="animate-spin" />
        <span>读取元信息…</span>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col p-6 gap-4 overflow-auto scroll-stable">
      <div className="flex items-center gap-3 text-fg-muted">
        <FileAudio size={28} className="shrink-0" />
        <div className="flex flex-col flex-1 min-w-0">
          <span className="text-sm truncate">音频预览</span>
          <span className="text-xs text-fg-subtle truncate" title="滚轮缩放 · 拖动平移 · 单击跳转 · 播放时自动跟随">
            滚轮缩放 · 拖动平移 · 单击跳转 · 播放时自动跟随
          </span>
        </div>
        {/* 播放控制(替代原生 audio 条,避免底栏挤压导致控件消失) */}
        <button
          onClick={togglePlay}
          className="h-7 w-7 inline-flex items-center justify-center rounded-sm text-fg-muted hover:text-fg hover:bg-bg-hover"
          title={isPlaying ? "暂停" : "播放"}
        >
          {isPlaying ? <Pause size={14} /> : <Play size={14} />}
        </button>
        <div className="flex items-center gap-1 text-xs text-fg-subtle px-2">
          <Volume2 size={12} />
          <input
            type="range"
            min={0}
            max={1}
            step={0.01}
            value={volume}
            onChange={(e) => setVolume(Number(e.target.value))}
            className="w-20 selectable"
            title="播放音量"
          />
        </div>
        <span className="font-mono text-xs text-fg-muted tabular-nums px-1">
          {formatDuration(currentSec)} / {formatDuration(duration)}
        </span>
        <div className="flex items-center gap-1 text-xs">
          <button
            onClick={() => zoomBy(1 / 1.5)}
            disabled={zoom <= MIN_ZOOM}
            className="h-6 px-2 inline-flex items-center gap-1 rounded-sm text-fg-muted hover:text-fg hover:bg-bg-hover disabled:opacity-40 disabled:cursor-not-allowed"
            title="缩小"
          >
            <ZoomOut size={12} />
          </button>
          <span className="font-mono text-fg-muted w-14 text-center">
            {zoom.toFixed(1)}x
          </span>
          <button
            onClick={() => zoomBy(1.5)}
            disabled={zoom >= MAX_ZOOM}
            className="h-6 px-2 inline-flex items-center gap-1 rounded-sm text-fg-muted hover:text-fg hover:bg-bg-hover disabled:opacity-40 disabled:cursor-not-allowed"
            title="放大"
          >
            <ZoomIn size={12} />
          </button>
          <button
            onClick={resetZoom}
            disabled={zoom === 1 && offsetSec === 0}
            className="h-6 px-2 inline-flex items-center gap-1 rounded-sm text-fg-muted hover:text-fg hover:bg-bg-hover disabled:opacity-40 disabled:cursor-not-allowed"
            title="重置"
          >
            <RotateCcw size={12} />
          </button>
        </div>
      </div>

      {/* 渲染工具行:节奏 / 结构 toggle + 节拍器音量 */}
      <div className="flex items-center gap-2 text-xs">
        <button
          onClick={toggleBeat}
          disabled={!songPaths || renderToggleBusy}
          title={
            !songPaths
              ? "需要 wav 位于 <扒谱师>_<歌曲名>_<其他>/<目录>/X.wav 形式才能定位 Beat.csv"
              : beatRender
                ? "关闭节奏渲染"
                : "读取 Beat.csv 在波形上叠节拍线 + Web Audio 节拍器"
          }
          className={clsx(
            "h-7 px-2.5 inline-flex items-center gap-1 rounded-sm",
            beatRender
              ? "bg-violet-500/20 text-violet-600 dark:text-violet-400"
              : "text-fg-muted hover:text-fg hover:bg-bg-hover",
            "disabled:opacity-40 disabled:cursor-not-allowed",
          )}
        >
          {renderToggleBusy && beatRender === false && (
            <Loader2 size={11} className="animate-spin" />
          )}
          {beatRender ? "取消节奏" : "渲染节奏"}
        </button>
        {beatRender && (
          <div className="flex items-center gap-2 ml-1 pl-3 border-l border-border">
            <span className="text-fg-subtle">节拍器</span>
            <input
              type="range"
              min={0}
              max={300}
              step={1}
              value={metronomeVolPct}
              onChange={(e) => setMetronomeVolPct(Number(e.target.value))}
              className="w-28 selectable"
              title="节拍器音量"
            />
            <span className="font-mono text-fg-muted w-10 tabular-nums">
              {metronomeVolPct}%
            </span>
          </div>
        )}
        <span className="flex-1" />
        <button
          onClick={toggleStructure}
          disabled={!songPaths || renderToggleBusy}
          title={
            !songPaths
              ? "需要 wav 位于 <扒谱师>_<歌曲名>_<其他>/<目录>/X.wav 形式才能定位 Structure.csv"
              : structureRender
                ? "关闭结构渲染"
                : "读取 Structure.csv 在波形上叠段落 marker"
          }
          className={clsx(
            "h-7 px-2.5 inline-flex items-center gap-1 rounded-sm",
            structureRender
              ? "bg-success/20 text-success"
              : "text-fg-muted hover:text-fg hover:bg-bg-hover",
            "disabled:opacity-40 disabled:cursor-not-allowed",
          )}
        >
          {structureRender ? "取消结构" : "渲染结构"}
        </button>
      </div>

      {/* 波形 */}
      <div className="flex flex-col gap-1">
        <div className="relative w-full h-32 border border-border rounded-sm overflow-hidden bg-bg-sidebar">
          {peaksLoading && (
            <div className="absolute inset-0 flex items-center justify-center text-fg-muted gap-2 z-10 bg-bg-sidebar/80">
              <Loader2 size={14} className="animate-spin" />
              <span className="text-xs">计算波形…</span>
            </div>
          )}
          {peaksError && (
            <div className="absolute inset-0 flex items-center justify-center text-danger gap-2 z-10 bg-bg-sidebar/80 px-4">
              <AlertCircle size={14} />
              <span className="text-xs text-center break-all">{peaksError}</span>
            </div>
          )}
          <canvas
            ref={canvasRef}
            onMouseDown={onMouseDown}
            onMouseMove={onMouseMove}
            onMouseUp={onMouseUp}
            onMouseLeave={onMouseLeave}
            className="w-full h-full block cursor-grab active:cursor-grabbing"
          />
        </div>
        <div className="flex justify-between text-xs text-fg-subtle font-mono px-1">
          <span>{formatDuration(offsetSec)}</span>
          <span>{formatDuration(currentSec)} / {formatDuration(duration)}</span>
          <span>{formatDuration(offsetSec + visibleSec)}</span>
        </div>
      </div>

      {/* 隐藏的 <audio> 元素 —— 不显示原生条(底栏挤压时会缩到看不见),
          play/pause / volume / seek 都在波形上方 toolbar 里操作。 */}
      <audio
        ref={audioRef}
        src={audioUrl}
        preload="metadata"
        className="hidden"
      />

      {/* 元信息 */}
      <div className="grid grid-cols-2 gap-x-8 gap-y-4 max-w-md selectable">
        <Field label="时长" value={formatDuration(meta.duration_seconds)} />
        <Field label="采样率" value={formatHz(meta.samplerate)} />
        <Field label="通道数" value={String(meta.channels)} />
        <Field label="位深" value={formatSubtype(meta.subtype)} />
        <Field label="帧数" value={meta.frames.toLocaleString()} />
        <Field label="精确时长" value={`${meta.duration_seconds.toFixed(3)} s`} />
      </div>
    </div>
  );
}
