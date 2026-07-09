// Chrome-style auto-update.
//
// The app asks our server for the latest version. If it's newer, we download the
// installer quietly in the background, then offer to restart and apply it (Inno
// Setup runs silently and relaunches). Never blocks the app; any failure is
// ignored so a bad network never breaks the user's session.
import { app, BrowserWindow, dialog } from "electron";
import { spawn } from "node:child_process";
import { createWriteStream, existsSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";

const SERVER = process.env.APEXCAM_SERVER || "https://api.apexcam.app";
const CHECK_EVERY_MS = 6 * 60 * 60 * 1000; // every 6 hours

type Manifest = { version: string; url: string; notes?: string; mandatory?: boolean };

/** true when `a` is a newer version than `b` (numeric dot compare, e.g. 1.2.10 > 1.2.9). */
function isNewer(a: string, b: string): boolean {
  const pa = a.split(".").map((n) => parseInt(n, 10) || 0);
  const pb = b.split(".").map((n) => parseInt(n, 10) || 0);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const x = pa[i] ?? 0;
    const y = pb[i] ?? 0;
    if (x !== y) return x > y;
  }
  return false;
}

async function download(url: string, version: string): Promise<string> {
  const dir = path.join(tmpdir(), "apexcam-update");
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  const dest = path.join(dir, `ApexCam-Setup-${version}.exe`);
  const res = await fetch(url);
  if (!res.ok || !res.body) throw new Error(`download failed: ${res.status}`);
  await pipeline(Readable.fromWeb(res.body as never), createWriteStream(dest));
  return dest;
}

/** Run the installer silently and quit so it can replace the app files. */
function install(installer: string) {
  spawn(installer, ["/VERYSILENT", "/NORESTART"], {
    detached: true,
    stdio: "ignore",
  }).unref();
  app.quit();
}

export async function checkForUpdate(win: BrowserWindow | null, silent = false) {
  try {
    const res = await fetch(`${SERVER}/update/latest?platform=win`);
    if (!res.ok) return;
    const m = (await res.json()) as Manifest;
    if (!m?.url || !m?.version || !isNewer(m.version, app.getVersion())) return;

    const installer = await download(m.url, m.version);

    if (m.mandatory) {
      install(installer);
      return;
    }
    const opts = {
      type: "info" as const,
      buttons: ["Restart & update", "Later"],
      defaultId: 0,
      cancelId: 1,
      title: "Update available",
      message: `Apex Cam ${m.version} is ready to install`,
      detail: m.notes || "The update installs in a few seconds and reopens the app.",
    };
    const { response } = win
      ? await dialog.showMessageBox(win, opts)
      : await dialog.showMessageBox(opts);
    if (response === 0) install(installer);
  } catch {
    if (!silent) {
      /* network/update problems must never break the app */
    }
  }
}

/** Check shortly after launch, then on a slow timer. */
export function startUpdateChecks(win: BrowserWindow) {
  setTimeout(() => void checkForUpdate(win, true), 20_000);
  setInterval(() => void checkForUpdate(win, true), CHECK_EVERY_MS);
}
