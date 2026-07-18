import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { lstat, mkdtemp, readlink, readdir, rename, rm, writeFile } from "node:fs/promises";
import path from "node:path";

const MANIFEST_NAME = "update-manifest.json";
const PRODUCT = "Audio QC";
const FORBIDDEN_SEGMENTS = new Set([
  "data", "cache", "caches", "config", "configs", "userdata", "user-data",
  "logs", "log", "chromium", "crashdumps", ".cache",
]);
const ARCH_NAMES = new Map([[1, "x64"], [3, "arm64"]]);

function archivePath(relative) {
  return relative.split(path.sep).join("/");
}

function assertSafePath(relative) {
  const parts = archivePath(relative).split("/");
  const lower = parts.map((segment) => segment.toLowerCase());
  const forbiddenRoot = FORBIDDEN_SEGMENTS.has(lower[0]);
  const forbiddenResourceRoot = lower[0] === "resources" && FORBIDDEN_SEGMENTS.has(lower[1]);
  const forbiddenSecret = lower.some((segment) => segment === "config.toml" || segment === "llm_override.json");
  if (forbiddenRoot || forbiddenResourceRoot || forbiddenSecret) {
    throw new Error(`Refusing to package forbidden data/cache/config path: ${relative}`);
  }
}

async function sha256(filePath) {
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(filePath)) hash.update(chunk);
  return hash.digest("hex");
}

async function collectFiles(root, relative = "", platform) {
  const directory = path.join(root, relative);
  const entries = await readdir(directory, { withFileTypes: true });
  entries.sort((left, right) => left.name.localeCompare(right.name, "en"));
  const files = [];
  for (const entry of entries) {
    const childRelative = relative ? path.join(relative, entry.name) : entry.name;
    if (!relative && entry.name.toLowerCase() === MANIFEST_NAME) continue;
    assertSafePath(childRelative);
    const childPath = path.join(root, childRelative);
    const details = await lstat(childPath);
    if (details.isSymbolicLink()) {
      if (platform !== "macos") throw new Error(`Refusing to package symlink: ${childRelative}`);
      const target = await readlink(childPath);
      if (path.isAbsolute(target)) throw new Error(`Refusing absolute symlink: ${childRelative} -> ${target}`);
      const resolved = path.resolve(path.dirname(childPath), target);
      const relativeTarget = path.relative(root, resolved);
      if (relativeTarget.startsWith("..") || path.isAbsolute(relativeTarget)) {
        throw new Error(`Refusing escaping symlink: ${childRelative} -> ${target}`);
      }
      files.push({ path: archivePath(childRelative), type: "symlink", mode: details.mode & 0o777, sha256: createHash("sha256").update(target).digest("hex") });
    } else if (details.isDirectory()) files.push(...await collectFiles(root, childRelative, platform));
    else if (details.isFile()) files.push({ path: archivePath(childRelative), type: "file", mode: details.mode & 0o777, sha256: await sha256(childPath) });
    else throw new Error(`Refusing to package non-file entry: ${childRelative}`);
  }
  return files;
}

function resolvePlatform(context) {
  const name = context.electronPlatformName ?? context.packager?.platform?.name;
  if (name === "win32" || name === "windows") return "windows";
  if (name === "darwin" || name === "mac") return "macos";
  throw new Error(`Unsupported update platform: ${String(name)}`);
}

function resolveArch(context) {
  if (context.arch === "x64" || context.arch === "arm64") return context.arch;
  const arch = ARCH_NAMES.get(context.arch);
  if (!arch) throw new Error(`Unsupported update architecture: ${String(context.arch)}`);
  return arch;
}

export async function generateUpdateManifest({ appOutDir, version, platform, arch }) {
  const files = await collectFiles(appOutDir, "", platform);
  if (!files.length) throw new Error(`Packaged application is empty: ${appOutDir}`);
  files.sort((left, right) => left.path.localeCompare(right.path, "en"));

  const managedRoots = [...new Set(files.map((file) => file.path.split("/", 1)[0]))]
    .sort((left, right) => left.localeCompare(right, "en"));
  if (platform === "macos" && (managedRoots.length !== 1 || managedRoots[0] !== `${PRODUCT}.app`)) {
    throw new Error(`macOS appOutDir must contain exactly ${PRODUCT}.app before writing the manifest`);
  }

  const archiveRoot = platform === "windows" ? `${PRODUCT} ${version}` : `${PRODUCT}.app`;
  const manifest = {
    schema: 2,
    product: PRODUCT,
    version,
    platform,
    arch,
    status: "unsigned-draft",
    archiveRoot,
    managedRoots,
    files,
  };
  await writeFile(path.join(appOutDir, MANIFEST_NAME), `${JSON.stringify(manifest, null, 2)}\n`, { flag: "w" });
  console.log(`[update-manifest] wrote ${files.length} hashes to ${path.join(appOutDir, MANIFEST_NAME)}`);
  return manifest;
}

export default async function afterPack(context) {
  await generateUpdateManifest({
    appOutDir: context.appOutDir,
    version: context.packager.appInfo.version,
    platform: resolvePlatform(context),
    arch: resolveArch(context),
  });
}
