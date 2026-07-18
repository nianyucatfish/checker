import { createHash } from "node:crypto";
import { createWriteStream, promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import yazl from "yazl";
import {
  UPDATE_PRODUCT,
  UPDATE_SCHEMA,
  UPDATE_STATUS,
  createApplyPlan,
  executeApplyPlan,
  generateMacApplyScript,
  generateWindowsApplyScript,
  inspectAndStageOfflineUpdate,
  validateArchivePath,
  type UpdateManifest,
} from "./offlineUpdate";

const temporaryRoots: string[] = [];
const hash = (value: Buffer) => createHash("sha256").update(value).digest("hex");
const target = { platform: "windows" as const, arch: "x64" as const, currentVersion: "1.0.0" };

async function temporaryRoot(): Promise<string> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "audio-qc-offline-update-"));
  temporaryRoots.push(root);
  return root;
}

afterEach(async () => {
  await Promise.all(temporaryRoots.splice(0).map((root) => fs.rm(root, { recursive: true, force: true })));
});

function manifestFor(payload: Buffer, overrides: Partial<UpdateManifest> = {}): UpdateManifest {
  return {
    schema: UPDATE_SCHEMA,
    product: UPDATE_PRODUCT,
    version: "2.0.0",
    platform: "windows",
    arch: "x64",
    status: UPDATE_STATUS,
    archiveRoot: "Audio QC 2.0.0",
    managedRoots: ["resources"],
    files: [{ path: "resources/app.asar", type: "file", mode: 0o644, sha256: hash(payload) }],
    ...overrides,
  };
}

async function writeZip(root: string, entries: Record<string, string | Buffer>): Promise<string> {
  const destination = path.join(root, `update-${Date.now()}-${Math.random()}.zip`);
  const zip = new yazl.ZipFile();
  for (const [name, value] of Object.entries(entries)) zip.addBuffer(Buffer.isBuffer(value) ? value : Buffer.from(value), name);
  zip.end();
  await new Promise<void>((resolve, reject) => {
    const output = createWriteStream(destination);
    zip.outputStream.once("error", reject).pipe(output).once("error", reject).once("close", resolve);
  });
  return destination;
}

async function makeUpdate(overrides: Partial<UpdateManifest> = {}, payload = Buffer.from("new app payload")) {
  const root = await temporaryRoot();
  const manifest = manifestFor(payload, overrides);
  const prefix = `${manifest.archiveRoot}/`;
  const zipPath = await writeZip(root, {
    [`${prefix}update-manifest.json`]: JSON.stringify(manifest),
    [`${prefix}resources/app.asar`]: payload,
  });
  return { root, zipPath, manifest };
}

