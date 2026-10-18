import { mkdir, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

if (process.platform !== "darwin") {
  throw new Error("DMG background rendering requires macOS (qlmanage)");
}

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const assets = path.join(repo, "packaging", "assets");
const source = path.join(assets, "dmg-background.svg");
const destination = path.join(assets, "dmg-background.png");

await mkdir(assets, { recursive: true });

function run(command, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: "inherit" });
    child.on("error", reject);
    child.on("exit", (code) => code === 0 ? resolve() : reject(new Error(`${command} exited with ${code}`)));
  });
}

// dmg-builder combines the standard and @2x PNG into a multi-resolution TIFF.
for (const scale of [1, 2]) {
  const output = scale === 1 ? destination : path.join(assets, "dmg-background@2x.png");
  await run("qlmanage", ["-t", "-s", String(540 * scale), "-o", assets, source]);
  await run("swift", [path.join(repo, "scripts", "crop-dmg-background.swift"), path.join(assets, "dmg-background.svg.png"), output, String(scale)]);
}
await rm(path.join(assets, "dmg-background.svg.png"), { force: true });
console.log(`Rendered standard and Retina DMG backgrounds in ${assets}`);
