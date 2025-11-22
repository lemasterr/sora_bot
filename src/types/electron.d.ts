export interface RunPythonCommand {
  task: 'pipeline' | 'autogen' | 'downloader' | 'watermark' | 'youtube' | 'tiktok';
  payload?: Record<string, unknown>;
}

export interface ElectronLogPayload {
  message: string;
  source: 'stdout' | 'stderr';
  timestamp: number;
  pid?: number;
}

declare global {
  interface Window {
    electronAPI?: {
      runPython: (command: RunPythonCommand) => Promise<{ code?: number } | void>;
      onLog: (callback: (payload: ElectronLogPayload) => void) => () => void;
    };
  }
}
