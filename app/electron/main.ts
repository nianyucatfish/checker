// Electron main process: spawn sidecar, manage window lifecycle.

import { app, BrowserWindow, dialog, ipcMain } from "electron";
import { spawn, ChildProcess } from "node:child_process";
import * as path from "node:path";
import * as net from "node:net";

const SIDECAR_PORT = 8765; // TODO Phase 5: pick random free port

let sidecarProc: ChildProcess | null = null;

async function waitForPort(port: number, timeoutMs = 10000): Promise<void> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const ok = await new Promise<boolean>((resolve) => {
      const sock = new net.Socket();
      sock.setTimeout(500);
      sock.once("connect", () => {
        sock.destroy();
        resolve(true);
      });
      sock.once("error", () => resolve(false));
      sock.once("timeout", () => {
        sock.destroy();
        resolve(false);
      });
      sock.connect(port, "127.0.0.1");
    });
    if (ok) return;
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error(`sidecar didn't come up on port ${port} within ${timeoutMs}ms`);
}

function spawnSidecar() {
  // 开发期：直接 venv/Scripts/python.exe -m sidecar.serve
  // 打包后：bundled python; TODO Phase 5
  const projectRoot = path.resolve(__dirname, "..", "..");
  const py = process.platform === "win32"
    ? path.join(projectRoot, "venv", "Scripts", "python.exe")
    : path.join(projectRoot, "venv", "bin", "python");
  sidecarProc = spawn(py, ["-X", "utf8", "-m", "sidecar.serve", "--port", String(SIDECAR_PORT)], {
    cwd: projectRoot,
    env: { ...process.env, PYTHONIOENCODING: "utf-8" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  sidecarProc.stdout?.on("data", (b) => process.stdout.write(`[sidecar] ${b}`));
  sidecarProc.stderr?.on("data", (b) => process.stderr.write(`[sidecar] ${b}`));
  sidecarProc.on("exit", (code) => console.log(`[sidecar] exited with ${code}`));
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      webviewTag: true,
    },
  });

  if (process.env.VITE_DEV_SERVER_URL) {
    win.loadURL(process.env.VITE_DEV_SERVER_URL);
    if (process.env.OPEN_DEVTOOLS === "1") win.webContents.openDevTools();
  } else {
    win.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  }
}

ipcMain.handle("dialog:select-workspace", async () => {
  const result = await dialog.showOpenDialog({
    properties: ["openDirectory"],
    title: "选择工作区文件夹",
  });
  if (result.canceled || result.filePaths.length === 0) return null;
  return result.filePaths[0];
});

ipcMain.handle("sidecar:url", () => `http://127.0.0.1:${SIDECAR_PORT}`);

app.whenReady().then(async () => {
  spawnSidecar();
  try {
    await waitForPort(SIDECAR_PORT);
    console.log(`[main] sidecar ready on ${SIDECAR_PORT}`);
  } catch (e) {
    console.error(`[main] ${e}`);
  }
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

console.log("[main] window-all-closed registered");
app.on("window-all-closed", () => {
  if (sidecarProc && !sidecarProc.killed) sidecarProc.kill();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  if (sidecarProc && !sidecarProc.killed) sidecarProc.kill();
});
