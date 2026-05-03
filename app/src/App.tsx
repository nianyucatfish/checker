import { useEffect, useMemo, useRef, useState } from "react";
import { PanelGroup, Panel, PanelResizeHandle } from "react-resizable-panels";
import {
  selectWorkspace,
  listWorkspace,
  checkWorkspace,
  pingSidecar,
} from "./api";
import type { CheckErrorOut } from "./api";

import { Toolbar } from "./components/Toolbar";
import { Explorer } from "./components/Explorer";
import { Center } from "./components/Center";
import { AgentSidebar } from "./components/AgentSidebar";
import { ProblemsPanel } from "./components/ProblemsPanel";
import { StatusBar } from "./components/StatusBar";

const LAST_WORKSPACE_KEY = "audio_qc.last_workspace";

export default function App() {
  // 主题：跟系统
  useEffect(() => {
    const apply = () => {
      const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      document.documentElement.classList.toggle("dark", dark);
    };
    apply();
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, []);

  const [root, setRoot] = useState<string | null>(null);
  const [songs, setSongs] = useState<string[]>([]);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [selectedIsDir, setSelectedIsDir] = useState<boolean>(false);
  const [errorsBySong, setErrorsBySong] = useState<Record<string, CheckErrorOut[]>>({});
  const [scanning, setScanning] = useState(false);
  const [sidecarReady, setSidecarReady] = useState<boolean | null>(null);

  const runScan = async (songsList: string[], rootPath: string) => {
    setScanning(true);
    try {
      const out = await checkWorkspace(rootPath);
      const songPaths = [...songsList].sort((a, b) => b.length - a.length);
      const grouped: Record<string, CheckErrorOut[]> = {};
      Object.entries(out.errors).forEach(([path, errs]) => {
        const owner = songPaths.find(
          (sp) => path === sp || path.startsWith(sp + "\\") || path.startsWith(sp + "/"),
        );
        if (!owner) return;
        grouped[owner] = (grouped[owner] || []).concat(errs);
      });
      setErrorsBySong(grouped);
    } finally {
      setScanning(false);
    }
  };

  const openWorkspace = async (picked: string, autoScan = true) => {
    setRoot(picked);
    setSelectedPath(null);
    setSelectedIsDir(false);
    setErrorsBySong({});
    const out = await listWorkspace(picked);
    setSongs(out.songs);
    localStorage.setItem(LAST_WORKSPACE_KEY, picked);
    if (autoScan) {
      await runScan(out.songs, picked);
    }
  };

  // 启动恢复：先 ping sidecar，OK 后自动打开上次工作区并扫描
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const ok = await pingSidecar();
      if (cancelled) return;
      setSidecarReady(ok);
      if (!ok) return;
      const saved = localStorage.getItem(LAST_WORKSPACE_KEY);
      if (!saved) return;
      try {
        await openWorkspace(saved);
      } catch (e) {
        console.warn("[app] failed to restore workspace:", e);
        localStorage.removeItem(LAST_WORKSPACE_KEY);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handlePick = async () => {
    const picked = await selectWorkspace();
    if (!picked) return;
    await openWorkspace(picked);
  };

  const handleScan = () => {
    if (!root) return;
    return runScan(songs, root);
  };

  const allErrors = useMemo<CheckErrorOut[]>(
    () => Object.values(errorsBySong).flat(),
    [errorsBySong],
  );

  const totalErrors = allErrors.length;

  // 当前选中所属的歌曲（用于 ProblemsPanel "当前歌曲" 模式）
  const selectedSong = useMemo<string | null>(() => {
    if (!selectedPath) return null;
    const songPaths = [...songs].sort((a, b) => b.length - a.length);
    return (
      songPaths.find(
        (sp) => selectedPath === sp || selectedPath.startsWith(sp + "\\") || selectedPath.startsWith(sp + "/"),
      ) || null
    );
  }, [selectedPath, songs]);

  const handleJumpTo = (path: string) => {
    // 启发式：歌曲文件夹/子目录无扩展名，文件有扩展名
    const isDir = songs.includes(path) || !/\.[^.\\/]+$/.test(path);
    setSelectedPath(path);
    setSelectedIsDir(isDir);
  };

  const handleSelect = (path: string, isDir: boolean) => {
    setSelectedPath(path);
    setSelectedIsDir(isDir);
  };

  return (
    <div className="flex flex-col h-screen text-fg bg-bg">
      <Toolbar
        hasWorkspace={!!root}
        scanning={scanning}
        onPickWorkspace={handlePick}
        onScan={handleScan}
      />
      <PanelGroup direction="horizontal" className="flex-1">
        <Panel defaultSize={20} minSize={12} maxSize={40}>
          <Explorer
            root={root}
            songs={songs}
            selected={selectedPath}
            selectedIsDir={selectedIsDir}
            allErrors={allErrors}
            onPickWorkspace={handlePick}
            onSelect={handleSelect}
          />
        </Panel>
        <PanelResizeHandle className="w-px bg-border hover:bg-accent transition-colors data-[resize-handle-active]:bg-accent" />
        <Panel minSize={30}>
          <CenterWithProblemsDrawer
            selectedPath={selectedPath}
            selectedIsDir={selectedIsDir}
            errorsBySong={errorsBySong}
            selectedSong={selectedSong}
            onJumpTo={handleJumpTo}
          />
        </Panel>
        <PanelResizeHandle className="w-px bg-border hover:bg-accent transition-colors data-[resize-handle-active]:bg-accent" />
        <Panel defaultSize={22} minSize={15} maxSize={45}>
          <AgentSidebar />
        </Panel>
      </PanelGroup>
      <StatusBar
        sidecarReady={sidecarReady}
        songCount={songs.length}
        errorCount={totalErrors}
      />
    </div>
  );
}

const PROBLEMS_HEIGHT_KEY = "audio_qc.problems_height";
const PROBLEMS_MIN_PX = 80;

function CenterWithProblemsDrawer({
  selectedPath,
  selectedIsDir,
  errorsBySong,
  selectedSong,
  onJumpTo,
}: {
  selectedPath: string | null;
  selectedIsDir: boolean;
  errorsBySong: Record<string, CheckErrorOut[]>;
  selectedSong: string | null;
  onJumpTo: (path: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [drawerHeight, setDrawerHeight] = useState<number>(() => {
    const saved = Number(localStorage.getItem(PROBLEMS_HEIGHT_KEY));
    return Number.isFinite(saved) && saved >= PROBLEMS_MIN_PX ? saved : 220;
  });
  const dragRef = useRef<{ startY: number; startH: number } | null>(null);

  const onResizeStart = (e: React.MouseEvent) => {
    e.preventDefault();
    dragRef.current = { startY: e.clientY, startH: drawerHeight };
    const onMove = (ev: MouseEvent) => {
      if (!dragRef.current) return;
      const containerH = containerRef.current?.clientHeight ?? 0;
      const dy = ev.clientY - dragRef.current.startY;
      // 拖把手向上 → drawer 变大;向下 → drawer 变小
      const next = dragRef.current.startH - dy;
      const max = Math.max(PROBLEMS_MIN_PX, containerH - 100);
      const clamped = Math.max(PROBLEMS_MIN_PX, Math.min(max, next));
      setDrawerHeight(clamped);
    };
    const onUp = () => {
      dragRef.current = null;
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      localStorage.setItem(PROBLEMS_HEIGHT_KEY, String(drawerHeight));
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    document.body.style.cursor = "ns-resize";
  };

  // 把手 release 时持久化最新值
  useEffect(() => {
    localStorage.setItem(PROBLEMS_HEIGHT_KEY, String(drawerHeight));
  }, [drawerHeight]);

  return (
    <div ref={containerRef} className="relative h-full">
      {/* Center 占据 drawer 之上的可视区,scrollbar 自动落在可见区内 */}
      <div
        className="absolute top-0 left-0 right-0"
        style={{ bottom: `${drawerHeight}px` }}
      >
        <Center selectedPath={selectedPath} selectedIsDir={selectedIsDir} />
      </div>
      {/* ProblemsPanel 抽屉:贴底,顶部一根可拖把手 */}
      <div
        className="absolute left-0 right-0 bottom-0 flex flex-col"
        style={{ height: `${drawerHeight}px` }}
      >
        <div
          onMouseDown={onResizeStart}
          className="h-1 -mb-px shrink-0 cursor-ns-resize bg-border hover:bg-accent transition-colors"
          title="拖动调整问题面板高度"
        />
        <div className="flex-1 min-h-0">
          <ProblemsPanel
            errorsBySong={errorsBySong}
            selectedSong={selectedSong}
            onJumpTo={onJumpTo}
          />
        </div>
      </div>
    </div>
  );
}
