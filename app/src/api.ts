// 与 sidecar 通信的薄客户端。

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

export interface DurationSummaryOut {
  ok: boolean;
  folder: string;
  inconsistent: boolean;
  summary: string | null;
}

export interface CheckResult {
  ok: boolean;
  scope: string;
  errors: Record<string, CheckErrorOut[]>;
  paths_with_errors: number;
  total_errors: number;
}

declare global {
  interface Window {
    electronAPI: {
      selectWorkspace: () => Promise<string | null>;
      getSidecarUrl: () => Promise<string>;
      revealInFolder: (path: string) => Promise<void>;
      openExternal: (url: string) => Promise<void>;
      openPath: (path: string) => Promise<void>;
      getPathForFile: (file: File) => string;
      fsWatch: (root: string) => Promise<void>;
      fsUnwatch: () => Promise<void>;
      onFsChanged: (cb: (dirs: string[]) => void) => () => void;
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
  return `${base}/files/raw?path=${encodeURIComponent(path)}`;
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

export async function writeCsv(path: string, rows: string[][]): Promise<WriteResultOut> {
  return postJson("/tools/write_csv", { path, rows });
}

export async function writeText(path: string, content: string): Promise<WriteResultOut> {
  return postJson("/tools/write_text", { path, content });
}

export async function getAudioDurations(paths: string[]): Promise<GetAudioDurationsOut> {
  return postJson("/tools/get_audio_durations", { paths });
}

export async function renamePath(src: string, dst: string): Promise<FileOpResultOut> {
  return postJson("/tools/rename_path", { src, dst });
}

export async function deletePaths(paths: string[]): Promise<FileOpResultOut> {
  return postJson("/tools/delete_paths", { paths });
}

export async function copyPaths(srcs: string[], dst_dir: string): Promise<FileOpResultOut> {
  return postJson("/tools/copy_paths", { srcs, dst_dir });
}

export async function movePaths(srcs: string[], dst_dir: string): Promise<FileOpResultOut> {
  return postJson("/tools/move_paths", { srcs, dst_dir });
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

export async function getDurationSummary(folder: string): Promise<DurationSummaryOut> {
  return getJson("/tools/get_duration_summary", { folder });
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
