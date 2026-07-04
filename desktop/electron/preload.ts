import { contextBridge } from "electron";

// Minimal, safe bridge. The renderer talks to the AI backend over HTTP/WS
// directly; this bridge is reserved for privileged desktop actions (choosing
// files, opening the recordings folder, etc.) added in later phases.
contextBridge.exposeInMainWorld("emycam", {
  version: "0.1.0",
  platform: process.platform,
});
