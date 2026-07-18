import { createHash } from "node:crypto";
import { spawn, type ChildProcess } from "node:child_process";
import { createWriteStream, promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { pipeline } from "node:stream/promises";
import yauzl, { type Entry, type ZipFile } from "yauzl";

export const UPDATE_MANIFEST_NAME = "update-manifest.json";
export const UPDATE_PRODUCT = "Audio QC";
export const UPDATE_SCHEMA = 2 as const;
export const UPDATE_STATUS = "unsigned-draft" as const;

export type UpdatePlatform = "windows" | "macos";
export type UpdateArch = "x64" | "arm64";

export interface UpdateLimits {
  maxZipBytes: number;
  maxEntries: number;
  maxFileBytes: number;
  maxTotalBytes: number;
  maxCompressionRatio: number;
  maxManifestBytes: number;
}

export const DEFAULT_UPDATE_LIMITS: Readonly<UpdateLimits> = Object.freeze({
  maxZipBytes: 1_500_000_000,
  maxEntries: 20_000,
  maxFileBytes: 750_000_000,
  maxTotalBytes: 3_000_000_000,
  maxCompressionRatio: 200,
  // A Windows package currently has thousands of hashed files (notably embedded Python),
  // so its legitimate JSON manifest is larger than 1 MiB. Keep a bounded allowance above
  // the observed package size instead of rejecting every real portable update.
  maxManifestBytes: 8_000_000,
});

export interface UpdateManifestFile {
  path: string;
  type: "file" | "symlink";
  mode: number;
  sha256: string;
}

/** `status` is metadata, not a signature. Version 2 requires one named ZIP root. */
export interface UpdateManifest {
  schema: 2;
  product: "Audio QC";
  version: string;
  platform: UpdatePlatform;
  arch: UpdateArch;
  status: "unsigned-draft";
  archiveRoot: string;
  managedRoots: string[];
  files: UpdateManifestFile[];
}

export interface InspectUpdateOptions {
  platform?: UpdatePlatform;
  arch?: UpdateArch;
  currentVersion?: string;
  limits?: Partial<UpdateLimits>;
}

export interface StagedUpdate {
  stagingDir: string;
  payloadDir: string;
  manifest: UpdateManifest;
}

export type ApplyPlan =
  | {
      kind: "windows-managed-roots";
      installRoot: string;
      payloadRoot: string;
      previousRoot: string;
      rollbackRoot: string;
      previousManagedRoots: string[];
      nextManagedRoots: string[];
    }
  | {
      kind: "mac-whole-app";
      appPath: string;
      stagedAppPath: string;
      previousPath: string;
      rollbackPath: string;
    };

export interface CreateApplyPlanOptions {
  platform: UpdatePlatform;
  staged: StagedUpdate;
  installRoot?: string;
  appPath?: string;
  previousManifest?: UpdateManifest;
  previousPath: string;
  rollbackPath: string;
}

export interface SpawnApplyHelperOptions {
  platform?: NodeJS.Platform;
  waitForPid?: number;
  relaunch?: { command: string; args?: string[]; cwd?: string };
  env?: NodeJS.ProcessEnv;
  helperDir?: string;
}

const FORBIDDEN_SEGMENTS = new Set([
  "data", "cache", "caches", "config", "configs", "userdata", "user-data",
  "logs", "log", "chromium", "crashdumps", ".cache",
]);
const HASH_RE = /^[a-f0-9]{64}$/;
const VERSION_RE = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/;
const MANIFEST_KEYS = ["arch", "archiveRoot", "files", "managedRoots", "platform", "product", "schema", "status", "version"];

/** Validate a portable ZIP path without normalizing attacker-controlled input. */
export function validateArchivePath(input: string): string {
  if (typeof input !== "string" || !input || input.includes("\\") || input.includes("\0")) {
    throw new Error(`Unsafe ZIP path: ${String(input)}`);
  }
  if (input.startsWith("/") || input.startsWith("//") || /^[A-Za-z]:/.test(input)) {
    throw new Error(`Absolute ZIP path: ${input}`);
  }
  const parts = input.split("/");
  if (parts.some((part) => !part || part === "." || part === "..")) {
    throw new Error(`Unsafe ZIP path: ${input}`);
  }
  const lower = parts.map((part) => part.toLowerCase());
  const forbiddenRoot = FORBIDDEN_SEGMENTS.has(lower[0]);
  const forbiddenResourceRoot = lower[0] === "resources" && FORBIDDEN_SEGMENTS.has(lower[1]);
  const forbiddenSecret = lower.some((part) => part === "config.toml" || part === "llm_override.json");
  if (forbiddenRoot || forbiddenResourceRoot || forbiddenSecret) {
    throw new Error(`Update contains forbidden data/cache/config path: ${input}`);
  }
  return parts.join("/");
}

function parseVersion(version: string): [number, number, number, string[] | undefined] {
  const match = VERSION_RE.exec(version);
  if (!match) throw new Error(`Invalid semantic version: ${version}`);
  return [Number(match[1]), Number(match[2]), Number(match[3]), match[4]?.split(".")];
}

export function compareVersions(left: string, right: string): number {
  const a = parseVersion(left);
  const b = parseVersion(right);
  for (let i = 0; i < 3; i += 1) {
    if (a[i] !== b[i]) return (a[i] as number) - (b[i] as number);
  }
  const ap = a[3];
  const bp = b[3];
  if (!ap && !bp) return 0;
  if (!ap) return 1;
  if (!bp) return -1;
  for (let i = 0; i < Math.max(ap.length, bp.length); i += 1) {
    if (ap[i] === undefined) return -1;
    if (bp[i] === undefined) return 1;
    if (ap[i] === bp[i]) continue;
    const an = /^\d+$/.test(ap[i]);
    const bn = /^\d+$/.test(bp[i]);
    if (an && bn) return Number(ap[i]) - Number(bp[i]);
    if (an !== bn) return an ? -1 : 1;
    return ap[i].localeCompare(bp[i], "en");
  }
  return 0;
}

function exactKeys(value: Record<string, unknown>, expected: string[], label: string): void {
  const actual = Object.keys(value).sort();
  const wanted = [...expected].sort();
  if (actual.length !== wanted.length || actual.some((key, index) => key !== wanted[index])) {
    throw new Error(`${label} has unknown or missing fields`);
  }
}

export function validateUpdateManifest(value: unknown, options: InspectUpdateOptions = {}): UpdateManifest {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Manifest must be an object");
  const raw = value as Record<string, unknown>;
  exactKeys(raw, MANIFEST_KEYS, "Manifest");
  if (raw.schema !== UPDATE_SCHEMA) throw new Error("Unsupported manifest schema");
  if (raw.product !== UPDATE_PRODUCT) throw new Error("Wrong update product");
  if (raw.status !== UPDATE_STATUS) throw new Error("Only unsigned-draft update metadata is supported");
  if (raw.platform !== "windows" && raw.platform !== "macos") throw new Error("Unsupported update platform");
  if (raw.platform !== (options.platform ?? (process.platform === "darwin" ? "macos" : "windows"))) {
    throw new Error("Wrong update platform");
  }
  if (raw.arch !== "x64" && raw.arch !== "arm64") throw new Error("Unsupported update architecture");
  if (raw.arch !== (options.arch ?? process.arch)) throw new Error("Wrong update architecture");
  if (typeof raw.version !== "string") throw new Error("Invalid update version");
  parseVersion(raw.version);
  if (typeof raw.archiveRoot !== "string") throw new Error("Invalid update archive root");
  const archiveRoot = validateArchivePath(raw.archiveRoot);
  if (archiveRoot.includes("/")) throw new Error("Update archive root must be top-level");
  if (options.currentVersion && compareVersions(raw.version, options.currentVersion) <= 0) {
    throw new Error("Update version must be newer than current version");
  }
  if (!Array.isArray(raw.managedRoots) || raw.managedRoots.length === 0) throw new Error("managedRoots must be non-empty");
  if (!Array.isArray(raw.files) || raw.files.length === 0) throw new Error("files must be non-empty");

  const foldedRoots = new Set<string>();
  const managedRoots = raw.managedRoots.map((item) => {
    const root = validateArchivePath(String(item));
    if (root.includes("/")) throw new Error(`Managed root must be top-level: ${root}`);
    const folded = root.toLowerCase();
    if (foldedRoots.has(folded)) throw new Error(`Duplicate managed root: ${root}`);
    foldedRoots.add(folded);
    return root;
  });
  if (raw.platform === "macos" && (managedRoots.length !== 1 || managedRoots[0] !== "Audio QC.app")) {
    throw new Error("macOS update must manage exactly Audio QC.app");
  }

  const foldedFiles = new Set<string>();
  const files = raw.files.map((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) throw new Error("Invalid manifest file");
    const file = item as Record<string, unknown>;
    exactKeys(file, ["mode", "path", "sha256", "type"], "Manifest file");
    const filePath = validateArchivePath(String(file.path));
    if (filePath.toLowerCase() === UPDATE_MANIFEST_NAME) throw new Error("Manifest cannot declare itself");
    const folded = filePath.toLowerCase();
    if (foldedFiles.has(folded)) throw new Error(`Duplicate or case-colliding file: ${filePath}`);
    foldedFiles.add(folded);
    if (file.type !== "file" && file.type !== "symlink") throw new Error(`Invalid manifest file type: ${filePath}`);
    if (!Number.isInteger(file.mode) || (file.mode as number) < 0 || (file.mode as number) > 0o777) throw new Error(`Invalid file mode: ${filePath}`);
    if (file.type === "symlink" && raw.platform !== "macos") throw new Error(`Symlink is forbidden on ${raw.platform}: ${filePath}`);
    if (typeof file.sha256 !== "string" || !HASH_RE.test(file.sha256)) throw new Error(`Invalid SHA-256: ${filePath}`);
    if (!foldedRoots.has(filePath.split("/", 1)[0].toLowerCase())) throw new Error(`File is outside managed roots: ${filePath}`);
    return { path: filePath, type: file.type as "file" | "symlink", mode: file.mode as number, sha256: file.sha256 };
  });
  const actualRoots = new Set(files.map((file) => file.path.split("/", 1)[0].toLowerCase()));
  if (managedRoots.some((root) => !actualRoots.has(root.toLowerCase()))) throw new Error("Managed root has no declared files");

  return {
    schema: UPDATE_SCHEMA, product: UPDATE_PRODUCT, version: raw.version,
    platform: raw.platform, arch: raw.arch, status: UPDATE_STATUS, archiveRoot, managedRoots, files,
  };
}

