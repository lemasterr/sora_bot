type NodeListener = (...args: any[]) => void;

declare const __dirname: string;
declare const process: {
  env: Record<string, string | undefined>;
  platform: string;
  pid?: number;
  on: (event: string, listener: NodeListener) => void;
};

declare class Buffer {
  constructor(...args: any[]);
  toString: (...args: any[]) => string;
}

declare module 'node:path' {
  const path: any;
  export = path;
}

declare module 'node:child_process' {
  export const spawn: any;
}

declare module 'electron' {
  export const app: any;
  export const BrowserWindow: any;
  export const ipcMain: any;
  export const shell: any;
  export const contextBridge: any;
  export const ipcRenderer: any;
  export namespace Electron {
    interface IpcRendererEvent {}
  }
}
