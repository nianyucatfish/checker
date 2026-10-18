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

await run("qlmanage", ["-t", "-s", "540", "-o", assets, source]);
await run("swift", [path.join(repo, "scripts", "crop-dmg-background.swift"), path.join(assets, "dmg-background.svg.png"), destination]);
await rm(path.join(assets, "dmg-background.svg.png"), { force: true });
console.log(`Rendered DMG background in ${assets}; app icons use the checked-in PNG/ICNS/ICO assets.`);
