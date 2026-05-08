// Electron main process: spawn sidecar, manage window lifecycle.

import { app, BrowserWindow, clipboard, dialog, ipcMain, Menu, shell } from "electron";
import { spawn, ChildProcess } from "node:child_process";
import * as path from "node:path";
import * as net from "node:net";
import chokidar, { FSWatcher } from "chokidar";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { initDb, closeDb } from "./db";

const SIDECAR_PORT = 8765; // TODO Phase 5: pick random free port

// 这是个工具型应用,不需要 Electron 默认的 File / Edit / View / Window / Help 菜单。
// 在所有窗口创建之前就置空,主窗和 mix 窗都不会再画菜单条。
Menu.setApplicationMenu(null);

let sidecarProc: ChildProcess | null = null;
let mainWindow: BrowserWindow | null = null;
let mixWindow: BrowserWindow | null = null;
const mixTracks = new Set<string>();
// 上次可见时的 bounds(用户拖到哪 / 改成多大)。下次显示从这恢复。
let lastVisibleMixBounds: Electron.Rectangle | null = null;
// 最近一次 toolbar 按钮的屏幕坐标。给 minimize / X / 内部关闭这些没 rect 的入口
// 也能跟着 toolbar 按钮做收缩动画。
let lastToolbarButtonRect: ButtonRect | null = null;
// 动画中标志,防止用户狂点 toolbar 触发并行动画
let mixAnimating = false;
// before-quit 时设 true,允许 mix 窗的 close 事件真正销毁(否则 preventDefault 拦下)
let isAppQuitting = false;

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


// ============================================================
//  MCP client (Phase 4: agent 工具通道)
//
//  跟 FastAPI sidecar 并行存在,各管各的:
//    - FastAPI(SIDECAR_PORT 8765):renderer 走 HTTP 调 /tools/* /dev/*
//    - MCP server (stdio 子进程):agent 通过 MCP 协议调工具
//
//  本切片只做"连通性证明":拉子进程、连上、列出工具、打日志,不接 UI / agent。
//  后续切片把 mcpClient 暴露给 IPC handler 供未来 agent loop 使用。
// ============================================================

// MCP client。子进程由 StdioClientTransport 内部 spawn,我们不握 ChildProcess
// 句柄;close() 会关 transport 并把子进程一并带走。
let mcpClient: Client | null = null;

async function startMcpClient(): Promise<void> {
  const projectRoot = path.resolve(__dirname, "..", "..");
  const py = process.platform === "win32"
    ? path.join(projectRoot, "venv", "Scripts", "python.exe")
    : path.join(projectRoot, "venv", "bin", "python");

  // StdioClientTransport 自己 spawn 子进程并接管 stdin/stdout 做 JSON-RPC,
  // 我们不另起 spawn —— 否则 transport 拿不到正确的句柄。但子进程的 stderr
  // 仍能被父进程拿到(SDK 默认透传 stderr),用来打 sidecar 内部 log。
  const transport = new StdioClientTransport({
    command: py,
    args: ["-X", "utf8", "-m", "sidecar.mcp_server"],
    cwd: projectRoot,
    env: { ...process.env, PYTHONIOENCODING: "utf-8" } as Record<string, string>,
    // stderr 走 inherit,sidecar logging.basicConfig 的输出能直接到主进程控制台
    stderr: "inherit",
  });

  mcpClient = new Client(
    { name: "audio-qc-electron", version: "0.1.0" },
    { capabilities: {} }
  );

  await mcpClient.connect(transport);
  console.log("[mcp] connected to sidecar.mcp_server");

  const { tools } = await mcpClient.listTools();
  console.log(`[mcp] ${tools.length} tools available:`);
  for (const t of tools) {
    console.log(`  - ${t.name}: ${(t.description ?? "").split("\n")[0]}`);
  }

  // 暴露给后续 IPC handler / agent loop 用,这一切片先不写调用方。
}

