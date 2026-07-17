import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const app = path.join(repo, "app");

const command = process.platform === "win32"
  ? "npm.cmd"
  : "npm";
const script = process.platform === "win32"
  ? "package:win"
  : process.platform === "darwin"
    ? process.arch === "x64"
      ? "package:mac:x64"
      : process.arch === "arm64"
        ? "package:mac:arm64"
        : undefined
    : undefined;

if (!script) {
  throw new Error(`Host packaging is unsupported for ${process.platform}-${process.arch}. Build Windows on Windows, or macOS on a native x64/arm64 Mac.`);
}

const child = spawn(command, ["run", script], { cwd: app, stdio: "inherit" });
child.on("error", (error) => { throw error; });
child.on("exit", (code) => { process.exitCode = code ?? 1; });
