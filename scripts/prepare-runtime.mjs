import { createHash } from "node:crypto";
import { createReadStream, createWriteStream } from "node:fs";
import { cp, mkdir, mkdtemp, readFile, readdir, rename, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(path.join(repo, "app", "package.json"));
const tar = require("tar");
const manifestPath = path.join(repo, "packaging", "python-runtime-manifest.json");
const stage = path.join(repo, "packaging", "staging");
const key = `${process.platform}-${process.arch}`;

function run(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: "inherit", ...options });
    child.on("error", reject);
    child.on("exit", (code) => code === 0 ? resolve() : reject(new Error(`${command} exited with ${code}`)));
  });
}

async function sha256(file) {
  const hash = createHash("sha256");
  await new Promise((resolve, reject) => createReadStream(file).on("data", (chunk) => hash.update(chunk)).on("end", resolve).on("error", reject));
  return hash.digest("hex");
}

async function download(url, destination) {
  const response = await fetch(url, {
    redirect: "follow",
    signal: AbortSignal.timeout(15 * 60_000),
  });
  if (!response.ok || !response.body) throw new Error(`download failed: HTTP ${response.status} ${url}`);
  const output = createWriteStream(destination);
  await new Promise((resolve, reject) => {
    response.body.pipeTo(new WritableStream({
      write(chunk) { return new Promise((ok, fail) => output.write(Buffer.from(chunk), (error) => error ? fail(error) : ok())); },
      close() { output.end(resolve); },
      abort(error) { output.destroy(); reject(error); },
    })).catch(reject);
    output.on("error", reject);
  });
}

async function pruneRuntime(runtimeRoot) {
  const removableDirectories = [
    "include",
    "libs",
    "tcl",
    path.join("Lib", "ensurepip"),
    path.join("Lib", "idlelib"),
    path.join("Lib", "lib2to3"),
    path.join("Lib", "tkinter"),
    path.join("Lib", "turtledemo"),
    path.join("Lib", "venv"),
    path.join("Lib", "site-packages", "pip"),
  ];
  await Promise.all(removableDirectories.map((relative) => rm(path.join(runtimeRoot, relative), { recursive: true, force: true })));

  async function walk(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const full = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        if (entry.name === "__pycache__" || entry.name === "test" || entry.name === "tests") {
          await rm(full, { recursive: true, force: true });
        } else {
          await walk(full);
        }
      } else if (entry.name.endsWith(".pdb") || entry.name.endsWith(".pyc") || entry.name.endsWith(".chm")) {
        await rm(full, { force: true });
      }
    }
  }
  await walk(runtimeRoot);
  for (const entry of await readdir(path.join(runtimeRoot, "Lib", "site-packages"))) {
    if (/^pip-.*\.dist-info$/i.test(entry)) {
      await rm(path.join(runtimeRoot, "Lib", "site-packages", entry), { recursive: true, force: true });
    }
  }
}

async function copyBackend() {
  const destination = path.join(stage, "backend", "sidecar");
  await cp(path.join(repo, "sidecar"), destination, {
    recursive: true,
    filter(source) {
      const relative = path.relative(path.join(repo, "sidecar"), source).replaceAll("\\", "/");
      return !relative.split("/").some((part) => part === "tests" || part === "__pycache__")
        && !relative.endsWith("requirements-dev.txt")
        && !relative.endsWith(".pyc");
    },
  });
  await cp(path.join(repo, "doc", "prompts"), path.join(stage, "prompts"), { recursive: true });
}

async function main() {
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  const entry = manifest.platforms?.[key];
  if (!entry) throw new Error(`no python runtime manifest entry for ${key}`);
  const url = process.env[entry.urlEnv] || entry.url;
  const expected = (process.env[entry.sha256Env] || entry.sha256 || "").toLowerCase();
  if (!url || !/^[a-f0-9]{64}$/.test(expected)) {
    throw new Error(`Python runtime metadata is not pinned for ${key}. Set ${entry.urlEnv} and ${entry.sha256Env} to a python-build-standalone install_only .tar.gz URL and its 64-character SHA-256, or pin verified values in ${manifestPath}.`);
  }
  if (!/install_only\.tar\.gz(?:$|\?)/.test(url)) throw new Error("runtime URL must reference a python-build-standalone install_only .tar.gz archive");

  await rm(stage, { recursive: true, force: true });
  await mkdir(stage, { recursive: true });
  const work = await mkdtemp(path.join(tmpdir(), "audio-qc-python-"));
  try {
    const archive = path.join(work, "runtime.tar.gz");
    console.log(`Downloading ${key} Python runtime...`);
    await download(url, archive);
    const actual = await sha256(archive);
    if (actual !== expected) throw new Error(`runtime SHA-256 mismatch: expected ${expected}, got ${actual}`);
    const extracted = path.join(work, "extracted");
    await mkdir(extracted);
    await tar.x({ file: archive, cwd: extracted, strict: true });
    const pythonRoot = path.join(extracted, "python");
    await stat(pythonRoot).catch(() => { throw new Error("archive does not contain the expected top-level python/ directory"); });
    const runtimeDestination = path.join(stage, "python-runtime");
    try {
      await rename(pythonRoot, runtimeDestination);
    } catch (error) {
      if (error?.code !== "EXDEV") throw error;
      await cp(pythonRoot, runtimeDestination, { recursive: true });
      await rm(pythonRoot, { recursive: true, force: true });
    }
    await copyBackend();

    const executable = process.platform === "win32"
      ? path.join(stage, "python-runtime", "python.exe")
      : path.join(stage, "python-runtime", "bin", "python3");
    await run(executable, ["-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir", "-r", path.join(repo, "sidecar", "requirements.txt")]);
    await run(executable, ["-I", "-c", "import fastapi, httpx, mcp, mido, numpy, pydantic, send2trash, soundfile, uvicorn; print('embedded Python smoke check passed')"]);
    await run(executable, ["-I", "-c", "import pytest" ]).then(
      () => { throw new Error("production runtime unexpectedly contains pytest"); },
      () => undefined,
    );
    await pruneRuntime(runtimeDestination);
    await run(executable, ["-I", "-c", "import fastapi, httpx, mcp, mido, numpy, pydantic, send2trash, soundfile, uvicorn; print('pruned embedded Python smoke check passed')"]);
    console.log(`Runtime staging ready: ${stage}`);
  } finally {
    await rm(work, { recursive: true, force: true });
  }
}

main().catch((error) => { console.error(error.message); process.exitCode = 1; });
