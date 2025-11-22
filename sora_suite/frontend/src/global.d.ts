import { AppConfig, BackendTaskEvent, ChromeProfileInfo, ContentPayload, ContentState, HistoryEntry } from './types';

type BackendAPI = {
  loadConfig: () => Promise<AppConfig>;
  startTask: (payload: { task: string; args?: string[]; env?: Record<string, string> }) => Promise<{ pid: number }>;
  stopTask: (pid: number) => Promise<boolean>;
  tailHistory: (limit?: number) => Promise<HistoryEntry[]>;
  loadContent: () => Promise<ContentState>;
  saveContent: (payload: ContentPayload) => Promise<boolean>;
  listChromeProfiles: () => Promise<ChromeProfileInfo[]>;
  launchChrome: (payload: { profileName: string; port?: number }) => Promise<{ pid: number; port: number }>;
  stopChrome: (pid: number) => Promise<boolean>;
  onTaskEvent: (cb: (event: BackendTaskEvent) => void) => () => void;
};

declare global {
  interface Window {
    backend?: BackendAPI;
  }
}

export {};
