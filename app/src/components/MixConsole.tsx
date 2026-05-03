import { useEffect, useMemo, useRef, useState } from "react";
import {
  Loader2,
  Play,
  Pause,
  Square,
  X,
  Minus,
  AlertCircle,
  Volume2,
} from "lucide-react";
import { rawFileUrl } from "../api";
import { MixEngine, type MixTrackData } from "../lib/mixEngine";
import { clsx } from "../utils";

interface Props {
  tracks: string[]; // 路径列表(由父级通过右键菜单累加)
  onRemove: (path: string) => void;
  onMinimize?: () => void; // 自定义"最小化"(隐藏窗口,tracks 保留)
  onClose: () => void;     // 关闭(清空 tracks)
}

const PEAKS_HEIGHT = 56;
const TRACK_NAME_WIDTH = 160;

function fmtTime(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) sec = 0;
  const total = Math.floor(sec);
  const mm = Math.floor(total / 60);
  const ss = total % 60;
  return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
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

function drawTrackWaveform(
  canvas: HTMLCanvasElement,
  peaks: { mins: Float32Array; maxs: Float32Array },
  durationSec: number,
  posSec: number,
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

  const cy = height / 2;
  const padY = 2 * dpr;
  const ampHalf = Math.max(1, cy - padY);

  // 中线
  ctx.strokeStyle = dark ? "#3c3c3c" : "#e5e5e5";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, cy);
  ctx.lineTo(width, cy);
  ctx.stroke();

  const n = peaks.mins.length;
  if (n === 0 || durationSec <= 0) return;

  const playheadX =
    posSec > 0 && posSec <= durationSec
      ? Math.floor((posSec / durationSec) * width)
      : -1;

  const drawSegment = (xStart: number, xEnd: number, color: string) => {
    if (xStart >= xEnd) return;
    ctx.strokeStyle = color;
    ctx.beginPath();
    for (let x = xStart; x < xEnd; x++) {
      const idx = Math.min(n - 1, Math.floor((x / width) * n));
      const mn = peaks.mins[idx];
      const mx = peaks.maxs[idx];
      ctx.moveTo(x + 0.5, cy + mn * ampHalf);
      ctx.lineTo(x + 0.5, cy + mx * ampHalf);
    }
    ctx.stroke();
  };

  const playedEnd = Math.max(0, Math.min(width, playheadX));
  drawSegment(0, playedEnd, dark ? "#3794ff" : "#007acc");
  drawSegment(playedEnd, width, dark ? "#6a6a6a" : "#9ca3af");

  if (playheadX >= 0 && playheadX <= width) {
    ctx.strokeStyle = "#ff3b30";
    ctx.lineWidth = 1 * dpr;
    ctx.beginPath();
    ctx.moveTo(playheadX + 0.5, 0);
    ctx.lineTo(playheadX + 0.5, height);
    ctx.stroke();
  }
}

interface TrackRowProps {
  track: MixTrackData;
  posSec: number;
  loading: boolean;
  loadError: string | null;
  onMute: (muted: boolean) => void;
  onSolo: (soloed: boolean) => void;
  onRemove: () => void;
  onSeek: (sec: number) => void;
  dark: boolean;
}

