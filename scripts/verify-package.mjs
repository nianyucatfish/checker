import { readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const root = path.resolve(process.argv[2] || path.join(repo, "packaging", "staging"));
const forbiddenParts = new Set(["cache", "tests", "__pycache__", ".pytest_cache"]);
const forbiddenFiles = new Set(["config.toml", "llm_override.json", "requirements-dev.txt"]);
const violations = [];

async function walk(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const full = path.join(directory, entry.name);
    const relative = path.relative(root, full).replaceAll("\\", "/");
    const isBundledPython = relative === "python-runtime" || relative.startsWith("python-runtime/");
    if (forbiddenFiles.has(entry.name) || (!isBundledPython && (forbiddenParts.has(entry.name) || entry.name.endsWith(".pyc")))) {
      violations.push(relative);
    }
    if (entry.isDirectory()) await walk(full);
  }
}

async function requirePath(relative) {
  await stat(path.join(root, relative)).catch(() => violations.push(`MISSING:${relative}`));
}

async function requireOneOf(relatives) {
  for (const relative of relatives) {
    try {
      await stat(path.join(root, relative));
      return;
    } catch {}
  }
  violations.push(`MISSING_ONE_OF:${relatives.join(",")}`);
}

await walk(root).catch((error) => { throw new Error(`cannot inspect ${root}: ${error.message}`); });
await Promise.all([
  requireOneOf(["python-runtime/python.exe", "python-runtime/bin/python3"]),
  requirePath("backend/sidecar/serve.py"),
  requirePath("backend/sidecar/requirements.txt"),
  requirePath("prompts/agent_workflow.md"),
]);
const requirements = await readFile(path.join(root, "backend", "sidecar", "requirements.txt"), "utf8").catch(() => "");
if (/^\s*pytest\b/im.test(requirements)) violations.push("backend/sidecar/requirements.txt contains pytest");
if (violations.length) {
  console.error("Package content verification failed:\n" + violations.map((item) => `  - ${item}`).join("\n"));
  process.exitCode = 1;
} else {
  console.log(`Package content verified: ${root}`);
}
