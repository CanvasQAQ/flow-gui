const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('flowGui', {
  platform: process.platform,
  version: process.versions.electron,
});
