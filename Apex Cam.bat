@echo off
REM Apex Cam launcher — starts the desktop app (which spawns the AI backend).
title Apex Cam
cd /d "%~dp0desktop"
REM Clear a globally-set var that would make electron run as plain Node.
set ELECTRON_RUN_AS_NODE=
node scripts\run-electron.mjs
