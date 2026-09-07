// Level 3 auto-update: hot-swap the PYTHON BACKEND source (~500 KB), no reinstall.
//
// Why this exists: the UI already updates itself (appUpdater.ts), but every BACKEND
// fix was stranded in the installer. Shipping a 30-line Python change — the camera
// auto-pick, a metering fix, a pipeline tweak — meant asking every customer to
// re-download a 3 GB installer, so in practice those fixes never reached anyone.
// The backend is plain source code and about the same size as the UI, so it can
// travel the same way.
//
// SAFETY, in priority order. This runs on machines we cannot debug, so every stage
// is written to fail into "keep whatever already works":
//   1. ONLY backend/app/** is ever replaced. backend/data (the customer's own face
//      library) and backend/models (3 GB) are never read, moved or deleted.
//   2. Download, extract and validate all happen in userData. Nothing inside the
//      installation is touched until a complete, verified copy exists on disk.
//   3. Applying is a local file copy at LAUNCH, before Python starts — never while
//      the backend is running and holding the camera.
//   4. The previous app/ is kept as app.prev, and main.ts restores it if the new
//      backend fails to answer /health.
//   5. A version that failed to start is recorded and never fetched again, so a bad
//      publish cannot put the app into an update loop.
//
// VERSION BOOKKEEPING: <backend>/backend-version.txt is the single source of truth
// for what is on disk. The installer ships it; a successful apply overwrites it.
// That makes it self-correcting — installing a newer full build resets the file to
// that build's baseline, so we can never believe we have code we do not have.
import { app } from "electron";
import { execFile } from "node:child_process";
import {
  createWriteStream, cpSync, existsSync, mkdirSync, readFileSync, renameSync,
  rmSync, writeFileSync,
} from "node:fs";
import path from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";

const SERVER = process.env.APEXCAM_SERVER || "https://apexcam-api.onrender.com";

const stagingRoot = () => path.join(app.getPath("userData"), "backend-update");
const readyFile = () => path.join(stagingRoot(), "ready.txt");
const rejectedFile = () => path.join(stagingRoot(), "rejected.txt");

function readNum(file: string): number {
  try { return parseInt(readFileSync(file, "utf-8").trim(), 10) || 0; } catch { return 0; }
}

/** What backend source version is actually sitting in the install right now. */
export function installedBackendVersion(backendDir: string): number {
  return readNum(path.join(backendDir, "backend-version.txt"));
}

/** Versions that were applied but failed to start — never fetch these again. */
function rejectedVersions(): number[] {
  try {
    return readFileSync(rejectedFile(), "utf-8").split(/\s+/)
      .map((n) => parseInt(n, 10)).filter((n) => n > 0);
  } catch { return []; }
}

function markRejected(version: number): void {
  const all = new Set(rejectedVersions());
  all.add(version);
  try {
    mkdirSync(stagingRoot(), { recursive: true });
    writeFileSync(rejectedFile(), [...all].join("\n"));
  } catch { /* best effort */ }
}

function clearStaging(): void {
  // Keep rejected.txt — it is the memory that stops us re-applying a bad build.
  const keep = rejectedVersions();
  try { rmSync(stagingRoot(), { recursive: true, force: true }); } catch { /* ignore */ }
  if (keep.length) {
    try {
      mkdirSync(stagingRoot(), { recursive: true });
      writeFileSync(rejectedFile(), keep.join("\n"));
    } catch { /* ignore */ }
  }
}

/** A staged payload is only usable if the whole package really extracted. */
function stagedLooksComplete(dir: string): boolean {
  return existsSync(path.join(dir, "app", "main.py"))
    && existsSync(path.join(dir, "app", "core", "pipeline.py"));
}

/**
 * Apply a staged backend update. Called at launch BEFORE Python starts. Returns the
 * version applied, or null if nothing was applied (the overwhelmingly common case).
 * Never throws: on any failure the previous backend is restored and the app carries
 * on exactly as it did before.
 */
