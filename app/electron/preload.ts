import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("electronAPI", {
  selectWorkspace: () => ipcRenderer.invoke("dialog:select-workspace"),
  getSidecarUrl: () => ipcRenderer.invoke("sidecar:url"),
  revealInFolder: (path: string) => ipcRenderer.invoke("shell:show-item-in-folder", path),
  openExternal: (url: string) => ipcRenderer.invoke("shell:open-external", url),
  openPath: (path: string) => ipcRenderer.invoke("shell:open-path", path),
  fsWatch: (root: string) => ipcRenderer.invoke("fs:watch", root),
  fsUnwatch: () => ipcRenderer.invoke("fs:unwatch"),
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
});
