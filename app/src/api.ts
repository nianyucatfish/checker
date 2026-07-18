// 与 sidecar 通信的薄客户端。

export interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

export interface ChatOut {
  ok: boolean;
  message: ChatMessage;
  model: string;
}

export interface CheckErrorOut {
  code: string;
  severity: string;
  path: string;
  message: string;
  expected: Record<string, unknown>;
  fix_hints: string[];
  machine_fixable: boolean;
}

export interface ListWorkspaceOut {
  ok: boolean;
  songs: string[];
}

export interface DirEntryOut {
  path: string;
  name: string;
  is_dir: boolean;
  size_bytes: number;
  ext: string;
}

export interface ListDirOut {
  ok: boolean;
  path: string;
  entries: DirEntryOut[];
}

export interface ReadCsvOut {
  ok: boolean;
  path: string;
  rows: string[][];
  total_rows: number;
  truncated: boolean;
}

export interface ReadTextOut {
  ok: boolean;
  path: string;
  content: string;
  truncated: boolean;
}

export interface AudioMetadataOut {
  ok: boolean;
  path: string;
  samplerate: number;
  channels: number;
  subtype: string;
  frames: number;
  duration_seconds: number;
}

export interface WriteResultOut {
  ok: boolean;
  path: string;
  bytes_written: number;
}

export interface AudioPeaksOut {
  ok: boolean;
  path: string;
  samplerate: number;
  channels: number;
  frames: number;
  duration_seconds: number;
  columns: number;
  mins: number[];
  maxs: number[];
}

export interface AudioDurationItem {
  frames: number;
  samplerate: number;
  duration_seconds: number;
}

export interface GetAudioDurationsOut {
  ok: boolean;
  durations: Record<string, AudioDurationItem | null>;
}

export interface FileOpResultOut {
  ok: boolean;
  executed: string[];
  errors: string[];
}

export interface RenameOp {
  src: string;
  dst: string;
  kind: string; // "song_folder" | "managed_dir" | "file"
}

export interface ProposeRenamesOut {
  ok: boolean;
  ops: RenameOp[];
  conflicts: string[];
}

export interface ApplyRenamesOut {
  ok: boolean;
  executed: RenameOp[];
  errors: string[];
  path_updates: Record<string, string>;
}

export interface PadResultOut {
  ok: boolean;
  padded: number;
  max_duration: number | null;
  error: string | null;
}

export interface CheckResult {
  ok: boolean;
  scope: string;
  errors: Record<string, CheckErrorOut[]>;
  paths_with_errors: number;
  total_errors: number;
}

export interface AgentEvent {
  chatId: string;
  type:
    | "assistant_text"
    | "tool_use"
    | "tool_result"
    | "phase_change"
    | "turn_done"
    | "awaiting_human"
    | "compacted"
    | "error";
  data?: unknown;
}

export interface OfflineUpdateManifest {
  schema: 2;
  product: "Audio QC";
  version: string;
  platform: "windows" | "macos";
  arch: "x64" | "arm64";
  status: "unsigned-draft";
  archiveRoot: string;
  managedRoots: string[];
  files: Array<{ path: string; sha256: string }>;
}

export interface OfflineUpdateInfo {
  currentVersion: string;
  platform: "windows" | "macos";
  arch: "x64" | "arm64";
  packaged: boolean;
  signatureStatus: "unsigned-draft";
}

export interface OfflineUpdateInspection {
  zipPath: string;
  manifest: OfflineUpdateManifest;
  fileCount: number;
  signatureStatus: "unsigned-draft";
}

// 会话列表项;主进程 listSessions 返回的精简形(不含 model / created_at)。
export interface SessionInfo {
  id: string;
  title: string;
  phase: string | null;
  song: string | null;
  updated_at: number;
}

// 与 main 进程 agent.ts UiTurn 对齐 —— hydrate 时回放,不含 human_check / error 类型
// (那两类瞬态事件不持久化)。
export type HydratedTurn =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string }
  | { kind: "tool"; name: string; args: unknown; result?: unknown }
  | { kind: "phase"; label: string };

