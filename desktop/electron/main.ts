import { app, BrowserWindow, session } from "electron";
import { spawn, ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";

// The Electron main process owns the window and supervises the Python AI
// backend lifecycle.
let backend: ChildProcess | null = null;

function startBackend() {
  if (process.env.APEXCAM_NO_BACKEND) return;
  const backendDir = path.resolve(__dirname, "../../backend");
  // Prefer the project venv that has the AI stack (insightface/onnxruntime);
  // fall back to a system uvicorn for plain dev.
  const venvPy = path.join(backendDir, ".venv311", "Scripts", "python.exe");
  try {
    if (existsSync(venvPy)) {
      backend = spawn(venvPy, ["-m", "uvicorn", "app.main:app", "--port", "8790"], {
        cwd: backendDir,
        stdio: "inherit",
      });
    } else {
      backend = spawn("uvicorn", ["app.main:app", "--port", "8790"], {
        cwd: backendDir,
        stdio: "inherit",
        shell: true,
      });
    }
  } catch {
    /* dev: backend started manually */
  }
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 1024,
    minHeight: 680,
    title: "Apex Cam",
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
