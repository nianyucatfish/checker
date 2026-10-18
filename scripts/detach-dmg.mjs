import { setTimeout } from "node:timers/promises";

// macOS may briefly retain references after the last bundled process exits.
export async function detachDmg(mount, run, delay = setTimeout) {
  for (let attempt = 0; attempt < 6; attempt++) {
    try {
      await run("hdiutil", ["detach", mount]);
      return;
    } catch (error) {
      if (attempt === 5) break;
      await delay(2000);
    }
  }
  // This helper is only used for the smoke test's read-only image.
  await run("hdiutil", ["detach", "-force", mount]);
}