function openZip(zipPath: string): Promise<ZipFile> {
  return new Promise((resolve, reject) => {
    yauzl.open(zipPath, { lazyEntries: true, autoClose: false, decodeStrings: true, validateEntrySizes: true }, (error, zip) => {
      if (error || !zip) reject(error ?? new Error("Cannot open ZIP"));
      else resolve(zip);
    });
  });
}

function readEntry(zip: ZipFile): Promise<Entry | undefined> {
  return new Promise((resolve, reject) => {
    const cleanup = () => { zip.off("entry", onEntry); zip.off("end", onEnd); zip.off("error", onError); };
    const onEntry = (entry: Entry) => { cleanup(); resolve(entry); };
    const onEnd = () => { cleanup(); resolve(undefined); };
    const onError = (error: Error) => { cleanup(); reject(error); };
    zip.once("entry", onEntry); zip.once("end", onEnd); zip.once("error", onError); zip.readEntry();
  });
}

function openEntry(zip: ZipFile, entry: Entry): Promise<NodeJS.ReadableStream> {
  return new Promise((resolve, reject) => zip.openReadStream(entry, (error, stream) => {
    if (error || !stream) reject(error ?? new Error(`Cannot read ZIP entry: ${entry.fileName}`));
    else resolve(stream);
  }));
}