describe("inspectAndStageOfflineUpdate", () => {
  it("accepts a matching ZIP and stages the exact verified payload", async () => {
    const update = await makeUpdate();
    const staged = await inspectAndStageOfflineUpdate(update.zipPath, path.join(update.root, "staging"), target);
    expect(staged.manifest).toEqual(update.manifest);
    expect(await fs.readFile(path.join(staged.payloadDir, "resources", "app.asar"), "utf8")).toBe("new app payload");
  });

  it("rejects the wrong platform", async () => {
    const update = await makeUpdate({ platform: "macos", managedRoots: ["Audio QC.app"], files: [{ path: "Audio QC.app/Contents/MacOS/Audio QC", type: "file", mode: 0o644, sha256: "0".repeat(64) }] });
    await expect(inspectAndStageOfflineUpdate(update.zipPath, path.join(update.root, "staging"), target)).rejects.toThrow("Wrong update platform");
  });

  it("rejects the wrong architecture", async () => {
    const update = await makeUpdate({ arch: "arm64" });
    await expect(inspectAndStageOfflineUpdate(update.zipPath, path.join(update.root, "staging"), target)).rejects.toThrow("Wrong update architecture");
  });

  it("rejects a missing manifest", async () => {
    const root = await temporaryRoot();
    const zipPath = await writeZip(root, { "Audio QC 2.0.0/resources/app.asar": "payload" });
    await expect(inspectAndStageOfflineUpdate(zipPath, path.join(root, "staging"), target)).rejects.toThrow("manifest is missing");
  });

  it("rejects a legacy flat ZIP and a mismatched outer directory", async () => {
    const update = await makeUpdate();
    const flatZip = await writeZip(update.root, {
      "update-manifest.json": JSON.stringify(update.manifest),
      "resources/app.asar": "new app payload",
    });
    await expect(inspectAndStageOfflineUpdate(flatZip, path.join(update.root, "flat-stage"), target)).rejects.toThrow("top-level application directory");

    const wrongRootZip = await writeZip(update.root, {
      "Other Folder/update-manifest.json": JSON.stringify(update.manifest),
      "Other Folder/resources/app.asar": "new app payload",
    });
    await expect(inspectAndStageOfflineUpdate(wrongRootZip, path.join(update.root, "wrong-root-stage"), target)).rejects.toThrow("root does not match");
  });

  it("allows a production-sized manifest below the bounded limit", async () => {
    const update = await makeUpdate();
    const prefix = `${update.manifest.archiveRoot}/`;
    const zipPath = await writeZip(update.root, {
      [`${prefix}update-manifest.json`]: JSON.stringify(update.manifest).padEnd(1_200_000, " "),
      [`${prefix}resources/app.asar`]: "new app payload",
    });
    await expect(inspectAndStageOfflineUpdate(zipPath, path.join(update.root, "large-manifest"), {
      ...target,
      limits: { maxCompressionRatio: 20_000 },
    })).resolves.toMatchObject({
      manifest: update.manifest,
    });
  });

  it("rejects a hash mismatch and removes failed staging", async () => {
    const update = await makeUpdate({ files: [{ path: "resources/app.asar", type: "file", mode: 0o644, sha256: "0".repeat(64) }] });
    const stagingParent = path.join(update.root, "staging");
    await expect(inspectAndStageOfflineUpdate(update.zipPath, stagingParent, target)).rejects.toThrow("SHA-256 mismatch");
    expect(await fs.readdir(stagingParent)).toEqual([]);
  });

  it("rejects traversal, absolute, drive, UNC, and backslash paths", () => {
    for (const malicious of ["../evil", "safe/../../evil", "/absolute", "C:/absolute", "//server/share", "safe\\..\\evil"]) {
      expect(() => validateArchivePath(malicious), malicious).toThrow();
    }
  });

  it("rejects an undeclared extra file and forbidden persistent paths", async () => {
    const update = await makeUpdate();
    const prefix = `${update.manifest.archiveRoot}/`;
    const zipPath = await writeZip(update.root, {
      [`${prefix}update-manifest.json`]: JSON.stringify(update.manifest),
      [`${prefix}resources/app.asar`]: "new app payload",
      [`${prefix}extra.txt`]: "surprise",
    });
    await expect(inspectAndStageOfflineUpdate(zipPath, path.join(update.root, "extra-stage"), target)).rejects.toThrow("file set");
    for (const forbidden of ["data/config.toml", "cache/item", "config.toml", "resources/logs/output.txt"]) {
      expect(() => validateArchivePath(forbidden), forbidden).toThrow("forbidden");
    }
  });
});

