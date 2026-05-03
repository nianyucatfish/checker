import { useEffect, useState } from "react";
import { MixConsole } from "./components/MixConsole";

// 混音台独立窗口的根组件:
// - 拉初始 tracks(主进程持有)
// - 订阅 mix:tracks-changed → 跟主窗口/其他渲染端的添加保持同步
// - 用户在 UI 里按 RM 移除 → IPC mix:remove-track,主进程更新后回播事件
// - 关闭按钮 → IPC mix:close,主进程关掉本窗口(进而清空 tracks 通知主窗口)
export function MixConsoleStandalone() {
  const [tracks, setTracks] = useState<string[]>([]);
  const [bootstrapped, setBootstrapped] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void window.electronAPI.mixGetTracks().then((paths) => {
      if (cancelled) return;
      setTracks(paths);
      setBootstrapped(true);
    });
    const off = window.electronAPI.onMixTracksChanged((paths) => {
      setTracks(paths);
    });
    return () => {
      cancelled = true;
      off();
    };
  }, []);

  const handleRemove = (path: string) => {
    void window.electronAPI.mixRemoveTrack(path);
  };

  const handleClose = () => {
    void window.electronAPI.mixClose();
  };

  const handleMinimize = () => {
    void window.electronAPI.mixHide();
  };

  // 系统主题跟随
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

  if (!bootstrapped) {
    return (
      <div className="h-screen w-screen flex items-center justify-center bg-bg text-fg-muted">
        <span className="text-sm">正在初始化…</span>
      </div>
    );
  }

  return (
    <div className="h-screen w-screen flex flex-col bg-bg text-fg overflow-hidden">
      <MixConsole
        tracks={tracks}
        onRemove={handleRemove}
        onMinimize={handleMinimize}
        onClose={handleClose}
      />
    </div>
  );
}
