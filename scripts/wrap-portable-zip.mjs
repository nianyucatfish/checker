import { cp, mkdtemp, rename, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(path.join(repo, "app", "package.json"));
const sevenZip = require("7zip-bin");
const product = "Audio QC";

function run(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: "inherit", ...options });
    child.on("error", reject);
    child.on("exit", (code) => code === 0 ? resolve() : reject(new Error(`${command} exited with ${code}`)));
  });
}

/** Wrap electron-builder's flat Windows ZIP in one named portable directory. */
export default async function afterAllArtifactBuild(context) {
  const artifacts = context.artifactPaths.filter((artifact) => /-windows-x64-portable\.zip$/i.test(artifact));
  for (const artifact of artifacts) {
    const match = /Audio QC-([0-9A-Za-z.+-]+)-windows-x64-portable\.zip$/i.exec(path.basename(artifact));
    if (!match) throw new Error(`Cannot derive portable wrapper name from ${artifact}`);
    const wrapper = `${product} ${match[1]}`;
    const work = await mkdtemp(path.join(os.tmpdir(), "audio-qc-portable-"));
    try {
      const contents = path.join(work, "contents");
      const temporaryZip = path.join(work, "portable.zip");
      await run(sevenZip.path7za, ["x", "-y", `-o${contents}`, artifact]);
      await rename(contents, path.join(work, wrapper));
      await run(sevenZip.path7za, ["a", "-tzip", "-mx=9", temporaryZip, wrapper], { cwd: work });
      try {
        await rename(temporaryZip, artifact);
      } catch (error) {
        if (error?.code !== "EXDEV") throw error;
        await cp(temporaryZip, artifact, { force: true });
      }
      console.log(`[portable-zip] wrapped ${artifact} in ${wrapper}/`);
    } finally {
      await rm(work, { recursive: true, force: true });
    }
  }
  return [];
}
