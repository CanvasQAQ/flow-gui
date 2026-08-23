const { app, BrowserWindow, ipcMain, shell } = require('electron');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const isDev = !app.isPackaged;
const DEV_URL = process.env.VITE_DEV_SERVER_URL || 'http://localhost:5173';
const BACKEND_URL = (process.env.FLOWPILOT_BACKEND_URL || 'http://127.0.0.1:8765').replace(/\/$/, '');
const DEMO_MODE = process.env.FLOWPILOT_DEMO === '1';
const DEMO_SCENARIO = process.env.FLOWPILOT_DEMO_SCENARIO || 'default';
const HEALTH_INTERVAL_STARTING = 400;
const HEALTH_INTERVAL_READY = 5_000;
// Copy-back may take 300 s. Allow the Backend lifespan to drain before forcing exit.
const STOP_GRACE_MS = Number(process.env.FLOWPILOT_STOP_GRACE_MS || 315_000);

let backendProcess = null;
let healthTimer = null;
let stoppingPromise = null;
let quitting = false;
let quitDrainStarted = false;
let backendStatus = {
  state: 'stopped', message: DEMO_MODE ? 'Demo Backend is stopped' : 'Backend is stopped',
  pid: null, url: BACKEND_URL, ...(DEMO_MODE ? { mode: 'demo', scenario: DEMO_SCENARIO } : {}),
};

