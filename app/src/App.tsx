import { useEffect, useMemo, useRef, useState } from "react";
import { PanelGroup, Panel, PanelResizeHandle } from "react-resizable-panels";
import { Loader2 } from "lucide-react";
import {
  selectWorkspace,
  listWorkspace,
  listDir,
  checkWorkspace,
  pingSidecar,
  proposeRenames,
  applyRenames,
  padSongToLongest,
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
  // selectedPath:左键命中的"行"(可以是 dir 也可以是 file),用于:
  //   - Explorer 内部 selectedSet 同步 + 自动展开祖先
  //   - ProblemsPanel 派生 selectedSong(当前歌曲过滤)
  // editorPath:Center 显示的"文件",仅在选中 file 时更新。点 dir 不动,这样
  //   左键展开目录不会把编辑器切走。
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [selectedIsDir, setSelectedIsDir] = useState<boolean>(false);
  const [editorPath, setEditorPath] = useState<string | null>(null);
  const [errorsBySong, setErrorsBySong] = useState<Record<string, CheckErrorOut[]>>({});
  const [scanning, setScanning] = useState(false);
  const [applying, setApplying] = useState(false);
  const [applyTask, setApplyTask] = useState<{
    label: string;
    current: number;
    total: number;
    detail?: string;
  } | null>(null);
  const [treeRefreshKey, setTreeRefreshKey] = useState(0);
  const [mixConsoleOpen, setMixConsoleOpen] = useState(false);
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
    setEditorPath(null);
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

  // 监听外部文件系统变化(对应工作区目录),工作区切换时自动迁移监听目标
  useEffect(() => {
    if (!root) {
      window.electronAPI.fsUnwatch().catch(() => {});
      return;
    }
    window.electronAPI.fsWatch(root).catch((e) => {
      console.warn("[app] fs watch failed:", e);
    });
    return () => {
      window.electronAPI.fsUnwatch().catch(() => {});
    };
  }, [root]);

  const handlePick = async () => {
    const picked = await selectWorkspace();
    if (!picked) return;
    await openWorkspace(picked);
  };

  const handleScan = () => {
    if (!root) return;
    return runScan(songs, root);
  };

  // Explorer 内部写操作(rename / delete / paste)完成后回调,
  // 用来触发错误同步:重新拉 songs + 重扫,保证错误列表与文件系统不脱节。
  const handleWorkspaceMutated = async () => {
    if (!root) return;
    try {
      const out = await listWorkspace(root);
      setSongs(out.songs);
      await runScan(out.songs, root);
    } catch (e) {
      console.warn("[app] mutation re-scan failed:", e);
    }
  };

  const handleToggleMixConsole = (rect: { x: number; y: number; w: number; h: number }) => {
    // 乐观更新 toolbar 高亮态;主进程动画完成后会通过 visibility-changed 回正
    setMixConsoleOpen((v) => !v);
    void window.electronAPI.mixToggle(rect);
  };

  // 右键"添加到混音台":主进程是 tracks 真值,本端只发 IPC,主进程会自动开窗 + 广播
  const handleAddToMix = (paths: string[]) => {
    if (paths.length === 0) return;
    void window.electronAPI.mixAddTracks(paths);
    setMixConsoleOpen(true);
  };

  // 右键"添加文件夹到混音台":展开 wavs 后批量加
  const handleAddFolderToMix = async (folderPath: string) => {
    try {
      const out = await listDir(folderPath);
      const wavs = out.entries
        .filter((e) => !e.is_dir && e.ext.toLowerCase() === "wav")
        .map((e) => e.path);
      if (wavs.length === 0) {
        alert(`目录 ${folderPath} 下没有 WAV 文件`);
        return;
      }
      handleAddToMix(wavs);
    } catch (e) {
      alert(`列举目录失败: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  // 主进程对混音窗可见性的权威通知,覆盖乐观更新
  useEffect(() => {
    const off = window.electronAPI.onMixVisibilityChanged((visible) => {
      setMixConsoleOpen(visible);
    });
    return () => off();
  }, []);

  // 工作区批量自动修复命名:对所有歌依次 propose,合并 ops,统一 apply。
  // 注意:这个 handler 只通过命令面板/agent 调用;工具栏没有按钮直接触发批量。
  // 单首歌的修复在右键菜单(handleAutofixSong)。

  // 单首歌(右键菜单触发)
  const handleAutofixSong = async (songPath: string) => {
    if (applying) return;
    setApplying(true);
    const name = songPath.split(/[\\/]/).pop() || "";
    setApplyTask({ label: "自动修复命名", current: 0, total: 1, detail: name });
    try {
      const r = await proposeRenames(songPath);
      if (r.ops.length === 0) {
        alert(
          r.conflicts.length > 0
            ? `没有可自动修复的命名;冲突 ${r.conflicts.length} 项`
            : "没有需要修复的命名",
        );
        return;
      }
      setApplyTask({
        label: "自动修复命名",
        current: 0,
        total: 1,
        detail: `应用 ${r.ops.length} 项重命名...`,
      });
      const ar = await applyRenames(r.ops);
      const msg = [
        `自动修复 "${name}": ${ar.executed.length}/${r.ops.length} 项`,
        ar.errors.length > 0 ? `\n错误:\n${ar.errors.slice(0, 5).join("\n")}` : "",
      ].join("");
      alert(msg);
      if (root) {
        const out = await listWorkspace(root);
        setSongs(out.songs);
        await runScan(out.songs, root);
        setTreeRefreshKey((k) => k + 1);
      }
    } finally {
      setApplying(false);
      setApplyTask(null);
    }
  };

  const handlePadSong = async (songPath: string) => {
    if (applying) return;
    setApplying(true);
    const name = songPath.split(/[\\/]/).pop() || "";
    setApplyTask({ label: "统一时长", current: 0, total: 1, detail: name });
    try {
      const r = await padSongToLongest(songPath);
      if (!r.ok) {
        alert(`补静音失败 (${name}): ${r.error || "未知错误"}`);
      } else {
        alert(`已补静音 ${r.padded} 个 WAV (${name})`);
      }
      if (root) {
        await runScan(songs, root);
        setTreeRefreshKey((k) => k + 1);
      }
    } finally {
      setApplying(false);
      setApplyTask(null);
    }
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
    if (!isDir) setEditorPath(path);
  };

  const handleSelect = (path: string, isDir: boolean) => {
    setSelectedPath(path);
    setSelectedIsDir(isDir);
    // 仅 file 命中才切 Center;dir 命中保留之前文件
    if (!isDir) setEditorPath(path);
  };

  return (
    <div className="flex flex-col h-screen text-fg bg-bg">
      <Toolbar
        hasWorkspace={!!root}
        scanning={scanning}
        applying={applying}
        rootDir={root}
        mixConsoleOpen={mixConsoleOpen}
        onPickWorkspace={handlePick}
        onScan={handleScan}
        onToggleMixConsole={handleToggleMixConsole}
      />
      <PanelGroup direction="horizontal" className="flex-1">
        <Panel defaultSize={20} minSize={12} maxSize={40}>
          <Explorer
            root={root}
            songs={songs}
            selected={selectedPath}
            selectedIsDir={selectedIsDir}
            allErrors={allErrors}
            refreshKey={treeRefreshKey}
            onPickWorkspace={handlePick}
            onSelect={handleSelect}
            onAutofixSong={handleAutofixSong}
            onPadSong={handlePadSong}
            onMutated={handleWorkspaceMutated}
            onAddToMixConsole={handleAddToMix}
            onAddFolderToMixConsole={handleAddFolderToMix}
          />
        </Panel>
        <PanelResizeHandle className="w-px bg-border hover:bg-accent transition-colors data-[resize-handle-active]:bg-accent" />
        <Panel minSize={30}>
          <CenterWithProblemsDrawer
            editorPath={editorPath}
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
      {applyTask && (
        <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center">
          <div className="bg-bg-sidebar border border-border rounded-md p-6 flex flex-col items-center gap-3 min-w-[320px] max-w-[480px]">
            <Loader2 size={28} className="animate-spin text-accent" />
            <div className="text-sm text-fg font-medium">{applyTask.label}</div>
            <div className="text-xs text-fg-muted font-mono">
              {applyTask.current} / {applyTask.total}
            </div>
            {applyTask.detail && (
              <div className="text-xs text-fg-subtle text-center break-all max-w-md">
                {applyTask.detail}
              </div>
            )}
            <div className="w-full h-1 bg-bg rounded overflow-hidden">
              <div
                className="h-full bg-accent transition-all duration-200"
                style={{
                  width: `${
                    applyTask.total === 0
                      ? 0
                      : Math.round((applyTask.current / applyTask.total) * 100)
                  }%`,
                }}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const PROBLEMS_HEIGHT_KEY = "audio_qc.problems_height";
const PROBLEMS_MIN_PX = 80;

function CenterWithProblemsDrawer({
  editorPath,
  errorsBySong,
  selectedSong,
  onJumpTo,
}: {
  editorPath: string | null;
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
        <Center selectedPath={editorPath} selectedIsDir={false} />
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
