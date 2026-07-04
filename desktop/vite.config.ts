import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import electron from "vite-plugin-electron";

// Some environments set ELECTRON_RUN_AS_NODE=1 globally, which makes electron.exe
// behave as plain Node (require("electron") returns a path, not the API) and the
// app crashes on launch. Clear it so any Electron process spawned from here runs
// as a real Electron app.
delete process.env.ELECTRON_RUN_AS_NODE;

// Vite drives the React renderer; vite-plugin-electron builds the Electron
// main + preload processes alongside it.
export default defineConfig({
  plugins: [
    react(),
    electron([
      { entry: "electron/main.ts" },
      { entry: "electron/preload.ts", onstart: (opts) => opts.reload() },
    ]),
  ],
  server: { port: 5173 },
});