async function readLimited(zip: ZipFile, entry: Entry, limit: number): Promise<Buffer> {
  if (entry.uncompressedSize > limit) throw new Error("Update manifest exceeds size limit");
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of await openEntry(zip, entry)) {
    size += chunk.length;
    if (size > limit) throw new Error("Update manifest exceeds size limit");
    chunks.push(Buffer.from(chunk));
  }
  return Buffer.concat(chunks);
}

function isSymlink(entry: Entry): boolean {
  const unixMode = (entry.externalFileAttributes >>> 16) & 0xffff;
  return (unixMode & 0o170000) === 0o120000;
}

/** Inspect the caller-supplied single ZIP, verify its exact payload and hashes, then extract to a fresh directory. */
export async function inspectAndStageOfflineUpdate(
  zipPath: string,
  stagingParent: string,
  options: InspectUpdateOptions = {},
): Promise<StagedUpdate> {
  if (typeof zipPath !== "string" || path.extname(zipPath).toLowerCase() !== ".zip") {
    throw new Error("Offline update input must be a single ZIP file");
  }
  const limits = { ...DEFAULT_UPDATE_LIMITS, ...options.limits };
  const zipStat = await fs.stat(zipPath);
  if (!zipStat.isFile()) throw new Error("Offline update input must be a single ZIP file");
  if (zipStat.size > limits.maxZipBytes) throw new Error("Update ZIP exceeds size limit");

  await fs.mkdir(stagingParent, { recursive: true });
  const stagingDir = await fs.mkdtemp(path.join(stagingParent, "offline-update-"));
  const payloadDir = path.join(stagingDir, "payload");
  await fs.mkdir(payloadDir);
  let zip: ZipFile | undefined;
  try {
    zip = await openZip(zipPath);
    const entries = new Map<string, Entry>();
    const foldedNames = new Set<string>();
    let archiveRoot: string | undefined;
    let manifestEntry: Entry | undefined;
    let count = 0;
    let totalBytes = 0;
    for (;;) {
      const entry = await readEntry(zip);
      if (!entry) break;
      count += 1;
      if (count > limits.maxEntries) throw new Error("ZIP has too many entries");
      const directory = entry.fileName.endsWith("/");
      const rawName = directory ? entry.fileName.slice(0, -1) : entry.fileName;
      const archiveName = validateArchivePath(rawName);
      const [root, ...relativeParts] = archiveName.split("/");
      if (archiveRoot && archiveRoot !== root) throw new Error("ZIP must contain exactly one top-level application directory");
      archiveRoot = root;
      if (relativeParts.length === 0) {
        if (!directory) throw new Error("ZIP files must be inside the top-level application directory");
        continue;
      }
      const name = validateArchivePath(relativeParts.join("/"));
      const folded = name.toLowerCase();
      if (foldedNames.has(folded)) throw new Error(`Duplicate or case-colliding ZIP entry: ${name}`);
      foldedNames.add(folded);
      if (directory) continue;
      if (entry.uncompressedSize > limits.maxFileBytes) throw new Error(`ZIP entry exceeds size limit: ${name}`);
      totalBytes += entry.uncompressedSize;
      if (totalBytes > limits.maxTotalBytes) throw new Error("ZIP exceeds extracted size limit");
      if (entry.uncompressedSize > 0 && entry.uncompressedSize / Math.max(1, entry.compressedSize) > limits.maxCompressionRatio) {
        throw new Error(`Suspicious compression ratio: ${name}`);
      }
      if (folded === UPDATE_MANIFEST_NAME) manifestEntry = entry;
      else entries.set(name, entry);
    }
    if (!archiveRoot || !manifestEntry) throw new Error("Update manifest is missing");

    let parsed: unknown;
    try {
      parsed = JSON.parse((await readLimited(zip, manifestEntry, limits.maxManifestBytes)).toString("utf8"));
    } catch (error) {
      if (error instanceof SyntaxError) throw new Error(`Invalid update manifest JSON: ${error.message}`);
      throw error;
    }
    const manifest = validateUpdateManifest(parsed, options);
    if (manifest.archiveRoot !== archiveRoot) throw new Error("ZIP root does not match update manifest");
    if (entries.size !== manifest.files.length) throw new Error("ZIP file set does not exactly match manifest");

    for (const declared of manifest.files) {
      // macOS's archive root is the managed Audio QC.app itself; Windows's root is
      // only a user-facing wrapper folder and is intentionally absent from files[].
      if (manifest.platform === "macos" && !declared.path.startsWith(`${manifest.archiveRoot}/`)) {
        throw new Error(`macOS manifest file is outside archive root: ${declared.path}`);
      }
      const entryPath = manifest.platform === "macos"
        ? declared.path.slice(manifest.archiveRoot.length + 1)
        : declared.path;
      const entry = entries.get(entryPath);
      if (!entry) throw new Error(`ZIP file set does not exactly match manifest: ${declared.path}`);
      if (isSymlink(entry) !== (declared.type === "symlink")) throw new Error(`ZIP entry type mismatch: ${declared.path}`);
      const destination = path.join(payloadDir, ...declared.path.split("/"));
      const relative = path.relative(payloadDir, destination);
      if (relative.startsWith("..") || path.isAbsolute(relative)) throw new Error(`Extraction escaped staging: ${declared.path}`);
      await fs.mkdir(path.dirname(destination), { recursive: true });
      const hash = createHash("sha256");
      const source = await openEntry(zip, entry);
      if (declared.type === "symlink") {
        const chunks: Buffer[] = [];
        for await (const chunk of source) chunks.push(Buffer.from(chunk));
        const target = Buffer.concat(chunks).toString("utf8");
        hash.update(target);
        if (path.isAbsolute(target)) throw new Error(`Absolute symlink target: ${declared.path}`);
        const resolved = path.resolve(path.dirname(destination), target);
        const relativeTarget = path.relative(payloadDir, resolved);
        if (relativeTarget.startsWith("..") || path.isAbsolute(relativeTarget)) throw new Error(`Symlink escaped staging: ${declared.path}`);
        await fs.symlink(target, destination);
      } else {
        source.on("data", (chunk: Buffer) => hash.update(chunk));
        await pipeline(source, createWriteStream(destination, { flags: "wx", mode: declared.mode }));
        await fs.chmod(destination, declared.mode);
      }
      if (hash.digest("hex") !== declared.sha256) throw new Error(`SHA-256 mismatch: ${declared.path}`);
    }
    await fs.writeFile(path.join(stagingDir, UPDATE_MANIFEST_NAME), JSON.stringify(manifest), { flag: "wx", mode: 0o600 });
    return { stagingDir, payloadDir, manifest };
  } catch (error) {
    await fs.rm(stagingDir, { recursive: true, force: true });
    throw error;
  } finally {
    zip?.close();
  }
}

