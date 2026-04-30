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

export async function selectWorkspace(): Promise<string | null> {
  return window.electronAPI.selectWorkspace();
}

export async function listWorkspace(root: string): Promise<ListWorkspaceOut> {
  return getJson("/tools/list_workspace", { root });
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