function TrackRow({
  track, posSec, loading, loadError,
  onMute, onSolo, onRemove, onSeek, dark,
}: TrackRowProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    drawTrackWaveform(c, track.peaks, track.durationSec, posSec, dark);
    const obs = new ResizeObserver(() => {
      drawTrackWaveform(c, track.peaks, track.durationSec, posSec, dark);
    });
    obs.observe(c);
    return () => obs.disconnect();
  }, [track.peaks, track.durationSec, posSec, dark]);

  const onCanvasClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const c = canvasRef.current;
    if (!c) return;
    const rect = c.getBoundingClientRect();
    const u = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    onSeek(u * track.durationSec);
  };

  return (
    <div className="flex items-stretch gap-2 px-2 py-1 border-b border-border-subtle">
      <div className="flex flex-col justify-center" style={{ width: TRACK_NAME_WIDTH }}>
        <span
          className="text-sm truncate"
          title={`${track.name}\n${track.path}`}
        >
          {track.name.replace(/\.[^.]+$/, "")}
        </span>
        <div className="flex items-center gap-1 mt-1">
          <button
            onClick={() => onMute(!track.muted)}
            title={track.muted ? "取消静音" : "静音"}
            className={clsx(
              "h-6 w-7 rounded-sm text-xs font-semibold",
              track.muted
                ? "bg-warning/30 text-warning"
                : "text-fg-muted hover:text-fg hover:bg-bg-hover",
            )}
          >
            M
          </button>
          <button
            onClick={() => onSolo(!track.soloed)}
            title={track.soloed ? "取消独奏" : "独奏"}
            className={clsx(
              "h-6 w-7 rounded-sm text-xs font-semibold",
              track.soloed
                ? "bg-accent/30 text-accent"
                : "text-fg-muted hover:text-fg hover:bg-bg-hover",
            )}
          >
            S
          </button>
          <button
            onClick={onRemove}
            title="移除轨道"
            className="h-6 w-8 rounded-sm text-xs text-fg-muted hover:text-danger hover:bg-bg-hover"
          >
            RM
          </button>
        </div>
      </div>
      <div className="flex-1 relative" style={{ height: PEAKS_HEIGHT }}>
        {loading ? (
          <div className="absolute inset-0 flex items-center justify-center text-fg-muted gap-2 bg-bg-sidebar/50 rounded-sm">
            <Loader2 size={12} className="animate-spin" />
            <span className="text-xs">加载中…</span>
          </div>
        ) : loadError ? (
          <div className="absolute inset-0 flex items-center justify-center text-danger gap-2 bg-bg-sidebar/50 rounded-sm px-3">
            <AlertCircle size={12} />
            <span className="text-xs truncate">{loadError}</span>
          </div>
        ) : (
          <canvas
            ref={canvasRef}
            onClick={onCanvasClick}
            className="w-full h-full block cursor-pointer rounded-sm border border-border"
          />
        )}
      </div>
    </div>
  );
}

interface LoadingPlaceholderProps {
  path: string;
  loadError: string | null;
  onRemove: () => void;
}

function LoadingPlaceholder({ path, loadError, onRemove }: LoadingPlaceholderProps) {
  const name = path.split(/[\\/]/).pop() || path;
  return (
    <div className="flex items-stretch gap-2 px-2 py-1 border-b border-border-subtle">
      <div className="flex flex-col justify-center" style={{ width: TRACK_NAME_WIDTH }}>
        <span className="text-sm truncate" title={path}>
          {name.replace(/\.[^.]+$/, "")}
        </span>
        <div className="mt-1">
          <button
            onClick={onRemove}
            title="移除"
            className="h-6 w-8 rounded-sm text-xs text-fg-muted hover:text-danger hover:bg-bg-hover"
          >
            RM
          </button>
        </div>
      </div>
      <div className="flex-1 relative" style={{ height: PEAKS_HEIGHT }}>
        {loadError ? (
          <div className="absolute inset-0 flex items-center justify-center text-danger gap-2 bg-bg-sidebar/50 rounded-sm px-3">
            <AlertCircle size={12} />
            <span className="text-xs truncate">{loadError}</span>
          </div>
        ) : (
          <div className="absolute inset-0 flex items-center justify-center text-fg-muted gap-2 bg-bg-sidebar/50 rounded-sm">
            <Loader2 size={12} className="animate-spin" />
            <span className="text-xs">加载中…</span>
          </div>
        )}
      </div>
    </div>
  );
}

