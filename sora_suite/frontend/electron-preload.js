import { contextBridge, ipcRenderer } from 'electron';

const api = {
  loadConfig: () => ipcRenderer.invoke('config:load'),
  updateConfig: (payload) => ipcRenderer.invoke('config:update', payload),
  startTask: (payload) => ipcRenderer.invoke('task:start', payload),
  stopTask: (pid) => ipcRenderer.invoke('task:stop', pid),
  tailHistory: (limit) => ipcRenderer.invoke('history:tail', limit),
  loadContent: () => ipcRenderer.invoke('content:load'),
  saveContent: (payload) => ipcRenderer.invoke('content:save', payload),
  onTaskEvent: (callback) => {
    const listener = (_event, data) => callback(data);
    ipcRenderer.on('task:event', listener);
    return () => ipcRenderer.removeListener('task:event', listener);
  },
};

contextBridge.exposeInMainWorld('backend', api);
