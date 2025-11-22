import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('electronAPI', {
  runPython: (command: { task: string; payload?: Record<string, unknown> }) => ipcRenderer.invoke('run-python', command),
  onLog: (callback: (payload: { message: string; source: 'stdout' | 'stderr'; timestamp: number; pid?: number }) => void) => {
    const listener = (_event: any, payload: { message: string; source: 'stdout' | 'stderr'; timestamp: number; pid?: number }) => {
      callback(payload);
    };
    ipcRenderer.on('python-log', listener);
    return () => ipcRenderer.removeListener('python-log', listener);
  },
});