function commandParts() {
  const configured = process.env.FLOWPILOT_BACKEND_COMMAND;
  if (!configured) {
    const executable = isDev
      ? path.join(app.getAppPath(), 'backend', '.venv', 'bin', 'flow-backend')
      : path.join(process.resourcesPath, 'backend', 'bin', 'flow-backend');
    return [executable];
  }
  try {
    const parsed = JSON.parse(configured);
    if (Array.isArray(parsed) && parsed.length && parsed.every((part) => typeof part === 'string')) return parsed;
  } catch { /* A regular command string is supported below. */ }
  const parts = configured.match(/(?:[^\s"']+|"[^"]*"|'[^']*')+/g) || [];
  return parts.map((part) => part.replace(/^("|')|("|')$/g, ''));
}

function logsDirectory() {
  return process.env.FLOWPILOT_LOG_DIR || path.join(app.getPath('logs'), 'backend');
}

function publishBackendStatus(update) {
  backendStatus = { ...backendStatus, ...update, updatedAt: new Date().toISOString() };
  for (const win of BrowserWindow.getAllWindows()) win.webContents.send('backend:status', backendStatus);
}

async function backendHealthy() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 2_000);
  try {
    const response = await fetch(`${BACKEND_URL}/api/v1/health`, { signal: controller.signal });
    return response.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

function scheduleHealthCheck(delay) {
  clearTimeout(healthTimer);
  healthTimer = setTimeout(checkBackendHealth, delay);
}

async function checkBackendHealth() {
  const healthy = await backendHealthy();
  if (healthy) {
    publishBackendStatus({ state: 'ready', message: DEMO_MODE ? `Demo Backend · ${DEMO_SCENARIO}` : 'Backend ready', pid: backendProcess?.pid || null });
    scheduleHealthCheck(HEALTH_INTERVAL_READY);
  } else if (backendProcess && backendProcess.exitCode === null) {
    publishBackendStatus({ state: backendStatus.state === 'ready' ? 'degraded' : 'starting', message: 'Waiting for Backend health check', pid: backendProcess.pid });
    scheduleHealthCheck(HEALTH_INTERVAL_STARTING);
  } else if (!quitting) {
    publishBackendStatus({ state: backendStatus.state === 'stopped' ? 'stopped' : 'crashed', message: 'Backend is not responding', pid: null });
  }
}

async function startBackend() {
  if (backendProcess && backendProcess.exitCode === null) return backendStatus;
  if (await backendHealthy()) {
    publishBackendStatus({ state: 'ready', message: 'Connected to an existing Backend', pid: null, managed: false });
    scheduleHealthCheck(HEALTH_INTERVAL_READY);
    return backendStatus;
  }
  const parts = commandParts();
  if (!parts.length) throw new Error('FLOWPILOT_BACKEND_COMMAND is empty');
  const logDir = logsDirectory();
  fs.mkdirSync(logDir, { recursive: true });
  const logStream = fs.createWriteStream(path.join(logDir, 'backend.log'), { flags: 'a' });
  publishBackendStatus({ state: 'starting', message: DEMO_MODE ? 'Starting Demo Backend' : 'Starting Backend', pid: null, managed: true });
  try {
    const backendSource = path.join(app.getAppPath(), 'backend', 'src');
    const childEnvironment = { ...process.env, PYTHONUNBUFFERED: '1' };
    if (isDev) childEnvironment.PYTHONPATH = [backendSource, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter);
    const child = spawn(parts[0], parts.slice(1), {
      cwd: isDev ? path.join(app.getAppPath(), 'backend') : app.getAppPath(),
      env: childEnvironment,
      shell: false,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    backendProcess = child;
    child.stdout.pipe(logStream, { end: false });
    child.stderr.pipe(logStream, { end: false });
    child.once('error', (error) => {
      if (backendProcess === child) backendProcess = null;
      publishBackendStatus({ state: 'crashed', message: error.message, pid: null });
      logStream.end();
    });
    child.once('exit', (code, signal) => {
      if (backendProcess === child) backendProcess = null;
      logStream.end();
      if (!quitting && backendStatus.state !== 'stopping') {
        publishBackendStatus({ state: 'crashed', message: `Backend exited (${signal || code})`, pid: null });
      } else {
        publishBackendStatus({ state: 'stopped', message: 'Backend stopped', pid: null });
      }
    });
    publishBackendStatus({ state: 'starting', message: 'Waiting for Backend readiness', pid: child.pid, managed: true });
    scheduleHealthCheck(0);
    return backendStatus;
  } catch (error) {
    logStream.end();
    publishBackendStatus({ state: 'crashed', message: error.message, pid: null });
    throw error;
  }
}

function stopBackend({ force = false } = {}) {
  if (stoppingPromise) return stoppingPromise;
  const child = backendProcess;
  if (!child || child.exitCode !== null) {
    if (backendStatus.state === 'ready' && backendStatus.managed === false) {
      return Promise.reject(new Error('The connected Backend was not started by this application'));
    }
    publishBackendStatus({ state: 'stopped', message: 'Backend stopped', pid: null });
    return Promise.resolve(backendStatus);
  }
  publishBackendStatus({ state: 'stopping', message: force ? 'Force stopping Backend' : 'Backend draining in-flight work' });
  clearTimeout(healthTimer);
  stoppingPromise = new Promise((resolve) => {
    const finish = () => {
      clearTimeout(forceTimer);
      stoppingPromise = null;
      resolve(backendStatus);
    };
    child.once('exit', finish);
    const forceTimer = setTimeout(() => {
      if (child.exitCode === null) child.kill('SIGKILL');
    }, force ? 0 : STOP_GRACE_MS);
    child.kill(force ? 'SIGKILL' : 'SIGTERM');
  });
  return stoppingPromise;
}

async function restartBackend() {
  await stopBackend();
  return startBackend();
}

function registerBackendIpc() {
  ipcMain.handle('backend:get-status', () => backendStatus);
  ipcMain.handle('backend:start', () => startBackend());
  ipcMain.handle('backend:stop', () => stopBackend());
  ipcMain.handle('backend:restart', () => restartBackend());
  ipcMain.handle('backend:open-logs', async () => {
    fs.mkdirSync(logsDirectory(), { recursive: true });
    const error = await shell.openPath(logsDirectory());
    if (error) throw new Error(error);
    return true;
  });
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1100,
    minHeight: 720,
    backgroundColor: '#f5f5f5',
    title: 'RX SA Offset Calibration',
    show: false,
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  win.once('ready-to-show', () => win.show());
  win.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: 'deny' }; });
  if (isDev) {
    win.loadURL(DEV_URL);
    win.webContents.openDevTools({ mode: 'detach' });
  } else {
    win.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
  }
}

app.whenReady().then(() => {
  registerBackendIpc();
  createWindow();
  startBackend().catch(() => {});
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});

app.on('before-quit', (event) => {
  if (quitDrainStarted || !backendProcess || backendProcess.exitCode !== null) return;
  event.preventDefault();
  quitting = true;
  quitDrainStarted = true;
  clearTimeout(healthTimer);
  stopBackend().finally(() => app.quit());
});

app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
