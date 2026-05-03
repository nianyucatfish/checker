import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("electronAPI", {
  selectWorkspace: () => ipcRenderer.invoke("dialog:select-workspace"),
  getSidecarUrl: () => ipcRenderer.invoke("sidecar:url"),
  revealInFolder: (path: string) => ipcRenderer.invoke("shell:show-item-in-folder", path),
});