/** Alias convenient for main-process callers. */
export const verifyAndStageUpdate = inspectAndStageOfflineUpdate;

function resolveChild(root: string, archivePath: string): string {
  return path.join(path.resolve(root), ...validateArchivePath(archivePath).split("/"));
}

function validateRootList(roots: string[]): string[] {
  return roots.map((root) => {
    const safe = validateArchivePath(root);
    if (safe.includes("/")) throw new Error(`Managed root must be top-level: ${safe}`);
    return safe;
  });
}

/** Build serializable instructions for a helper that runs after the current app exits. */
export function createApplyPlan(options: CreateApplyPlanOptions): ApplyPlan {
  if (options.platform !== options.staged.manifest.platform) throw new Error("Apply platform does not match staged update");
  if (options.platform === "windows") {
    if (!options.installRoot) throw new Error("installRoot is required for Windows update");
    return {
      kind: "windows-managed-roots",
      installRoot: path.resolve(options.installRoot),
      payloadRoot: path.resolve(options.staged.payloadDir),
      previousRoot: path.resolve(options.previousPath),
      rollbackRoot: path.resolve(options.rollbackPath),
      previousManagedRoots: validateRootList(options.previousManifest?.managedRoots ?? []),
      nextManagedRoots: validateRootList(options.staged.manifest.managedRoots),
    };
  }
  if (!options.appPath) throw new Error("appPath is required for macOS update");
  if (options.staged.manifest.managedRoots.length !== 1 || options.staged.manifest.managedRoots[0] !== "Audio QC.app") {
    throw new Error("macOS staged update must contain exactly Audio QC.app");
  }
  return {
    kind: "mac-whole-app",
    appPath: path.resolve(options.appPath),
    stagedAppPath: resolveChild(options.staged.payloadDir, "Audio QC.app"),
    previousPath: path.resolve(options.previousPath),
    rollbackPath: path.resolve(options.rollbackPath),
  };
}

