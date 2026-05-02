import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("electronAPI", {
  selectWorkspace: () => ipcRenderer.invoke("dialog:select-workspace"),
  getSidecarUrl: () => ipcRenderer.invoke("sidecar:url"),
});
