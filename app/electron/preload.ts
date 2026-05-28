import { contextBridge, ipcRenderer, webUtils } from "electron";

contextBridge.exposeInMainWorld("electronAPI", {
  selectWorkspace: () => ipcRenderer.invoke("dialog:select-workspace"),
  getSidecarUrl: () => ipcRenderer.invoke("sidecar:url"),
  revealInFolder: (path: string) => ipcRenderer.invoke("shell:show-item-in-folder", path),
  openExternal: (url: string) => ipcRenderer.invoke("shell:open-external", url),
  openPath: (path: string) => ipcRenderer.invoke("shell:open-path", path),
  // 把 dataTransfer.files 里的 File 对象解成本地绝对路径(Electron 32+ 的官方做法,
  // 替代之前 contextIsolation 下被禁的 file.path)。OS 拖来的临时 / blob 文件
  // 没有本地路径时返回空字符串。
  getPathForFile: (file: File) => {
    try {
      return webUtils.getPathForFile(file);
    } catch {
      return "";
    }
  },
  fsWatch: (root: string) => ipcRenderer.invoke("fs:watch", root),
  fsUnwatch: () => ipcRenderer.invoke("fs:unwatch"),
  fsPauseWatch: () => ipcRenderer.invoke("fs:pause-watch"),
  fsResumeWatch: (root: string) => ipcRenderer.invoke("fs:resume-watch", root),
  // 读 OS 剪贴板上"复制的文件"列表(Win CF_HDROP / macOS NSFilenamesPboardType);
  // 没文件返回 []。给 Explorer Ctrl+V 跨进程粘贴用。
  clipboardReadFiles: () =>
    ipcRenderer.invoke("clipboard:read-files") as Promise<string[]>,
  // 注意:返回 unsubscribe 函数,在 useEffect cleanup 里调用避免重复订阅
  onFsChanged: (cb: (dirs: string[]) => void) => {
    const listener = (_e: unknown, dirs: string[]) => cb(dirs);
    ipcRenderer.on("fs:changed", listener);
    return () => ipcRenderer.off("fs:changed", listener);
  },
  // 混音台独立窗口控制
  // mixToggle 带 toolbar 按钮的 client rect,主进程动画从该位置展开/收缩
  mixToggle: (rect: { x: number; y: number; w: number; h: number } | null) =>
    ipcRenderer.invoke("mix:toggle", rect),
  mixClose: () => ipcRenderer.invoke("mix:close"),
  mixHide: () => ipcRenderer.invoke("mix:hide"),
  mixAddTracks: (paths: string[]) => ipcRenderer.invoke("mix:add-tracks", paths),
  mixRemoveTrack: (path: string) => ipcRenderer.invoke("mix:remove-track", path),
  mixGetTracks: () => ipcRenderer.invoke("mix:get-tracks"),
  onMixTracksChanged: (cb: (paths: string[]) => void) => {
    const listener = (_e: unknown, paths: string[]) => cb(paths);
    ipcRenderer.on("mix:tracks-changed", listener);
    return () => ipcRenderer.off("mix:tracks-changed", listener);
  },
  onMixVisibilityChanged: (cb: (visible: boolean) => void) => {
    const listener = (_e: unknown, visible: boolean) => cb(visible);
    ipcRenderer.on("mix:visibility-changed", listener);
    return () => ipcRenderer.off("mix:visibility-changed", listener);
  },
  // ---------- agent ----------
  agentSend: (chatId: string, text: string) =>
    ipcRenderer.invoke("agent:send", chatId, text),
  agentStartQc: (chatId: string, song: string) =>
    ipcRenderer.invoke("agent:start-qc", chatId, song),
  agentCancel: (chatId: string) => ipcRenderer.invoke("agent:cancel", chatId),
  agentHydrate: (chatId: string) =>
    ipcRenderer.invoke("agent:hydrate", chatId) as Promise<{
      phase: "A" | "B";
      song: string | null;
      turns: unknown[];
    }>,
  agentHumanCheckResolve: (
    chatId: string,
    payload: { answers: { choice: string; note?: string }[]; cancelled?: boolean },
  ) => ipcRenderer.invoke("agent:human-check-resolve", chatId, payload),
  agentSetWorkspace: (root: string | null) =>
    ipcRenderer.invoke("agent:set-workspace", root),
  agentListSessions: () =>
    ipcRenderer.invoke("agent:list-sessions") as Promise<
      Array<{ id: string; title: string; phase: string | null; song: string | null; updated_at: number }>
    >,
  agentNewSession: (title?: string) =>
    ipcRenderer.invoke("agent:new-session", title) as Promise<{
      id: string;
      title: string;
      phase: string | null;
      song: string | null;
      updated_at: number;
    }>,
  agentRenameSession: (chatId: string, title: string) =>
    ipcRenderer.invoke("agent:rename-session", chatId, title) as Promise<void>,
  agentDeleteSession: (chatId: string) =>
    ipcRenderer.invoke("agent:delete-session", chatId) as Promise<void>,
  onAgentEvent: (cb: (ev: unknown) => void) => {
    const listener = (_e: unknown, ev: unknown) => cb(ev);
    ipcRenderer.on("agent:event", listener);
    return () => ipcRenderer.off("agent:event", listener);
  },
  // ---------- ui tools (agent → renderer) ----------
  // agent.uiTools.openFile / togglePlayback 走 webContents.send 到这,
  // renderer 监听后改 editor state / dispatch CustomEvent。
  onUiOpenFile: (cb: (path: string) => void) => {
    const listener = (_e: unknown, p: string) => cb(p);
    ipcRenderer.on("ui:open-file", listener);
    return () => ipcRenderer.off("ui:open-file", listener);
  },
  onPlaybackToggle: (cb: (kind: "beat" | "structure", on: boolean) => void) => {
    const listener = (_e: unknown, kind: "beat" | "structure", on: boolean) => cb(kind, on);
    ipcRenderer.on("playback:toggle", listener);
    return () => ipcRenderer.off("playback:toggle", listener);
  },
  // ---------- agent dev toggles ----------
  agentGetDumpLlm: () => ipcRenderer.invoke("agent:get-dump-llm") as Promise<boolean>,
  agentSetDumpLlm: (on: boolean) => ipcRenderer.invoke("agent:set-dump-llm", on) as Promise<void>,
});