describe("apply planning and rollback", () => {
  it("plans Windows managed-root replacement and preserves data", async () => {
    const root = await temporaryRoot();
    const payloadDir = path.join(root, "payload");
    const installRoot = path.join(root, "install");
    await fs.mkdir(path.join(payloadDir, "resources"), { recursive: true });
    await fs.mkdir(path.join(installRoot, "resources"), { recursive: true });
    await fs.mkdir(path.join(installRoot, "data"), { recursive: true });
    await fs.writeFile(path.join(payloadDir, "resources", "app.asar"), "new");
    await fs.writeFile(path.join(installRoot, "resources", "app.asar"), "old");
    await fs.writeFile(path.join(installRoot, "data", "config.toml"), "keep");
    const manifest = manifestFor(Buffer.from("new"));
    const plan = createApplyPlan({
      platform: "windows",
      staged: { stagingDir: root, payloadDir, manifest },
      previousManifest: manifestFor(Buffer.from("old")),
      installRoot,
      previousPath: path.join(root, "previous"),
      rollbackPath: path.join(root, "rollback"),
    });
    await executeApplyPlan(plan);
    expect(await fs.readFile(path.join(installRoot, "resources", "app.asar"), "utf8")).toBe("new");
    expect(await fs.readFile(path.join(installRoot, "data", "config.toml"), "utf8")).toBe("keep");
    expect(await fs.readFile(path.join(root, "previous", "resources", "app.asar"), "utf8")).toBe("old");
  });

  it("restores Windows previous roots when replacement fails", async () => {
    const root = await temporaryRoot();
    const payloadDir = path.join(root, "payload");
    const installRoot = path.join(root, "install");
    await fs.mkdir(path.join(installRoot, "resources"), { recursive: true });
    await fs.mkdir(payloadDir);
    await fs.writeFile(path.join(installRoot, "resources", "app.asar"), "old");
    await expect(executeApplyPlan({
      kind: "windows-managed-roots", installRoot, payloadRoot: payloadDir,
      previousRoot: path.join(root, "previous"), rollbackRoot: path.join(root, "rollback"),
      previousManagedRoots: ["resources"], nextManagedRoots: ["resources"],
    })).rejects.toThrow("missing");
    expect(await fs.readFile(path.join(installRoot, "resources", "app.asar"), "utf8")).toBe("old");
  });

  it("embeds Windows paths as base64 data, not PowerShell source", () => {
    const hostile = "C:\\Audio QC\\it's $(throw 'injected')";
    const script = generateWindowsApplyScript({
      waitForPid: 42,
      plan: {
        kind: "windows-managed-roots", installRoot: hostile, payloadRoot: `${hostile}\\payload`,
        previousRoot: `${hostile}\\previous`, rollbackRoot: `${hostile}\\rollback`,
        previousManagedRoots: ["resources"], nextManagedRoots: ["resources"],
      },
      relaunch: { command: `${hostile}\\Audio QC.exe`, args: ["quote\"arg", "space arg"] },
    });
    expect(script).not.toContain(hostile);
    expect(script).toContain("FromBase64String");
    expect(script).toContain("Get-Process");
    expect(script).toContain("Remove-Item -LiteralPath $PSCommandPath");
  });

  it("shell-quotes macOS paths and relaunch arguments", () => {
    const script = generateMacApplyScript({
      waitForPid: 43,
      plan: {
        kind: "mac-whole-app",
        appPath: "/Applications/Audio QC's $(touch nope).app",
        stagedAppPath: "/tmp/stage space/Audio QC.app",
        previousPath: "/tmp/previous's app",
        rollbackPath: "/tmp/rollback; false",
      },
      relaunch: { command: "/usr/bin/open", args: ["-a", "Audio QC's app"] },
    });
    expect(script).toContain("'/Applications/Audio QC'\"'\"'s $(touch nope).app'");
    expect(script).toContain("'/usr/bin/open' '-a' 'Audio QC'\"'\"'s app'");
    expect(script).toContain("trap cleanup EXIT HUP INT TERM");
    expect(script).toContain("mv -- \"$previous\" \"$rollback\"");
  });

  it("rejects a helper whose native platform does not match the plan", () => {
    const windowsPlan = {
      kind: "windows-managed-roots" as const, installRoot: "C:\\app", payloadRoot: "C:\\payload",
      previousRoot: "C:\\previous", rollbackRoot: "C:\\rollback",
      previousManagedRoots: ["resources"], nextManagedRoots: ["resources"],
    };
    expect(() => generateMacApplyScript({ plan: windowsPlan, waitForPid: 1 })).toThrow("macOS helper requires");
  });
});
