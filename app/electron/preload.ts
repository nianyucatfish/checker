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
});