export function MixConsole({ tracks, onRemove, onMinimize, onClose }: Props) {
  const [engineReady, setEngineReady] = useState(false);
  const engineRef = useRef<MixEngine | null>(null);

  // 加载状态:正在解码的 path → 进行中;失败的 path → 错误信息
  const [loadingPaths, setLoadingPaths] = useState<Set<string>>(new Set());
  const [loadErrors, setLoadErrors] = useState<Map<string, string>>(new Map());

  // 引擎内的 tracks 是 mutable 的(直接改 muted/soloed),组件用一个 rev counter 强制重渲染
  const [, setEngineRev] = useState(0);
  const bumpRev = () => setEngineRev((r) => r + 1);

  // 播放状态
  const [posSec, setPosSec] = useState(0);
  const [maxSec, setMaxSec] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [masterPct, setMasterPct] = useState(100);
  const dark = useDarkTheme();

  // 一次性创建/销毁 engine
  useEffect(() => {
    const engine = new MixEngine();
    engine.setPositionListener((pos, max) => {
      setPosSec(pos);
      setMaxSec(max);
    });
    engineRef.current = engine;
    setEngineReady(true);
    return () => {
      engine.dispose();
      engineRef.current = null;
      setEngineReady(false);
    };
  }, []);

  // 主音量
  useEffect(() => {
    engineRef.current?.setMasterVolume(masterPct / 100);
  }, [masterPct]);

  // tracks prop ↔ engine 同步:加载新增的、移除消失的
  useEffect(() => {
    if (!engineReady) return;
    const engine = engineRef.current;
    if (!engine) return;
    const wantSet = new Set(tracks);
    const currentSet = new Set(engine.getTracks().map((t) => t.path));

    // 1. 移除引擎里不在 want 的轨道
    for (const p of currentSet) {
      if (!wantSet.has(p)) {
        engine.removeTrack(p);
      }
    }
    // 2. 触发新增轨道的加载(未在 currentSet,未在 loadingPaths)
    const toLoad = tracks.filter(
      (p) => !currentSet.has(p) && !loadingPaths.has(p),
    );
    if (toLoad.length === 0) {
      bumpRev();
      return;
    }
    setLoadingPaths((prev) => {
      const n = new Set(prev);
      for (const p of toLoad) n.add(p);
      return n;
    });
    // 清掉之前的错误(如果用户重试同一路径)
    if (toLoad.some((p) => loadErrors.has(p))) {
      setLoadErrors((prev) => {
        const n = new Map(prev);
        for (const p of toLoad) n.delete(p);
        return n;
      });
    }
    let cancelled = false;
    void Promise.all(
      toLoad.map(async (p) => {
        try {
          const url = await rawFileUrl(p);
          await engine.loadTrack(p, url);
          return { path: p, ok: true as const };
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          return { path: p, ok: false as const, error: msg };
        }
      }),
    ).then((results) => {
      if (cancelled) return;
      setLoadingPaths((prev) => {
        const n = new Set(prev);
        for (const r of results) n.delete(r.path);
        return n;
      });
      const failed = results.filter((r) => !r.ok) as Array<{
        path: string;
        ok: false;
        error: string;
      }>;
      if (failed.length > 0) {
        setLoadErrors((prev) => {
          const n = new Map(prev);
          for (const f of failed) n.set(f.path, f.error);
          return n;
        });
      }
      bumpRev();
    });
    return () => {
      cancelled = true;
    };
    // engineReady 加进 deps 是为了首次 mount 后 tracks 重跑;
    // loadingPaths/loadErrors 不放 deps 避免与本身互相触发循环
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tracks, engineReady]);

  const sortedTracks = useMemo<MixTrackData[]>(() => {
    if (!engineReady) return [];
    const engine = engineRef.current;
    if (!engine) return [];
    return [...engine.getTracks()].sort((a, b) => a.name.localeCompare(b.name));
  }, [tracks, loadingPaths, loadErrors, engineReady]);

  // 仍在加载的 / 失败但未被移除的占位行(即在 tracks prop 但还没进 engine 的)
  const placeholders = useMemo(() => {
    if (!engineReady) return [];
    const engine = engineRef.current;
    if (!engine) return [];
    const inEngine = new Set(engine.getTracks().map((t) => t.path));
    return tracks
      .filter((p) => !inEngine.has(p))
      .sort((a, b) => {
        const an = a.split(/[\\/]/).pop() || "";
        const bn = b.split(/[\\/]/).pop() || "";
        return an.localeCompare(bn);
      });
  }, [tracks, loadingPaths, loadErrors, engineReady]);

  const handlePlayPause = () => {
    const engine = engineRef.current;
    if (!engine) return;
    if (playing) {
      engine.pause();
      setPlaying(false);
    } else {
      engine.play();
      setPlaying(engine.getState() === "playing");
    }
  };

  const handleStop = () => {
    const engine = engineRef.current;
    if (!engine) return;
    engine.stop();
    setPlaying(false);
  };

  const handleSeek = (sec: number) => {
    const engine = engineRef.current;
    if (!engine) return;
    engine.seek(sec);
    setPlaying(engine.getState() === "playing");
  };

  const handleMute = (path: string, muted: boolean) => {
    engineRef.current?.setMuted(path, muted);
    bumpRev();
  };

  const handleSolo = (path: string, soloed: boolean) => {
    engineRef.current?.setSoloed(path, soloed);
    bumpRev();
  };

  const noTracks = tracks.length === 0;
  const allMuted =
    sortedTracks.length > 0 && sortedTracks.every((t) => t.muted);

  return (
    <div className="flex flex-col h-full bg-bg">
      {/* 顶栏:无原生标题栏,这里同时担任拖拽区 + 控件 + 自定义窗口按钮。
          drag 区交给整条 bar,所有可点击元素 / 输入框需要 no-drag 否则不响应。 */}
      <div
        className="flex items-center gap-3 px-4 h-11 border-b border-border bg-bg-sidebar shrink-0 select-none"
        style={{ WebkitAppRegion: "drag" } as React.CSSProperties}
      >
        <span className="text-xs font-semibold text-fg-muted tracking-wide uppercase">
          混音台
        </span>
        <span
          className="font-mono text-sm text-fg-muted tabular-nums w-28 ml-2"
          style={{ WebkitAppRegion: "no-drag" } as React.CSSProperties}
        >
          {fmtTime(posSec)} / {fmtTime(maxSec)}
        </span>
        <div
          className="flex items-center gap-1"
          style={{ WebkitAppRegion: "no-drag" } as React.CSSProperties}
        >
          <button
            onClick={handlePlayPause}
            disabled={noTracks || allMuted}
            title={playing ? "暂停" : "播放"}
            className={clsx(
              "h-7 px-3 inline-flex items-center gap-1.5 rounded-sm text-sm",
              "text-fg-muted hover:text-fg hover:bg-bg-hover",
              "disabled:opacity-40 disabled:cursor-not-allowed",
            )}
          >
            {playing ? <Pause size={13} /> : <Play size={13} />}
            <span>{playing ? "暂停" : "播放"}</span>
          </button>
          <button
            onClick={handleStop}
            disabled={noTracks || (!playing && posSec === 0)}
            title="停止"
            className="h-7 px-3 inline-flex items-center gap-1.5 rounded-sm text-sm text-fg-muted hover:text-fg hover:bg-bg-hover disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <Square size={13} />
            <span>停止</span>
          </button>
        </div>
        <span className="flex-1" />
        <div
          className="flex items-center gap-2 text-xs"
          style={{ WebkitAppRegion: "no-drag" } as React.CSSProperties}
        >
          <Volume2 size={14} className="text-fg-muted" />
          <span className="text-fg-subtle">主音量</span>
          <input
            type="range"
            min={0}
            max={200}
            step={1}
            value={masterPct}
            onChange={(e) => setMasterPct(Number(e.target.value))}
            className="w-32 selectable"
          />
          <span className="font-mono text-fg-muted w-10 tabular-nums">
            {masterPct}%
          </span>
        </div>
        <div
          className="flex items-center gap-0.5"
          style={{ WebkitAppRegion: "no-drag" } as React.CSSProperties}
        >
          {onMinimize && (
            <button
              onClick={onMinimize}
              title="最小化(隐藏窗口,保留轨道)"
              className="h-7 w-7 inline-flex items-center justify-center rounded-sm text-fg-muted hover:text-fg hover:bg-bg-hover"
            >
              <Minus size={14} />
            </button>
          )}
          <button
            onClick={onClose}
            title="关闭混音台(清空轨道)"
            className="h-7 w-7 inline-flex items-center justify-center rounded-sm text-fg-muted hover:text-danger hover:bg-bg-hover"
          >
            <X size={14} />
          </button>
        </div>
      </div>

      {/* 轨道列表 */}
      <div className="flex-1 overflow-auto scroll-stable">
        {noTracks ? (
          <div className="flex flex-col items-center justify-center h-full gap-2 text-fg-muted text-sm px-6 text-center">
            <Volume2 size={28} className="text-fg-subtle" />
            <p>混音台为空</p>
            <p className="text-xs text-fg-subtle">
              在文件树右键 WAV 文件或文件夹 → "添加到混音台"
            </p>
          </div>
        ) : (
          <>
            {sortedTracks.map((t) => (
              <TrackRow
                key={t.path}
                track={t}
                posSec={posSec}
                loading={false}
                loadError={loadErrors.get(t.path) ?? null}
                onMute={(m) => handleMute(t.path, m)}
                onSolo={(s) => handleSolo(t.path, s)}
                onRemove={() => onRemove(t.path)}
                onSeek={handleSeek}
                dark={dark}
              />
            ))}
            {placeholders.map((p) => (
              <LoadingPlaceholder
                key={p}
                path={p}
                loadError={loadErrors.get(p) ?? null}
                onRemove={() => onRemove(p)}
              />
            ))}
          </>
        )}
      </div>
    </div>
  );
}
