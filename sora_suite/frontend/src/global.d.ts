import { AppConfig, BackendTaskEvent, ContentPayload, ContentState, HistoryEntry } from './types';

type BackendAPI = {
  loadConfig: () => Promise<AppConfig>;
  startTask: (payload: { task: string; args?: string[]; env?: Record<string, string> }) => Promise<{ pid: number }>;
  stopTask: (pid: number) => Promise<boolean>;
  tailHistory: (limit?: number) => Promise<HistoryEntry[]>;
  loadContent: () => Promise<ContentState>;
  saveContent: (payload: ContentPayload) => Promise<boolean>;
  onTaskEvent: (cb: (event: BackendTaskEvent) => void) => () => void;
  updateConfig: (payload: Partial<AppConfig>) => Promise<AppConfig>;
};

declare global {
  interface Window {
    backend?: BackendAPI;
  }
}

export {};
