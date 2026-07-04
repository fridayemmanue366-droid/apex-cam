// Launches the built Electron app with a clean environment.
//
// Guards against ELECTRON_RUN_AS_NODE being set globally on the machine, which
// otherwise makes electron.exe run as plain Node and crash with
// "Cannot read properties of undefined (reading 'whenReady')".
import { spawn } from "node:child_process";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const electronPath = require("electron"); // path to electron.exe when run under Node

delete process.env.ELECTRON_RUN_AS_NODE;

const child = spawn(electronPath, ["."], { stdio: "inherit", env: process.env });
child.on("close", (code) => process.exit(code ?? 0));
