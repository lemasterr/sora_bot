export type WorkspaceStatus = 'idle' | 'running' | 'stopped' | 'error';

export interface WorkspaceLog {
  timestamp: string;
  level: 'info' | 'warn' | 'error';
  message: string;
}

export interface WorkspaceProfile {
  id: string;
  name: string;
  port: number;
  status: WorkspaceStatus;
  lastLog?: WorkspaceLog;
  downloadLimit?: number;
  mergeLimit?: number;
  chromeProfile?: string;
  chromePid?: number;
}

export interface AutomatorStep {
  id: string;
  type: 'generate' | 'wait' | 'download' | 'custom';
  label: string;
  params?: Record<string, string | number | boolean>;
  profileIds: string[];
}

export interface AutomatorSequence {
  id: string;
  name: string;
  description?: string;
  steps: AutomatorStep[];
}

export interface WatermarkCheckItem {
  id: string;
  fileName: string;
  status: 'pending' | 'processing' | 'clean' | 'watermark_found';
  previewUrl?: string;
}

export interface ContentFile {
  id: string;
  label: string;
  value: string;
  path: string;
}

export interface TitleEntry {
  profileId: string;
  title: string;
  videoId?: string;
}

export interface TelegramConfig {
  botToken: string;
  chatId: string;
  notificationsEnabled: boolean;
  lastNotices: string[];
}

export interface DirectorySettings {
  rawDir: string;
  mergedDir: string;
  logsDir: string;
  assetsDir: string;
  tempDir?: string;
}

export interface FFmpegSettings {
  codec: string;
  preset: string;
  crf?: number;
  extraArgs?: string;
}

export interface ImageGenSettings {
  model: string;
  apiKey: string;
  width?: number;
  height?: number;
  stylePreset?: string;
}

export interface AutogenSettings {
  delayMs: number;
  maxConcurrent: number;
  retryLimit: number;
}

export interface InterfaceSettings {
  theme: 'dark' | 'light';
  density: 'compact' | 'comfortable';
  showRightPanel: boolean;
}

export interface MaintenanceSettings {
  clearCache: boolean;
  autoUpdate: boolean;
}

export interface SettingsState {
  directories: DirectorySettings;
  ffmpeg: FFmpegSettings;
  imageGen: ImageGenSettings;
  autogen: AutogenSettings;
  interface: InterfaceSettings;
  maintenance: MaintenanceSettings;
}

export interface AppConfigSession {
  id?: string;
  name?: string;
  chrome_profile?: string;
  prompt_profile?: string;
  cdp_port?: number;
  prompts_file?: string;
  image_prompts_file?: string;
  submitted_log?: string;
  failed_log?: string;
  notes?: string;
  auto_launch_chrome?: boolean;
  auto_launch_autogen?: string;
  download_dir?: string;
  clean_dir?: string;
  titles_file?: string;
  cursor_file?: string;
  max_videos?: number;
  open_drafts?: boolean;
}

export interface AppConfig {
  autogen?: { sessions?: AppConfigSession[]; workdir?: string; image_prompts_file?: string; prompts_file?: string };
  downloader?: { max_videos?: number; open_drafts?: boolean; workdir?: string; entry?: string };
  titles_file?: string;
  youtube?: Record<string, unknown>;
  tiktok?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ChromeProfileInfo {
  name: string;
  user_data_dir: string;
  profile_directory: string;
  cdp_port?: number;
}

export interface ErrorEvent {
  id: string;
  timestamp: string;
  level: 'error' | 'fatal';
  message: string;
  stack?: string;
  context?: Record<string, unknown>;
}

export interface HistoryEntry {
  id: string;
  timestamp: string;
  actor: string;
  action: string;
  status: 'pending' | 'running' | 'success' | 'failed';
  details?: string;
}

export interface BackendTaskEvent {
  kind: 'start' | 'log' | 'exit';
  pid: number;
  task: string;
  channel?: 'stdout' | 'stderr';
  line?: string;
  code?: number | null;
  signal?: string | null;
}

export interface ContentState {
  prompts: string;
  imagePrompts: string;
  titles: string;
  promptsPath: string;
  imagePromptsPath: string;
  titlesPath: string;
  config?: AppConfig;
  titlesByProfile?: Record<string, string>;
  promptsByProfile?: Record<string, string>;
  imagePromptsByProfile?: Record<string, string>;
  sessionPaths?: Record<string, { promptsPath: string; imagePromptsPath: string; titlesPath: string }>;
}

export interface ContentPayload {
  prompts?: string;
  imagePrompts?: string;
  titles?: string;
  promptsPath?: string;
  imagePromptsPath?: string;
  titlesPath?: string;
  titlesByProfile?: Record<string, string>;
  promptsByProfile?: Record<string, string>;
  imagePromptsByProfile?: Record<string, string>;
  sessionPaths?: Record<string, { promptsPath: string; imagePromptsPath: string; titlesPath: string }>;
}