async function pathExists(target: string): Promise<boolean> {
  try { await fs.lstat(target); return true; } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return false;
    throw error;
  }
}

async function moveReplacing(source: string, destination: string): Promise<void> {
  await fs.rm(destination, { recursive: true, force: true });
  await fs.mkdir(path.dirname(destination), { recursive: true });
  await fs.rename(source, destination);
}

/** Apply a prepared plan. The external helper should call this only after the app process exits. */
export async function executeApplyPlan(plan: ApplyPlan): Promise<void> {
  if (plan.kind === "mac-whole-app") {
    if (!(await pathExists(plan.stagedAppPath))) throw new Error("Staged Audio QC.app is missing");
    await fs.rm(plan.previousPath, { recursive: true, force: true });
    await fs.rm(plan.rollbackPath, { recursive: true, force: true });
    await fs.rename(plan.appPath, plan.previousPath);
    try {
      await fs.rename(plan.stagedAppPath, plan.appPath);
    } catch (error) {
      await fs.rename(plan.previousPath, plan.rollbackPath);
      await fs.rename(plan.rollbackPath, plan.appPath);
      throw error;
    }
    return;
  }

  const oldRoots = validateRootList(plan.previousManagedRoots);
  const newRoots = validateRootList(plan.nextManagedRoots);
  const affected = [...new Set([...oldRoots, ...newRoots])];
  for (const root of newRoots) {
    if (!(await pathExists(resolveChild(plan.payloadRoot, root)))) throw new Error(`Staged managed root is missing: ${root}`);
  }
  await fs.rm(plan.previousRoot, { recursive: true, force: true });
  await fs.rm(plan.rollbackRoot, { recursive: true, force: true });
  await fs.mkdir(plan.previousRoot, { recursive: true });
  const backedUp: string[] = [];
  const installedNext: string[] = [];
  try {
    for (const root of affected) {
      const installed = resolveChild(plan.installRoot, root);
      if (await pathExists(installed)) {
        await moveReplacing(installed, resolveChild(plan.previousRoot, root));
        backedUp.push(root);
      }
    }
    for (const root of newRoots) {
      await moveReplacing(resolveChild(plan.payloadRoot, root), resolveChild(plan.installRoot, root));
      installedNext.push(root);
    }
  } catch (error) {
    await fs.mkdir(plan.rollbackRoot, { recursive: true });
    for (const root of installedNext) {
      const installed = resolveChild(plan.installRoot, root);
      if (await pathExists(installed)) await moveReplacing(installed, resolveChild(plan.rollbackRoot, root));
    }
    for (const root of backedUp) {
      const previous = resolveChild(plan.previousRoot, root);
      if (await pathExists(previous)) await moveReplacing(previous, resolveChild(plan.installRoot, root));
    }
    throw error;
  }
}

