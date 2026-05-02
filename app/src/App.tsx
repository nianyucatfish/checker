import { useEffect, useMemo, useState } from "react";
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
      <PanelGroup direction="vertical" className="flex-1">
        <Panel minSize={30}>
          <PanelGroup direction="horizontal" className="h-full">
            <Panel defaultSize={20} minSize={12} maxSize={40}>
              <Explorer
                root={root}
                songs={songs}
                selected={selectedPath}
                allErrors={allErrors}
                onPickWorkspace={handlePick}
                onSelect={handleSelect}
              />
            </Panel>
            <PanelResizeHandle className="w-px bg-border hover:bg-accent transition-colors data-[resize-handle-active]:bg-accent" />
            <Panel minSize={30}>
              <Center selectedPath={selectedPath} selectedIsDir={selectedIsDir} />
            </Panel>
            <PanelResizeHandle className="w-px bg-border hover:bg-accent transition-colors data-[resize-handle-active]:bg-accent" />
            <Panel defaultSize={22} minSize={15} maxSize={45}>
              <AgentSidebar />
            </Panel>
          </PanelGroup>
        </Panel>
        <PanelResizeHandle className="h-px bg-border hover:bg-accent transition-colors data-[resize-handle-active]:bg-accent" />
        <Panel defaultSize={25} minSize={10} maxSize={60}>
          <ProblemsPanel
            errorsBySong={errorsBySong}
            selectedSong={selectedSong}
            onJumpTo={handleJumpTo}
          />
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