declare global {
  interface Window {
    electronAPI: {
      selectWorkspace: () => Promise<string | null>;
      getSidecarUrl: () => Promise<string>;
      revealInFolder: (path: string) => Promise<void>;
      openExternal: (url: string) => Promise<void>;
      openPath: (path: string) => Promise<void>;
      showAlert: (message: string) => Promise<void>;
      showConfirm: (message: string) => Promise<boolean>;
      getPathForFile: (file: File) => string;
      updateInfo: () => Promise<{
        currentVersion: string;
        platform: "windows" | "macos";
        arch: "x64" | "arm64";
        packaged: boolean;
        signatureStatus: "unsigned-draft";
      }>;
      updateSelectZip: () => Promise<string | null>;
      updateInspect: (zipPath: string) => Promise<{
        zipPath: string;
        manifest: {
          version: string;
          platform: "windows" | "macos";
          arch: "x64" | "arm64";
          status: "unsigned-draft";
        };
        fileCount: number;
        signatureStatus: "unsigned-draft";
      }>;
      updateApply: () => Promise<{ ok: boolean }>;
      fsWatch: (root: string) => Promise<void>;
      fsUnwatch: () => Promise<void>;
      fsPauseWatch: () => Promise<void>;
      fsResumeWatch: (root: string) => Promise<void>;
      onFsChanged: (cb: (dirs: string[]) => void) => () => void;
      clipboardReadFiles: () => Promise<string[]>;
      clipboardWriteText: (text: string) => Promise<void>;
      mixToggle: (
        rect: { x: number; y: number; w: number; h: number } | null,
      ) => Promise<void>;
      mixClose: () => Promise<void>;
      mixHide: () => Promise<void>;
      mixAddTracks: (paths: string[]) => Promise<void>;
      mixRemoveTrack: (path: string) => Promise<void>;
      mixGetTracks: () => Promise<string[]>;
      onMixTracksChanged: (cb: (paths: string[]) => void) => () => void;
      onMixVisibilityChanged: (cb: (visible: boolean) => void) => () => void;
      agentSend: (chatId: string, text: string) => Promise<void>;
      agentStartQc: (chatId: string, song: string) => Promise<void>;
      agentCancel: (chatId: string) => Promise<void>;
      agentHydrate: (chatId: string) => Promise<{
        phase: "A" | "B";
        song: string | null;
        turns: HydratedTurn[];
      }>;
      agentHumanCheckResolve: (
        chatId: string,
        payload: { answers: { choice: string; note?: string }[]; cancelled?: boolean },
      ) => Promise<void>;
      agentSetWorkspace: (root: string | null) => Promise<void>;
      agentListSessions: () => Promise<SessionInfo[]>;
      agentNewSession: (title?: string) => Promise<SessionInfo>;
      agentRenameSession: (chatId: string, title: string) => Promise<void>;
      agentDeleteSession: (chatId: string) => Promise<void>;
      onAgentEvent: (cb: (ev: AgentEvent) => void) => () => void;
      onUiOpenFile: (cb: (path: string) => void) => () => void;
      onPlaybackToggle: (
        cb: (reqId: number, kind: "beat" | "structure", on: boolean) => void,
      ) => () => void;
      playbackToggleResult: (
        reqId: number,
        result: { ok: boolean; code?: string; message?: string },
      ) => void;
      agentGetDumpLlm: () => Promise<boolean>;
      agentSetDumpLlm: (on: boolean) => Promise<void>;
    };
  }
}

let _sidecarUrl: string | null = null;
async function sidecarUrl(): Promise<string> {
  if (_sidecarUrl) return _sidecarUrl;
  _sidecarUrl = await window.electronAPI.getSidecarUrl();
  return _sidecarUrl;
}

async function getJson<T>(p: string, params?: Record<string, string>): Promise<T> {
  const base = await sidecarUrl();
  const url = new URL(base + p);
  if (params) Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
  const r = await fetch(url.toString());
  if (!r.ok) throw new Error(`${p} ${r.status}: ${await r.text()}`);
  return (await r.json()) as T;
}