type NativeHelperPayload = {
  plan: ApplyPlan;
  waitForPid: number;
  relaunch?: { command: string; args?: string[]; cwd?: string };
};

function validateHelperPayload(payload: NativeHelperPayload): void {
  if (!Number.isSafeInteger(payload.waitForPid) || payload.waitForPid <= 0) throw new Error("Invalid helper wait PID");
  if (payload.relaunch) {
    if (!payload.relaunch.command || payload.relaunch.args?.some((arg) => typeof arg !== "string")) throw new Error("Invalid relaunch command");
  }
}

/** Generate a standalone PowerShell helper. Update paths live in base64 JSON rather than PowerShell source literals. */
export function generateWindowsApplyScript(payload: NativeHelperPayload): string {
  validateHelperPayload(payload);
  if (payload.plan.kind !== "windows-managed-roots") throw new Error("Windows helper requires a Windows apply plan");
  const encoded = Buffer.from(JSON.stringify(payload), "utf8").toString("base64");
  return `$ErrorActionPreference = 'Stop'
$payload = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${encoded}')) | ConvertFrom-Json
$failed = $false
function Exists([string]$p) { return Test-Path -LiteralPath $p }
function Move-Replacing([string]$source, [string]$destination) {
  Remove-Item -LiteralPath $destination -Recurse -Force -ErrorAction SilentlyContinue
  New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($destination)) -Force | Out-Null
  Move-Item -LiteralPath $source -Destination $destination -Force
}
function Child([string]$root, [string]$name) { return [IO.Path]::Combine($root, $name) }
function Quote-Arg([string]$arg) {
  if ($arg.Length -gt 0 -and $arg -notmatch '[\\s"]') { return $arg }
  return '"' + [regex]::Replace($arg, '(\\*)("|$)', '$1$1$2') + '"'
}
try {
  $tries = 0
  while (Get-Process -Id ([int]$payload.waitForPid) -ErrorAction SilentlyContinue) {
    if ($tries -ge 600) { throw 'Timed out waiting for parent process to exit' }
    Start-Sleep -Milliseconds 200
    $tries++
  }
  $p = $payload.plan
  foreach ($root in $p.nextManagedRoots) { if (-not (Exists (Child $p.payloadRoot $root))) { throw "Staged managed root is missing: $root" } }
  Remove-Item -LiteralPath $p.previousRoot -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $p.rollbackRoot -Recurse -Force -ErrorAction SilentlyContinue
  New-Item -ItemType Directory -Path $p.previousRoot -Force | Out-Null
  $affected = @($p.previousManagedRoots) + @($p.nextManagedRoots) | Select-Object -Unique
  $backedUp = [Collections.Generic.List[string]]::new()
  $installedNext = [Collections.Generic.List[string]]::new()
  try {
    foreach ($root in $affected) {
      $installed = Child $p.installRoot $root
      if (Exists $installed) { Move-Replacing $installed (Child $p.previousRoot $root); $backedUp.Add($root) }
    }
    foreach ($root in $p.nextManagedRoots) { Move-Replacing (Child $p.payloadRoot $root) (Child $p.installRoot $root); $installedNext.Add($root) }
  } catch {
    New-Item -ItemType Directory -Path $p.rollbackRoot -Force | Out-Null
    foreach ($root in $installedNext) { $installed = Child $p.installRoot $root; if (Exists $installed) { Move-Replacing $installed (Child $p.rollbackRoot $root) } }
    foreach ($root in $backedUp) { $previous = Child $p.previousRoot $root; if (Exists $previous) { Move-Replacing $previous (Child $p.installRoot $root) } }
    throw
  }
} catch { $failed = $true }
try {
  if ($payload.relaunch) {
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $payload.relaunch.command
    $info.WorkingDirectory = if ($payload.relaunch.cwd) { $payload.relaunch.cwd } else { [IO.Path]::GetDirectoryName($payload.relaunch.command) }
    $info.UseShellExecute = $false
    $info.Arguments = (($payload.relaunch.args | ForEach-Object { Quote-Arg ([string]$_) }) -join ' ')
    [Diagnostics.Process]::Start($info) | Out-Null
  }
} finally { Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue }
if ($failed) { exit 1 }
`;
}

