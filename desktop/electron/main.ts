import { app, BrowserWindow, session } from "electron";
import { spawn, ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";

// The Electron main process owns the window and supervises the Python AI
// backend lifecycle.
let backend: ChildProcess | null = null;

// Locate the Python + backend to run. In a packaged install we ship a private
// (bundled) Python next to the app, so the customer never installs Python. In
// dev we fall back to the project venv, then a system uvicorn.
function resolveBackend(): { py: string | null; dir: string } {
  const exeDir = path.dirname(app.getPath("exe"));
  // 1) Bundled runtime (consumer install): <appRoot>/python + <appRoot>/backend
  const bundledPy = path.join(exeDir, "python", "python.exe");
  const bundledDir = path.join(exeDir, "backend");
  if (existsSync(bundledPy) && existsSync(bundledDir)) {
    return { py: bundledPy, dir: bundledDir };
  }
  // 2) Dev venv
  const devDir = path.resolve(__dirname, "../../backend");
  const venvPy = path.join(devDir, ".venv311", "Scripts", "python.exe");
  if (existsSync(venvPy)) return { py: venvPy, dir: devDir };
  // 3) System uvicorn (last resort)
  return { py: null, dir: devDir };
}

function startBackend() {
  if (process.env.APEXCAM_NO_BACKEND) return;
  const { py, dir } = resolveBackend();
  try {
    if (py) {
      backend = spawn(py, ["-m", "uvicorn", "app.main:app", "--port", "8790"], {
        cwd: dir,
        stdio: "inherit",
      });
    } else {
      backend = spawn("uvicorn", ["app.main:app", "--port", "8790"], {
        cwd: dir,
        stdio: "inherit",
        shell: true,
      });
    }
  } catch {
    /* dev: backend started manually */
  }
}

function appIcon(): string | undefined {
  // Bundle: resources/app/apexcam.ico; dev: desktop/build/apexcam.ico.
  for (const p of [
    path.join(__dirname, "..", "apexcam.ico"),
    path.join(__dirname, "..", "build", "apexcam.ico"),
  ]) {
    if (existsSync(p)) return p;
  }
  return undefined;
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 1024,
    minHeight: 680,
    title: "Apex Cam",
    icon: appIcon(),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  const devUrl = process.env.VITE_DEV_SERVER_URL;
  if (devUrl) {
    win.loadURL(devUrl);
  } else {
    win.loadFile(path.join(__dirname, "../dist/index.html"));
  }
}

app.whenReady().then(() => {
  // The renderer needs camera/mic for live preview; grant only media requests.
  session.defaultSession.setPermissionRequestHandler((_wc, permission, callback) => {
    callback(permission === "media");
  });
  startBackend();
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  backend?.kill();
  if (process.platform !== "darwin") app.quit();
});
