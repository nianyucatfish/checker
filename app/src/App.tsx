import { useEffect, useMemo, useState } from "react";
import { Group, Panel, Separator } from "react-resizable-panels";
import {
  selectWorkspace,
  listWorkspace,
  checkWorkspace,
  pingSidecar,
} from "./api";
import type { CheckErrorOut } from "./api";

import { ActivityBar, type ActivityView } from "./components/ActivityBar";
import { Explorer, type SongState } from "./components/Explorer";
import { Center } from "./components/Center";
import { AgentSidebar } from "./components/AgentSidebar";
import { StatusBar } from "./components/StatusBar";

export default function App() {
  // 主题（先按系统）；Phase 后续做切换
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

  const [activity, setActivity] = useState<ActivityView>("explorer");
  const [root, setRoot] = useState<string | null>(null);
  const [songs, setSongs] = useState<SongState[]>([]);
  const [selectedSong, setSelectedSong] = useState<string | null>(null);
  const [errorsBySong, setErrorsBySong] = useState<Record<string, CheckErrorOut[]>>(
    {}
  );
  const [scanning, setScanning] = useState(false);
  const [sidecarReady, setSidecarReady] = useState<boolean | null>(null);

  useEffect(() => {
    pingSidecar().then(setSidecarReady);
  }, []);

  const handlePick = async () => {
    const picked = await selectWorkspace();
    if (!picked) return;
    setRoot(picked);
    setSelectedSong(null);
    setErrorsBySong({});
    const out = await listWorkspace(picked);
    setSongs(out.songs.map((p) => ({ path: p, errorCount: 0 })));
  };

  const handleScan = async () => {
    if (!root) return;
    setScanning(true);
    try {
      const out = await checkWorkspace(root);
      const counts: Record<string, number> = {};
      const grouped: Record<string, CheckErrorOut[]> = {};
      const songPaths = songs
        .map((s) => s.path)
        .sort((a, b) => b.length - a.length);
      Object.entries(out.errors).forEach(([path, errs]) => {
        const owner = songPaths.find(
          (sp) =>
            path === sp || path.startsWith(sp + "\\") || path.startsWith(sp + "/")
        );
        if (!owner) return;
        counts[owner] = (counts[owner] || 0) + errs.length;
        grouped[owner] = (grouped[owner] || []).concat(errs);
      });
      setSongs((prev) =>
        prev.map((s) => ({ ...s, errorCount: counts[s.path] || 0 }))
      );
      setErrorsBySong(grouped);
    } finally {
      setScanning(false);
    }
  };

  const totalErrors = useMemo(
    () => songs.reduce((acc, s) => acc + s.errorCount, 0),
    [songs]
  );

  return (
    <div className="flex flex-col h-screen">
      <div className="flex flex-1 min-h-0">
        <ActivityBar
          current={activity}
          onChange={setActivity}
          sidecarReady={sidecarReady}
        />
        <Group orientation="horizontal" className="flex-1 flex">
          <Panel defaultSize={20} minSize={12} maxSize={40}>
            {activity === "explorer" && (
              <Explorer
                root={root}
                songs={songs}
                selected={selectedSong}
                scanning={scanning}
                onPickWorkspace={handlePick}
                onScan={handleScan}
                onSelect={setSelectedSong}
              />
            )}
            {activity === "agent" && <AgentSidebar />}
            {activity === "settings" && (
              <div className="pane">
                <div className="pane-header">设置</div>
                <div className="pane-body px-3 py-2 text-fg-muted">
                  设置面板待实现
                </div>
              </div>
            )}
          </Panel>
          <Separator className="w-px bg-border hover:bg-accent transition-colors cursor-col-resize" />
          <Panel minSize={30}>
            <Center
              selected={selectedSong}
              errors={selectedSong ? errorsBySong[selectedSong] || [] : []}
            />
          </Panel>
          <Separator className="w-px bg-border hover:bg-accent transition-colors cursor-col-resize" />
          <Panel defaultSize={22} minSize={15} maxSize={45}>
            <AgentSidebar />
          </Panel>
        </Group>
      </div>
      <StatusBar
        sidecarReady={sidecarReady}
        songCount={songs.length}
        errorCount={totalErrors}
      />
    </div>
  );
}