function shellQuote(value: string): string {
  if (value.includes("\0")) throw new Error("NUL is not allowed in helper arguments");
  return `'${value.replace(/'/g, `'"'"'`)}'`;
}

/** Generate a standalone /bin/sh helper with every path shell-quoted as one literal argument. */
export function generateMacApplyScript(payload: NativeHelperPayload): string {
  validateHelperPayload(payload);
  if (payload.plan.kind !== "mac-whole-app") throw new Error("macOS helper requires a macOS apply plan");
  const p = payload.plan;
  const relaunch = payload.relaunch
    ? `if ! (cd ${shellQuote(payload.relaunch.cwd ?? path.dirname(payload.relaunch.command))} && ${[payload.relaunch.command, ...(payload.relaunch.args ?? [])].map(shellQuote).join(" ")} >/dev/null 2>&1 &); then failed=1; fi`
    : ":";
  return `#!/bin/sh
failed=0
cleanup() { rm -f -- "$0"; }
trap cleanup EXIT HUP INT TERM
pid=${payload.waitForPid}
i=0
while kill -0 "$pid" 2>/dev/null; do
  [ "$i" -ge 600 ] && failed=1 && break
  sleep 0.2
  i=$((i + 1))
done
if [ "$failed" -eq 0 ]; then
  app=${shellQuote(p.appPath)}
  staged=${shellQuote(p.stagedAppPath)}
  previous=${shellQuote(p.previousPath)}
  rollback=${shellQuote(p.rollbackPath)}
  if [ ! -e "$staged" ]; then
    failed=1
  else
    rm -rf -- "$previous" "$rollback"
    if mv -- "$app" "$previous"; then
      if ! mv -- "$staged" "$app"; then
        mv -- "$previous" "$rollback" 2>/dev/null || true
        mv -- "$rollback" "$app" 2>/dev/null || true
        failed=1
      fi
    else
      failed=1
    fi
  fi
fi
${relaunch}
exit "$failed"
`;
}

