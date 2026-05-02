import { useEffect, useMemo, useRef, useState } from "react";
import { Loader2, AlertCircle, FileAudio, ZoomIn, ZoomOut, RotateCcw } from "lucide-react";
import { getAudioMetadata, getAudioPeaks, rawFileUrl } from "../../api";
import type { AudioMetadataOut, AudioPeaksOut } from "../../api";

interface Props {
  path: string;
}

const MIN_ZOOM = 1;
const MAX_ZOOM = 200;
const FOLLOW_RIGHT_RATIO = 0.9;       // 播放头超过窗口 90% 处时触发自动跟随
const FOLLOW_PLACE_RATIO = 0.3;       // 跟随后把播放头放在窗口 30% 处
const DRAG_PIXEL_THRESHOLD = 3;        // 拖动距离 < 3px 视为单击

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
  const dpr = window.devicePixelRatio || 1;
  const width = Math.floor(canvas.clientWidth * dpr);
  const height = Math.floor(canvas.clientHeight * dpr);
  if (canvas.width !== width) canvas.width = width;
  if (canvas.height !== height) canvas.height = height;

  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.clearRect(0, 0, width, height);

  ctx.fillStyle = dark ? "#1e1e1e" : "#fafafa";
  ctx.fillRect(0, 0, width, height);

  const centerY = height / 2;
  const padding = 4 * dpr;
  const ampHalf = Math.max(1, centerY - padding);

  ctx.strokeStyle = dark ? "#3c3c3c" : "#e5e5e5";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, centerY);
  ctx.lineTo(width, centerY);
  ctx.stroke();

  const n = peaks.columns;
  if (n === 0 || view.duration <= 0 || view.visibleSec <= 0) return;

  // 可见时间范围 → peaks 索引切片
  const t0 = view.offsetSec;
  const t1 = view.offsetSec + view.visibleSec;
  const i0 = Math.max(0, Math.floor((t0 / view.duration) * n));
  const i1 = Math.min(n - 1, Math.ceil((t1 / view.duration) * n));
  const slice = Math.max(1, i1 - i0);

  // 播放头 X（限定在 [-1, width+1]）
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

function useDarkTheme() {
  const [dark, setDark] = useState(() =>
    document.documentElement.classList.contains("dark"),
  );
  useEffect(() => {
    const obs = new MutationObserver(() => {
      setDark(document.documentElement.classList.contains("dark"));
    });
    obs.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
    return () => obs.disconnect();
  }, []);
  return dark;
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

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const dragStateRef = useRef<{
    startX: number;
    startOffset: number;
    moved: boolean;
  } | null>(null);
  const dark = useDarkTheme();

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
    };
  }, [path]);

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

  // 重绘波形
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !peaks) return;
    const view: View = { duration, offsetSec, visibleSec, currentSec };
    drawWaveform(canvas, peaks, view, dark);
    const obs = new ResizeObserver(() => {
      drawWaveform(canvas, peaks, view, dark);
    });
    obs.observe(canvas);
    return () => obs.disconnect();
  }, [peaks, duration, offsetSec, visibleSec, currentSec, dark]);

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

  // 滚轮缩放（以光标位置为锚点）
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
    <div className="flex-1 flex flex-col p-6 gap-5 overflow-auto">
      <div className="flex items-center gap-3 text-fg-muted">
        <FileAudio size={28} />
        <div className="flex flex-col flex-1">
          <span className="text-sm">音频预览</span>
          <span className="text-xs text-fg-subtle">
            滚轮缩放 · 拖动平移 · 单击跳转 · 播放时自动跟随
          </span>
        </div>
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

      {/* 播放器 */}
      <audio
        ref={audioRef}
        controls
        src={audioUrl}
        className="w-full"
        preload="metadata"
      />

      {/* 元信息 */}
      <div className="grid grid-cols-2 gap-x-8 gap-y-4 max-w-md">
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
