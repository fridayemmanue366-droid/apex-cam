@echo off
REM Apex Cam launcher - starts the desktop app (which spawns the AI backend).
title Apex Cam
cd /d "%~dp0desktop"

REM Clear a globally-set var that would make electron run as plain Node.
set ELECTRON_RUN_AS_NODE=

REM Find Node even when it isn't on PATH (common on fresh installs).
set "NODE_EXE=node"
where node >nul 2>nul
if errorlevel 1 (
  if exist "%ProgramFiles%\nodejs\node.exe" (
    set "NODE_EXE=%ProgramFiles%\nodejs\node.exe"
  ) else if exist "%ProgramFiles(x86)%\nodejs\node.exe" (
    set "NODE_EXE=%ProgramFiles(x86)%\nodejs\node.exe"
  ) else if exist "%LOCALAPPDATA%\Programs\nodejs\node.exe" (
    set "NODE_EXE=%LOCALAPPDATA%\Programs\nodejs\node.exe"
  ) else (
    echo Node.js was not found. Please run install.ps1 first, or install Node.js.
    pause
    exit /b 1
  )
)

REM Build the UI once if it hasn't been built yet.
if not exist "dist-electron\main.js" (
  echo First run - building the app...
  call "%NODE_EXE%" node_modules\vite\bin\vite.js build
)

"%NODE_EXE%" scripts\run-electron.mjs