/** Launch a detached native helper outside the installation being replaced. Call app.quit() after this succeeds. */
export async function spawnExternalApplyHelper(plan: ApplyPlan, options: SpawnApplyHelperOptions = {}): Promise<ChildProcess> {
  const platform = options.platform ?? process.platform;
  if (platform !== "win32" && platform !== "darwin") throw new Error(`Offline update apply is unsupported on ${platform}`);
  const payload: NativeHelperPayload = { plan, waitForPid: options.waitForPid ?? process.pid, relaunch: options.relaunch };
  const helperDir = options.helperDir ?? os.tmpdir();
  await fs.mkdir(helperDir, { recursive: true });
  const extension = platform === "win32" ? "ps1" : "sh";
  const helperPath = path.join(helperDir, `audio-qc-update-${process.pid}-${Date.now()}.${extension}`);
  const script = platform === "win32" ? generateWindowsApplyScript(payload) : generateMacApplyScript(payload);
  await fs.writeFile(helperPath, script, { flag: "wx", mode: 0o700 });
  const child = platform === "win32"
    ? spawn("powershell.exe", ["-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", helperPath], {
        detached: true, stdio: "ignore", windowsHide: true, env: { ...process.env, ...options.env },
      })
    : spawn("/bin/sh", [helperPath], {
        detached: true, stdio: "ignore", env: { ...process.env, ...options.env },
      });
  child.once("error", () => { void fs.rm(helperPath, { force: true }); });
  child.unref();
  return child;
}