async function postJson<T>(p: string, body: unknown): Promise<T> {
  const base = await sidecarUrl();
  const r = await fetch(base + p, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${p} ${r.status}: ${await r.text()}`);
  return (await r.json()) as T;
}

export async function rawFileUrl(path: string): Promise<string> {
  const base = await sidecarUrl();
  // 加时间戳 cache-bust:即使 sidecar Cache-Control 没生效(webview cache 行为有时
  // 怪),不同的 URL 也强制重 fetch。代价仅是 query string 变化,sidecar 不解析 _t。
  const t = Date.now();
  return `${base}/files/raw?path=${encodeURIComponent(path)}&_t=${t}`;
}

export async function sendChat(messages: ChatMessage[]): Promise<ChatOut> {
  return postJson("/chat", { messages });
}

export interface LlmConfig {
  protocol: string;
  endpoint: string;
  model: string;
  api_key: string;
  key_set: boolean;
  key_masked: string;
  config_path?: string;
}

export async function getLlmConfig(): Promise<LlmConfig> {
  return getJson("/config/llm");
}

export async function saveLlmConfig(body: {
  protocol: string;
  endpoint: string;
  model: string;
  api_key: string;
}): Promise<LlmConfig & { ok: boolean; config_path: string }> {
  return postJson("/config/llm", body);
}

export interface TencentDocsConfig {
  client_id: string;
  access_token: string;
  open_id: string;
  spreadsheet_id: string;
  sheet_id: string;
  access_token_expires_at: string;
  reviewer_name: string;
  token_set: boolean;
  token_masked: string;
  config_path?: string;
}

export async function getTencentDocsConfig(): Promise<TencentDocsConfig> {
  return getJson("/config/tencent");
}

export async function saveTencentDocsConfig(body: {
  client_id?: string;
  access_token?: string;
  open_id?: string;
  spreadsheet_id?: string;
  sheet_id?: string;
  access_token_expires_at?: string;
  reviewer_name?: string;
}): Promise<TencentDocsConfig & { ok: boolean; config_path: string }> {
  return postJson("/config/tencent", body);
}

// 分工表可用性(纯配置检查,sidecar 不打腾讯 API)。configured=false = 本地降级
// 模式:表格核对/写回交用户人工,QC 照跑。AgentSidebar 徽章用。
export interface SheetMode {
  configured: boolean;
  credentials: boolean;
  fixture: boolean;
  reviewer_set: boolean;
}

export async function getSheetMode(): Promise<SheetMode> {
  return getJson("/tools/sheet_mode");
}

/** 用 protocol-aware 的 /agent/completion 发一条极短消息探活当前配置。 */
export async function testLlmConfig(): Promise<{ ok: boolean; error?: string; preview?: string }> {
  try {
    const r = await postJson<{ message?: { content?: string } }>("/agent/completion", {
      messages: [{ role: "user", content: "ping，用一个词回复" }],
      tools: [],
      max_tokens: 16,
    });
    return { ok: true, preview: r.message?.content ?? "" };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

export async function selectWorkspace(): Promise<string | null> {
  return window.electronAPI.selectWorkspace();
}

export async function listWorkspace(root: string): Promise<ListWorkspaceOut> {
  return getJson("/tools/list_workspace", { root });
}

export async function listDir(path: string): Promise<ListDirOut> {
  return getJson("/tools/list_dir", { path });
}

export async function readCsv(path: string, start = 0, end = 5000): Promise<ReadCsvOut> {
  return getJson("/tools/read_csv", { path, start: String(start), end: String(end) });
}

export async function readText(path: string, maxBytes = 200_000): Promise<ReadTextOut> {
  return getJson("/tools/read_text", { path, max_bytes: String(maxBytes) });
}

export async function getAudioMetadata(path: string): Promise<AudioMetadataOut> {
  return getJson("/tools/get_audio_metadata", { path });
}

export async function getAudioPeaks(path: string, columns = 4000): Promise<AudioPeaksOut> {
  return getJson("/tools/get_audio_peaks", { path, columns: String(columns) });
}

// 写操作前广播 audio:release,通知 AudioViewer / MidiViewer 释放对应文件的播放句柄。
async function releaseAudio(): Promise<void> {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("audio:release"));
    await new Promise((r) => setTimeout(r, 30));
  }
}

// 当前 watch root,由 App.tsx 通过 setWatchRoot 设置。pause/resume 用。
let _watchRoot: string | null = null;
export function setWatchRoot(root: string | null): void {
  _watchRoot = root;
}

/** 写操作前置准备:释放音频 + 暂停 chokidar(Win 上 chokidar 会持目录句柄,
 *  导致 rename/delete folder 时撞 WinError 5)。 */
async function preWrite(): Promise<void> {
  await releaseAudio();
  if (_watchRoot && typeof window !== "undefined") {
    try { await window.electronAPI.fsPauseWatch(); } catch { /* ignore */ }
  }
}

/** 写操作完成后恢复 watcher。在 finally 里调,保证不论成败都恢复。 */
async function postWrite(): Promise<void> {
  if (_watchRoot && typeof window !== "undefined") {
    try { await window.electronAPI.fsResumeWatch(_watchRoot); } catch { /* ignore */ }
  }
}

async function withWriteGuard<T>(fn: () => Promise<T>): Promise<T> {
  await preWrite();
  try {
    return await fn();
  } finally {
    await postWrite();
  }
}

export async function writeCsv(path: string, rows: string[][]): Promise<WriteResultOut> {
  return withWriteGuard(() => postJson("/tools/write_csv", { path, rows }));
}

export async function writeText(path: string, content: string): Promise<WriteResultOut> {
  return withWriteGuard(() => postJson("/tools/write_text", { path, content }));
}

export async function getAudioDurations(paths: string[]): Promise<GetAudioDurationsOut> {
  return postJson("/tools/get_audio_durations", { paths });
}

export async function renamePath(src: string, dst: string): Promise<FileOpResultOut> {
  return withWriteGuard(() => postJson("/tools/rename_path", { src, dst }));
}

export async function deletePaths(paths: string[]): Promise<FileOpResultOut> {
  return withWriteGuard(() => postJson("/tools/delete_paths", { paths }));
}

export async function copyPaths(srcs: string[], dst_dir: string): Promise<FileOpResultOut> {
  return withWriteGuard(() => postJson("/tools/copy_paths", { srcs, dst_dir }));
}

export async function movePaths(srcs: string[], dst_dir: string): Promise<FileOpResultOut> {
  return withWriteGuard(() => postJson("/tools/move_paths", { srcs, dst_dir }));
}

export async function revealInFolder(path: string): Promise<void> {
  return window.electronAPI.revealInFolder(path);
}

export async function proposeRenames(songPath: string): Promise<ProposeRenamesOut> {
  return getJson("/tools/propose_renames", { song_path: songPath });
}

export async function applyRenames(ops: RenameOp[]): Promise<ApplyRenamesOut> {
  return postJson("/tools/apply_renames", { ops });
}

export async function padSongToLongest(songPath: string): Promise<PadResultOut> {
  return postJson("/tools/pad_song_to_longest", { song_path: songPath });
}

export async function checkWorkspace(root: string): Promise<CheckResult> {
  return getJson("/tools/check_workspace", { root });
}

export async function checkSong(songPath: string): Promise<CheckResult> {
  return getJson("/tools/check_song", { song_path: songPath });
}

export async function pingSidecar(): Promise<boolean> {
  try {
    const base = await sidecarUrl();
    const r = await fetch(`${base}/health`);
    return r.ok;
  } catch {
    return false;
  }
}

// ====================================================
// Dev 面板专用 (/dev/*) — 给 Toolbar 调试用,不进 agent 工具集
// ====================================================

export interface DevSheetStatus {
  mem_cached: boolean;
  mem_rows: number;
  fetched_at: string | null;
  disk_cached: boolean;
  disk_path: string;
  disk_size_kb: number | null;
  spreadsheet_id: string;
  sheet_id: string;
}

export interface DevRefreshResult {
  rows: number;
  elapsed_ms: number;
  fetched_at: string | null;
}

// 整行版:headers = 表头(列名),items = 当前用户未验收的整行数据。
export interface DevPendingItem {
  row_index: number;
  cells: string[];
}

export interface DevPendingResult {
  count: number;
  headers: string[];
  items: DevPendingItem[];
}

export async function devSheetStatus(): Promise<DevSheetStatus> {
  return getJson("/dev/sheet_status");
}

export async function devRefreshSheet(): Promise<DevRefreshResult> {
  return postJson("/dev/refresh_sheet", {});
}

export async function devListMyPending(): Promise<DevPendingResult> {
  return getJson("/dev/list_my_pending");
}

export async function devListMyAccepted(): Promise<DevPendingResult> {
  return getJson("/dev/list_my_accepted");
}
