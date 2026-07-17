// Level 2 auto-update: hot-swap just the FRONTEND UI (~230 KB), no 4 GB reinstall.
//
// The app's React UI is tiny and is where almost every change happens. This lets a
// new UI reach installed customers as a small background download. SAFETY: the app
// ALWAYS falls back to the bundled UI — a failed/partial download can never break
// it. The Python backend and the 3 GB models are untouched (they change rarely and
// still go through the full installer).
//
// Publish an update: rebuild the frontend, zip the CONTENTS of dist/ into
// server/app_bundle/frontend.zip, bump server/app_bundle/version.txt, push. Render
// redeploys and every app pulls the new UI on its next launch.
import { app } from "electron";
import { execFile } from "node:child_process";
import { createWriteStream, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";

const SERVER = process.env.APEXCAM_SERVER || "https://apexcam-api.onrender.com";

const bundledDir = () => path.join(__dirname, "..");                       // resources/app
const bundledIndex = () => path.join(bundledDir(), "dist", "index.html");
const cacheRoot = () => path.join(app.getPath("userData"), "webapp");

function readNum(file: string): number {
  try { return parseInt(readFileSync(file, "utf-8").trim(), 10) || 0; } catch { return 0; }
}
const bundledVersion = () => readNum(path.join(bundledDir(), "dist", "frontend-version.txt"));
const cacheVersion = () => readNum(path.join(cacheRoot(), "version.txt"));

/** The newest WORKING index.html: a downloaded UI if it's newer than the bundled
 *  one and actually on disk; otherwise the bundled one (always safe). */
export function resolveFrontend(): string {
  const cv = cacheVersion();
  const cached = path.join(cacheRoot(), String(cv), "index.html");
  if (cv > bundledVersion() && existsSync(cached)) return cached;
  return bundledIndex();
}

/** Download a newer UI if the server has one. Never throws; on any failure the app
 *  simply keeps using whatever resolveFrontend() already returns. Applies on the
 *  next launch. */
export async function checkFrontendUpdate(): Promise<void> {
  try {
    const res = await fetch(`${SERVER}/update/app`);
    if (!res.ok) return;
    const { version, url } = (await res.json()) as { version?: string; url?: string };
    const v = parseInt(String(version ?? "0"), 10) || 0;
    if (!v || !url) return;
    if (v <= Math.max(bundledVersion(), cacheVersion())) return;   // already have it
    const dir = path.join(cacheRoot(), String(v));
    mkdirSync(dir, { recursive: true });
    const zip = path.join(dir, "frontend.zip");
    const dl = await fetch(url);
    if (!dl.ok || !dl.body) return;
    await pipeline(Readable.fromWeb(dl.body as never), createWriteStream(zip));
    // Extract with Windows' built-in Expand-Archive (no extra dependency).
    await new Promise<void>((resolve, reject) =>
      execFile("powershell", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
        `Expand-Archive -Path '${zip}' -DestinationPath '${dir}' -Force`],
        (err) => (err ? reject(err) : resolve())));
    if (!existsSync(path.join(dir, "index.html"))) return;   // bad/partial — ignore, stay on old UI
    writeFileSync(path.join(cacheRoot(), "version.txt"), String(v));
  } catch { /* ignore — the bundled UI keeps working */ }
}
