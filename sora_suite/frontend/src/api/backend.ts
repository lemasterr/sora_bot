import { AppConfig, BackendTaskEvent, ContentPayload, ContentState, HistoryEntry } from '../types';

type StartTaskRequest = {
  task: string;
  args?: string[];
  env?: Record<string, string>;
};

type BackendAPI = {
  loadConfig: () => Promise<AppConfig>;
  startTask: (payload: StartTaskRequest) => Promise<{ pid: number }>;
  stopTask: (pid: number) => Promise<boolean>;
  tailHistory: (limit?: number) => Promise<HistoryEntry[]>;
  loadContent: () => Promise<ContentState>;
  saveContent: (payload: ContentPayload) => Promise<boolean>;
  onTaskEvent: (cb: (event: BackendTaskEvent) => void) => () => void;
};

const backend: BackendAPI | undefined = typeof window !== 'undefined' ? (window as any).backend : undefined;

export const loadConfig = async () => backend?.loadConfig?.();
export const startTask = async (payload: StartTaskRequest) => backend?.startTask?.(payload);
export const stopTask = async (pid: number) => backend?.stopTask?.(pid);
export const tailHistory = async (limit?: number) => backend?.tailHistory?.(limit ?? 50);
export const loadContent = async () => backend?.loadContent?.();
export const saveContent = async (payload: ContentPayload) => backend?.saveContent?.(payload);
export const onTaskEvent = (cb: (event: BackendTaskEvent) => void) => backend?.onTaskEvent?.(cb) ?? (() => {});