async function stopMcpClient(): Promise<void> {
  if (mcpClient) {
    try {
      await mcpClient.close();
    } catch (e) {
      console.warn("[mcp] close failed:", e);
    }
    mcpClient = null;
  }
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
  mainWindow = win;
  // setApplicationMenu(null) 把默认 F12 / Ctrl+Shift+I 一起带走了,这里手动补回
  // 确保 dev / 排查问题时能开 DevTools
  win.webContents.on("before-input-event", (e, input) => {
    if (input.type !== "keyDown") return;
    const isF12 = input.key === "F12";
    const isCtrlShiftI =
      (input.control || input.meta) && input.shift &&
      (input.key === "I" || input.key === "i");
    if (isF12 || isCtrlShiftI) {
      win.webContents.toggleDevTools();
      e.preventDefault();
    }
  });
  win.on("closed", () => {
    if (mainWindow === win) mainWindow = null;
    // 主窗关 → 混音窗也带走,避免 orphan(走 destroy 跳过 close 拦截)
    if (mixWindow && !mixWindow.isDestroyed()) mixWindow.destroy();
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

// ---------- 系统剪贴板:读"复制的文件"列表 ----------
// 用户在 Windows 资源管理器 / macOS Finder 里 Ctrl+C/Cmd+C 一个文件,然后切到本 app
// 按 Ctrl+V 粘贴。Electron contextIsolation 下 renderer 拿不到 native clipboard,
// 让主进程读出文件路径列表交给 Explorer.doPaste。
//
// Windows: clipboard 的 CF_HDROP 是文件列表标准格式;先 readBuffer 再 parse
// macOS: NSFilenamesPboardType (deprecated 但仍被多数应用写入) 是 plist XML,正则抽
// 其他: 暂不支持

function parseHdropBuffer(buf: Buffer): string[] {
  // DROPFILES struct (Windows):
  //   DWORD pFiles    offset to file list (typically 20)
  //   POINT pt        (8 bytes, ignored)
  //   BOOL  fNC       (4 bytes, ignored)
  //   BOOL  fWide     (4 bytes;非零 = UTF-16LE,零 = ANSI)
  // 之后是 double-null 结尾的路径列表
  if (buf.length < 20) return [];
  const pFiles = buf.readUInt32LE(0);
  const fWide = buf.readUInt32LE(16) !== 0;
  if (pFiles >= buf.length) return [];
  const data = buf.subarray(pFiles);
  const raw = fWide ? data.toString("utf16le") : data.toString("latin1");
  return raw.split("\0").filter((s) => s.length > 0);
}

// Windows: 读 OS 剪贴板上"复制的文件"列表。
//
// 历史教训:Electron 的 clipboard.readBuffer("CF_HDROP") 在 Win 上是把字符串
// 喂给 RegisterClipboardFormat 注册一个"自定义"格式 id,而非走系统 CF_HDROP=15,
// 因此空 buffer 永远拿不到。换 PowerShell 的 Get-Clipboard 走 OLE 接口最可靠
// (PyQt QClipboard 也是走 OLE,行为一致)。慢点(每次 ~200ms)但只在 Ctrl+V 时调,
// 用户不会有感。
async function readClipboardFilesWindows(): Promise<string[]> {
  try {
    const paths = await new Promise<string[]>((resolve) => {
      const proc = spawn(
        "powershell.exe",
        [
          "-NoProfile",
          "-NonInteractive",
          "-Command",
          "[Console]::OutputEncoding = [Text.Encoding]::UTF8;" +
            " Get-Clipboard -Format FileDropList | ForEach-Object { $_.FullName }",
        ],
        { windowsHide: true },
      );
      let stdout = "";
      proc.stdout?.on("data", (d: Buffer) => (stdout += d.toString("utf-8")));
      let resolved = false;
      const finish = () => {
        if (resolved) return;
        resolved = true;
        const out = stdout
          .split(/\r?\n/)
          .map((s) => s.trim())
          .filter((s) => s.length > 0);
        resolve(out);
      };
      proc.on("error", () => {
        if (!resolved) {
          resolved = true;
          resolve([]);
        }
      });
      proc.on("close", finish);
      setTimeout(() => {
        if (resolved) return;
        try { proc.kill(); } catch { /* ignore */ }
        finish();
      }, 3000);
    });
    if (paths.length > 0) return paths;
  } catch (e) {
    console.warn("[clipboard] powershell read failed:", e);
  }

  // 兜底:native single-file format(用户对单文件 Ctrl+C 时偶尔有效)
  try {
    const buf = clipboard.readBuffer("FileNameW");
    if (buf.length >= 2) {
      const s = buf.toString("utf16le").replace(/\0+$/, "");
      if (s) return [s];
    }
  } catch {
    /* ignore */
  }
  return [];
}

ipcMain.handle("clipboard:read-files", async () => {
  try {
    if (process.platform === "win32") {
      const paths = await readClipboardFilesWindows();
      console.log("[clipboard] read-files (win):", paths);
      return paths;
    }
    if (process.platform === "darwin") {
      const xml = clipboard.read("NSFilenamesPboardType");
      if (xml) {
        return Array.from(xml.matchAll(/<string>([^<]+)<\/string>/g)).map(
          (m) => m[1],
        );
      }
      return [];
    }
  } catch (e) {
    console.warn("[clipboard] read-files failed:", e);
  }
  return [];
});

// ---------- 混音台独立窗口 ----------
// 主进程持有 mixTracks set + mixWindow 实例;主窗口/混音窗口通过 IPC 操纵。
//
// 关闭语义(对齐老 PyQt closeEvent):
// - 工具栏 toggle: 隐藏/显示窗口,tracks 保留
// - 系统 X 按钮 / 内部关闭按钮: 拦截 close, hide + 清空 tracks
// - 主窗关 / app quit: 真正销毁
//
// 隐藏/显示动画:从 toolbar 按钮位置缩放进出,220ms ease-out

function broadcastMixTracks() {
  if (!mixWindow || mixWindow.isDestroyed()) return;
  try {
    mixWindow.webContents.send("mix:tracks-changed", Array.from(mixTracks));
  } catch (e) {
    console.warn("[mix] broadcast failed:", e);
  }
}

function notifyMixVisibility(visible: boolean) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  try {
    mainWindow.webContents.send("mix:visibility-changed", visible);
  } catch (e) {
    console.warn("[mix] notify visibility failed:", e);
  }
}

function defaultMixBounds(): Electron.Rectangle {
  // 默认在主窗中央
  if (mainWindow && !mainWindow.isDestroyed()) {
    const mb = mainWindow.getBounds();
    const w = Math.min(960, Math.max(600, mb.width - 200));
    const h = Math.min(640, Math.max(360, mb.height - 200));
    return {
      x: mb.x + Math.round((mb.width - w) / 2),
      y: mb.y + Math.round((mb.height - h) / 2),
      width: w,
      height: h,
    };
  }
  return { x: 100, y: 100, width: 960, height: 640 };
}

interface ButtonRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

// 把渲染器里 button.getBoundingClientRect() (CSS px,主窗内坐标) 转成屏幕坐标。
// Electron 的 contentBounds 与 CSS px 在 Windows / Mac 1:1 对齐,直接相加。
function buttonRectToScreen(rect: ButtonRect | null): Electron.Rectangle | null {
  if (!rect || !mainWindow || mainWindow.isDestroyed()) return null;
  const cb = mainWindow.getContentBounds();
  return {
    x: Math.round(cb.x + rect.x),
    y: Math.round(cb.y + rect.y),
    width: Math.max(1, Math.round(rect.w)),
    height: Math.max(1, Math.round(rect.h)),
  };
}

function easeOutCubic(t: number): number {
  return 1 - Math.pow(1 - t, 3);
}

async function animateMixWindow(
  win: BrowserWindow,
  fromBounds: Electron.Rectangle,
  toBounds: Electron.Rectangle,
  fromOpacity: number,
  toOpacity: number,
  durationMs = 220,
): Promise<void> {
  const start = Date.now();
  while (true) {
    if (win.isDestroyed()) return;
    const elapsed = Date.now() - start;
    const t = Math.min(1, elapsed / durationMs);
    const e = easeOutCubic(t);
    win.setBounds({
      x: Math.round(fromBounds.x + (toBounds.x - fromBounds.x) * e),
      y: Math.round(fromBounds.y + (toBounds.y - fromBounds.y) * e),
      width: Math.max(1, Math.round(fromBounds.width + (toBounds.width - fromBounds.width) * e)),
      height: Math.max(1, Math.round(fromBounds.height + (toBounds.height - fromBounds.height) * e)),
    });
    win.setOpacity(fromOpacity + (toOpacity - fromOpacity) * e);
    if (t >= 1) return;
    await new Promise((r) => setTimeout(r, 16));
  }
}

function ensureMixWindow(): BrowserWindow {
  if (mixWindow && !mixWindow.isDestroyed()) return mixWindow;
  const win = new BrowserWindow({
    width: 960,
    height: 640,
    // 动画期间会临时缩到很小,所以给 1 而不是默认大小
    minWidth: 1,
    minHeight: 1,
    title: "混音台",
    parent: mainWindow ?? undefined,
    show: false, // 自己控制显示时机以便动画
    // 无原生标题栏 + 控制按钮:由 React 内部 toolbar 提供 minimize / close,避免
    // 原生 minimize 动画与我们的 hide 动画相互打架(老版 frame: true 时会"抽搐")
    frame: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      webviewTag: false,
    },
  });
  mixWindow = win;
  if (process.env.VITE_DEV_SERVER_URL) {
    win.loadURL(`${process.env.VITE_DEV_SERVER_URL}/?view=mix-console`);
  } else {
    win.loadFile(path.join(__dirname, "..", "dist", "index.html"), {
      query: { view: "mix-console" },
    });
  }
  // X 按钮 / Alt+F4: 拦截改成 hide + 清空 tracks(老 PyQt closeEvent 行为)
  win.on("close", (e) => {
    if (mixWindow !== win) return;
    if (isAppQuitting) return; // app 真退出,放行
    e.preventDefault();
    mixTracks.clear();
    broadcastMixTracks();
    void hideMixWindowAnimated(lastToolbarButtonRect);
  });
  // 键盘 Win+Down / Cmd+M 仍可能触发 minimize 事件;直接 hide,不 restore
  // (无 frame 后这条路径几乎不再被触发,留个兜底)
  win.on("minimize", () => {
    if (mixWindow !== win) return;
    if (isAppQuitting) return;
    win.hide();
    notifyMixVisibility(false);
  });
  // 拖动 / 改大小时实时更新 cached bounds,minimize / hide 后再 show 能回到正确位置
  win.on("resize", () => {
    if (mixWindow !== win) return;
    if (win.isVisible() && !win.isMinimized() && !mixAnimating) {
      lastVisibleMixBounds = win.getBounds();
    }
  });
  win.on("move", () => {
    if (mixWindow !== win) return;
    if (win.isVisible() && !win.isMinimized() && !mixAnimating) {
      lastVisibleMixBounds = win.getBounds();
    }
  });
  win.on("closed", () => {
    if (mixWindow === win) mixWindow = null;
  });
  return win;
}

async function showMixWindowAnimated(rect: ButtonRect | null): Promise<void> {
  const win = ensureMixWindow();
  if (mixAnimating) return;
  if (win.isVisible()) {
    win.focus();
    return;
  }
  mixAnimating = true;
  try {
    const target = lastVisibleMixBounds ?? defaultMixBounds();
    const screenRect = buttonRectToScreen(rect);
    if (screenRect) {
      win.setBounds(screenRect);
      win.setOpacity(0);
      win.show();
      await animateMixWindow(win, screenRect, target, 0, 1);
    } else {
      win.setBounds(target);
      win.setOpacity(1);
      win.show();
    }
    notifyMixVisibility(true);
  } finally {
    mixAnimating = false;
  }
}

async function hideMixWindowAnimated(rect: ButtonRect | null): Promise<void> {
  if (!mixWindow || mixWindow.isDestroyed()) return;
  if (!mixWindow.isVisible()) return;
  if (mixAnimating) return;
  mixAnimating = true;
  try {
    const win = mixWindow;
    const fromBounds = win.getBounds();
    lastVisibleMixBounds = fromBounds;
    const screenRect = buttonRectToScreen(rect);
    if (screenRect) {
      await animateMixWindow(win, fromBounds, screenRect, 1, 0);
    }
    win.hide();
    // 还原默认状态(下次直接 show 用)
    win.setOpacity(1);
    win.setBounds(fromBounds);
    notifyMixVisibility(false);
  } finally {
    mixAnimating = false;
  }
}

ipcMain.handle("mix:toggle", async (_e, rect: ButtonRect | null) => {
  if (rect) lastToolbarButtonRect = rect; // 缓存给 minimize / X 按钮当 fallback
  if (!mixWindow || mixWindow.isDestroyed() || !mixWindow.isVisible()) {
    await showMixWindowAnimated(rect);
  } else {
    await hideMixWindowAnimated(rect);
  }
});

// 内部 UI(MixConsole 顶栏 X)调用:与系统 X 等价 → hide + 清空 tracks
ipcMain.handle("mix:close", async () => {
  if (!mixWindow || mixWindow.isDestroyed()) return;
  mixTracks.clear();
  broadcastMixTracks();
  await hideMixWindowAnimated(lastToolbarButtonRect);
});

// 自定义 minimize 按钮:与 toolbar 再点一次等价 → 仅 hide,tracks 保留
ipcMain.handle("mix:hide", async () => {
  await hideMixWindowAnimated(lastToolbarButtonRect);
});

ipcMain.handle("mix:add-tracks", async (_e, paths: unknown) => {
  if (!Array.isArray(paths)) return;
  let added = false;
  for (const p of paths) {
    if (typeof p === "string" && p && !mixTracks.has(p)) {
      mixTracks.add(p);
      added = true;
    }
  }
  await showMixWindowAnimated(lastToolbarButtonRect);
  if (added) broadcastMixTracks();
});

ipcMain.handle("mix:remove-track", (_e, p: unknown) => {
  if (typeof p !== "string") return;
  if (mixTracks.delete(p)) broadcastMixTracks();
});

ipcMain.handle("mix:get-tracks", () => Array.from(mixTracks));

app.whenReady().then(async () => {
  // chat 持久化 DB(SQLite)。dataDir 走 Electron userData,跨平台标准位置。
  // Win:%APPDATA%/audio-qc-app/  macOS:~/Library/Application Support/audio-qc-app/
  try {
    initDb(app.getPath("userData"));
    console.log(`[db] chat DB initialized at ${app.getPath("userData")}/chats.db`);
  } catch (e) {
    console.error("[db] init failed:", e);
  }

  spawnSidecar();
  try {
    await waitForPort(SIDECAR_PORT);
    console.log(`[main] sidecar ready on ${SIDECAR_PORT}`);
  } catch (e) {
    console.error(`[main] ${e}`);
  }

  // MCP 子进程并行启动,失败不阻塞主流程(agent 还没接,FastAPI 路径仍工作)
  startMcpClient().catch((e) => {
    console.error("[mcp] startMcpClient failed:", e);
  });

  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

console.log("[main] window-all-closed registered");
app.on("window-all-closed", () => {
  isAppQuitting = true;
  stopFsWatcher();
  if (mixWindow && !mixWindow.isDestroyed()) mixWindow.destroy();
  if (sidecarProc && !sidecarProc.killed) sidecarProc.kill();
  void stopMcpClient();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  isAppQuitting = true;
  stopFsWatcher();
  if (mixWindow && !mixWindow.isDestroyed()) mixWindow.destroy();
  if (sidecarProc && !sidecarProc.killed) sidecarProc.kill();
  void stopMcpClient();
  closeDb();
});
