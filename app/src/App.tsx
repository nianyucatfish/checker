import { useEffect, useState } from "react";
import { selectWorkspace, listWorkspace, checkWorkspace, pingSidecar } from "./api";
import type { CheckErrorOut } from "./api";

interface SongState {
  path: string;
  errorCount: number;
}

export default function App() {
  const [root, setRoot] = useState<string | null>(null);
  const [songs, setSongs] = useState<SongState[]>([]);
  const [selectedSong, setSelectedSong] = useState<string | null>(null);
  const [errorsBySong, setErrorsBySong] = useState<Record<string, CheckErrorOut[]>>({});
  const [scanning, setScanning] = useState(false);
  const [sidecarReady, setSidecarReady] = useState<boolean | null>(null);

  // ping sidecar on boot
  useEffect(() => {
    pingSidecar().then(setSidecarReady);
  }, []);

  const handlePickWorkspace = async () => {
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
      // 按歌聚合：每个 song folder 路径下所有 error path 计入它的 errorCount
      const counts: Record<string, number> = {};
      const grouped: Record<string, CheckErrorOut[]> = {};
      const songPaths = songs.map((s) => s.path).sort((a, b) => b.length - a.length);
      Object.entries(out.errors).forEach(([path, errs]) => {
        const owner = songPaths.find((sp) => path === sp || path.startsWith(sp + "\\") || path.startsWith(sp + "/"));
        if (!owner) return;
        counts[owner] = (counts[owner] || 0) + errs.length;
        grouped[owner] = (grouped[owner] || []).concat(errs);
      });
      setSongs((prev) => prev.map((s) => ({ ...s, errorCount: counts[s.path] || 0 })));
      setErrorsBySong(grouped);
    } finally {
      setScanning(false);
    }
  };

  return (
    <div className="app-shell">
      <aside className="pane">
        <div className="pane-header">
          {root ? root.split(/[\/]/).pop() : "未选择工作区"}
        </div>
        <div className="pane-body">
          <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
            <button onClick={handlePickWorkspace}>选择工作区</button>
            {root && (
              <button onClick={handleScan} disabled={scanning}>
                {scanning ? "扫描中..." : "全量扫描"}
              </button>
            )}
          </div>
          {songs.length === 0 && root && <div className="placeholder">无歌曲</div>}
          {songs.map((song) => (
            <div
              key={song.path}
              className={`song-row${song.errorCount > 0 ? " has-errors" : ""}${
                selectedSong === song.path ? " selected" : ""
              }`}
              onClick={() => setSelectedSong(song.path)}
              style={selectedSong === song.path ? { background: "rgba(127,127,127,0.15)" } : undefined}
            >
              <span>{song.path.split(/[\/]/).pop()}</span>
              {song.errorCount > 0 && <span className="error-badge">{song.errorCount}</span>}
            </div>
          ))}
        </div>
      </aside>

      <main className="pane">
        <div className="pane-header">
          {selectedSong ? selectedSong.split(/[\/]/).pop() : "中央工作区"}
        </div>
        <div className="pane-body">
          {!selectedSong && <div className="placeholder">在左侧选择一首歌</div>}
          {selectedSong && (
            <ErrorList errors={errorsBySong[selectedSong] || []} />
          )}
        </div>
      </main>

      <aside className="pane">
        <div className="pane-header">Agent 聊天侧栏</div>
        <div className="pane-body">
          <div className="placeholder">
            <div style={{ textAlign: "center" }}>
              <p>(Phase 4 接入)</p>
              <p style={{ fontSize: 11, marginTop: 8 }}>
                Sidecar:{" "}
                {sidecarReady === null
                  ? "检测中..."
                  : sidecarReady
                  ? "✓ 在线"
                  : "✗ 离线"}
              </p>
            </div>
          </div>
        </div>
      </aside>
    </div>
  );
}

function ErrorList({ errors }: { errors: CheckErrorOut[] }) {
  if (errors.length === 0) {
    return <div className="placeholder">该歌曲无错误（或还未扫描）</div>;
  }
  return (
    <div>
      <div style={{ marginBottom: 8, color: "#d32f2f", fontWeight: 500 }}>
        {errors.length} 处问题
      </div>
      {errors.map((e, i) => (
        <div
          key={i}
          style={{
            padding: 8,
            marginBottom: 4,
            border: "1px solid rgba(127,127,127,0.2)",
            borderRadius: 4,
          }}
        >
          <div style={{ fontWeight: 500 }}>
            <code style={{ fontSize: 11, color: "rgba(127,127,127,0.8)" }}>{e.code}</code>{" "}
            {e.message}
          </div>
          <div style={{ fontSize: 11, color: "rgba(127,127,127,0.7)", marginTop: 2 }}>
            {e.path.split(/[\/]/).slice(-2).join("/")}
          </div>
        </div>
      ))}
    </div>
  );
}
