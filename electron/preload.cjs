const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('flowGui', {
  platform: process.platform,
  version: process.versions.electron,
  backend: {
    baseUrl: (process.env.FLOWPILOT_BACKEND_URL || 'http://127.0.0.1:8765').replace(/\/$/, ''),
    getStatus: () => ipcRenderer.invoke('backend:get-status'),
    start: () => ipcRenderer.invoke('backend:start'),
    stop: () => ipcRenderer.invoke('backend:stop'),
    restart: () => ipcRenderer.invoke('backend:restart'),
    openLogs: () => ipcRenderer.invoke('backend:open-logs'),
    onStatus: (listener) => {
      const handler = (_event, status) => listener(status);
      ipcRenderer.on('backend:status', handler);
      return () => ipcRenderer.removeListener('backend:status', handler);
    },
  },
});
