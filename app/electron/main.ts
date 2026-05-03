// Electron main process: spawn sidecar, manage window lifecycle.

import { app, BrowserWindow, dialog, ipcMain, shell } from "electron";
import { spawn, ChildProcess } from "node:child_process";
import * as path from "node:path";
import * as net from "node:net";
import chokidar, { FSWatcher } from "chokidar";

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

// 在系统资源管理器/Finder 中选中并显示文件
ipcMain.handle("shell:show-item-in-folder", (_e, p: string) => {
  if (typeof p === "string" && p) shell.showItemInFolder(p);
});

// 在系统默认浏览器/Finder 中打开外部链接 / 文件夹
ipcMain.handle("shell:open-external", async (_e, url: string) => {
  if (typeof url === "string" && url) await shell.openExternal(url);
});

// 在系统默认应用中打开文件 / 文件夹(用于"在资源管理器中显示根目录"这类)
ipcMain.handle("shell:open-path", async (_e, p: string) => {
  if (typeof p === "string" && p) await shell.openPath(p);
});

// ---------- 文件系统监听(外部修改同步) ----------
// 用 chokidar 监视当前工作区目录的递归变化,300ms 节流批量推送 fs:changed 事件。
// 渲染进程接到事件后:
//   - 对已缓存目录调 listDir 重拉
//   - 调 onMutated 触发 workspace 重扫(错误同步)
let fsWatcher: FSWatcher | null = null;
let watcherTarget: BrowserWindow | null = null;
const pendingFsDirs = new Set<string>();
let flushTimer: NodeJS.Timeout | null = null;

function flushFsChanges() {
  flushTimer = null;
  if (pendingFsDirs.size === 0) return;
  if (!watcherTarget || watcherTarget.isDestroyed()) {
    pendingFsDirs.clear();
    return;
  }
  const dirs = Array.from(pendingFsDirs);
  pendingFsDirs.clear();
  try {
    watcherTarget.webContents.send("fs:changed", dirs);
  } catch (e) {
    console.warn("[fs-watch] send failed:", e);
  }
}

function scheduleFsFlush() {
  if (flushTimer) return;
  flushTimer = setTimeout(flushFsChanges, 300);
}

function stopFsWatcher() {
  if (flushTimer) {
    clearTimeout(flushTimer);
    flushTimer = null;
  }
  pendingFsDirs.clear();
  if (fsWatcher) {
    fsWatcher.close().catch(() => {});
    fsWatcher = null;
  }
  watcherTarget = null;
}

function startFsWatcher(rootPath: string, win: BrowserWindow) {
  stopFsWatcher();
  watcherTarget = win;
  // ignoreInitial: 启动时不为已存在文件触发 add 事件(避免初次扫描风暴)
  // awaitWriteFinish: 大文件复制时等文件写完再触发,避免读到不完整的文件
  fsWatcher = chokidar.watch(rootPath, {
    ignored: [
      /(^|[\\/])\../, // 隐藏文件 (.git / .DS_Store / .vscode 等)
      /(^|[\\/])(Thumbs\.db|desktop\.ini)$/i,
      /\.(bak|tmp|swp|crdownload|part)$/i,
    ],
    ignoreInitial: true,
    persistent: true,
    awaitWriteFinish: {
      stabilityThreshold: 250,
      pollInterval: 100,
    },
  });

  fsWatcher.on("all", (event, p) => {
    // event ∈ {add, change, unlink, addDir, unlinkDir}
    // 改了哪个父目录就把它入队;dir 增删时祖父也算改了(影响其父的列表)
    const parent = path.dirname(p);
    pendingFsDirs.add(parent);
    if (event === "addDir" || event === "unlinkDir") {
      pendingFsDirs.add(path.dirname(parent));
    }
    scheduleFsFlush();
  });

  fsWatcher.on("error", (e) => {
    console.warn("[fs-watch] error:", e);
  });
}

ipcMain.handle("fs:watch", (e, rootPath: string) => {
  if (typeof rootPath !== "string" || !rootPath) return;
  const win = BrowserWindow.fromWebContents(e.sender);
  if (!win) return;
  startFsWatcher(rootPath, win);
});

ipcMain.handle("fs:unwatch", () => {
  stopFsWatcher();
});

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
  stopFsWatcher();
  if (sidecarProc && !sidecarProc.killed) sidecarProc.kill();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  stopFsWatcher();
  if (sidecarProc && !sidecarProc.killed) sidecarProc.kill();
});
