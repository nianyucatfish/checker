import { lstat, mkdtemp, readdir, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

if (process.platform !== "darwin") {
  throw new Error("macOS package smoke check requires macOS");
}

const [version, arch] = process.argv.slice(2);
if (!version || !arch) throw new Error("usage: node scripts/smoke-macos-package.mjs <version> <x64|arm64>");
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const releaseDir = path.join(repo, "app", "release");
const prefix = `Audio QC-${version}-macos-${arch}-`;
const artifacts = await readdir(releaseDir);
const dmg = artifacts.find((name) => name.startsWith(prefix) && name.endsWith(".dmg"));
const zip = artifacts.find((name) => name.startsWith(prefix) && name.endsWith(".zip"));
if (!dmg || !zip) throw new Error(`missing ${arch} DMG or update ZIP in ${releaseDir}`);

function run(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: "inherit", ...options });
    child.on("error", reject);
    child.on("exit", (code) => code === 0 ? resolve() : reject(new Error(`${command} exited with ${code}`)));
  });
}

async function appChecks(appPath) {
  const resources = path.join(appPath, "Contents", "Resources");
  const python = path.join(resources, "python-runtime", "bin", "python3");
  for (const required of [
    appPath,
    path.join(resources, "backend", "sidecar", "serve.py"),
    path.join(resources, "prompts", "agent_workflow.md"),
    python,
  ]) {
    await stat(required);
  }
  await run(python, ["-I", "-c", "import fastapi, mcp, uvicorn; print('bundled Python imports passed')"]);
  const backend = path.join(resources, "backend");
  const pythonEnv = { ...process.env, PYTHONPATH: backend, PYTHONNOUSERSITE: "1" };
  await run(python, ["-X", "utf8", "-m", "sidecar.serve", "--help"], { cwd: backend, env: pythonEnv });
  await run(python, ["-X", "utf8", "-c", "import sidecar.mcp_server; print('bundled MCP module imports passed')"], { cwd: backend, env: pythonEnv });
}

const work = await mkdtemp(path.join(tmpdir(), "audio-qc-macos-smoke-"));
const mount = path.join(work, "mount");
const extracted = path.join(work, "update-zip");
try {
  await run("mkdir", ["-p", mount]);
  await run("hdiutil", ["attach", path.join(releaseDir, dmg), "-nobrowse", "-readonly", "-mountpoint", mount]);
  const mountedApp = path.join(mount, "Audio QC.app");
  await appChecks(mountedApp);
  // `stat()` follows the drag target to /Applications (a directory). `lstat()`
  // observes the actual DMG entry: a POSIX symlink on HFS+ or Finder alias on APFS.
  const applications = await lstat(path.join(mount, "Applications"));
  if (!applications.isSymbolicLink() && !applications.isFile()) {
    throw new Error("DMG Applications alias is missing");
  }
  await run("hdiutil", ["detach", mount]);

  await run("mkdir", ["-p", extracted]);
  // ditto, unlike BSD unzip, retains macOS symlinks and executable mode bits.
  await run("ditto", ["-x", "-k", path.join(releaseDir, zip), extracted]);
  await appChecks(path.join(extracted, "Audio QC.app"));
  console.log(`macOS ${arch} package smoke check passed`);
} finally {
  await run("hdiutil", ["detach", mount]).catch(() => undefined);
  await rm(work, { recursive: true, force: true });
}