export function applyStagedBackendUpdate(backendDir: string): number | null {
  let version = 0;
  try {
    version = readNum(readyFile());
    if (!version) return null;
    if (version <= installedBackendVersion(backendDir)) { clearStaging(); return null; }
    if (rejectedVersions().includes(version)) { clearStaging(); return null; }

    const staged = path.join(stagingRoot(), String(version));
    if (!stagedLooksComplete(staged)) { clearStaging(); return null; }

    const live = path.join(backendDir, "app");
    const prev = path.join(backendDir, "app.prev");
    if (!existsSync(live)) { clearStaging(); return null; }

    try { rmSync(prev, { recursive: true, force: true }); } catch { /* ignore */ }
    renameSync(live, prev);                        // keep the known-good copy
    try {
      cpSync(path.join(staged, "app"), live, { recursive: true });
    } catch {
      // Copy failed midway — put the old backend back and forget this update.
      try { rmSync(live, { recursive: true, force: true }); } catch { /* ignore */ }
      try { renameSync(prev, live); } catch { /* ignore */ }
      markRejected(version);
      clearStaging();
      return null;
    }
    writeFileSync(path.join(backendDir, "backend-version.txt"), String(version));
    clearStaging();
    return version;
  } catch {
    if (version) markRejected(version);
    clearStaging();
    return null;
  }
}

/**
 * Put the previous backend back after a failed start. Called by main.ts when a
 * freshly-applied backend never answers /health.
 */
export function rollbackBackend(backendDir: string, badVersion: number): boolean {
  try {
    const live = path.join(backendDir, "app");
    const prev = path.join(backendDir, "app.prev");
    if (!existsSync(path.join(prev, "main.py"))) return false;
    try { rmSync(live, { recursive: true, force: true }); } catch { /* ignore */ }
    renameSync(prev, live);
    // Back to whatever the installer shipped; 0 makes the next check re-offer it.
    writeFileSync(path.join(backendDir, "backend-version.txt"), "0");
    markRejected(badVersion);
    return true;
  } catch { return false; }
}

/** Drop the rollback copy once the new backend has proved itself. */
export function dropBackendBackup(backendDir: string): void {
  try { rmSync(path.join(backendDir, "app.prev"), { recursive: true, force: true }); }
  catch { /* harmless if it lingers */ }
}

/**
 * Check for a newer backend and STAGE it. Runs in the background after launch and
 * applies on the NEXT start — the same rule the UI updater follows, so a download
 * can never disturb a running session. Never throws.
 */
export async function checkBackendUpdate(backendDir: string): Promise<void> {
  try {
    const res = await fetch(`${SERVER}/update/backend`);
    if (!res.ok) return;
    const { version, url } = (await res.json()) as { version?: string; url?: string };
    const v = parseInt(String(version ?? "0"), 10) || 0;
    if (!v || !url) return;
    if (v <= installedBackendVersion(backendDir)) return;      // already current
    if (rejectedVersions().includes(v)) return;                // known bad
    if (readNum(readyFile()) === v) return;                    // already staged

    const dir = path.join(stagingRoot(), String(v));
    try { rmSync(dir, { recursive: true, force: true }); } catch { /* ignore */ }
    mkdirSync(dir, { recursive: true });

    const zip = path.join(dir, "backend.zip");
    const dl = await fetch(url);
    if (!dl.ok || !dl.body) return;
    await pipeline(Readable.fromWeb(dl.body as never), createWriteStream(zip));

    // Extract with Windows' built-in Expand-Archive (no extra dependency), exactly
    // as the frontend updater does.
    await new Promise<void>((resolve, fail) =>
      execFile("powershell", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
        `Expand-Archive -Path '${zip}' -DestinationPath '${dir}' -Force`],
        (err) => (err ? fail(err) : resolve())));

    if (!stagedLooksComplete(dir)) {                // partial/corrupt — discard
      try { rmSync(dir, { recursive: true, force: true }); } catch { /* ignore */ }
      return;
    }
    writeFileSync(readyFile(), String(v));          // armed for the next launch
  } catch { /* ignore — the installed backend keeps running */ }
}
