import React, { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Cpu,
  Download,
  Play,
  Settings,
  Terminal,
  Zap,
} from 'lucide-react';
import type { ElectronLogPayload, RunPythonCommand } from './types/electron';

interface LogEntry extends ElectronLogPayload {
  id: string;
}

const useCountdown = (trigger: boolean, seconds = 2) => {
  const [remaining, setRemaining] = useState(seconds);

  useEffect(() => {
    if (!trigger) return;
    const timer = setInterval(() => {
      setRemaining((prev) => {
        if (prev <= 1) {
          clearInterval(timer);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [trigger, seconds]);

  return remaining;
};

const App: React.FC = () => {
  const [promptText, setPromptText] = useState('A cinematic, neon-lit lab with holographic UI panels and floating drones.');
  const [cdpEndpoint, setCdpEndpoint] = useState('http://localhost:9222');
  const [downloadsDir, setDownloadsDir] = useState('downloads');
  const [promptsFile, setPromptsFile] = useState('');
  const [submittedLog, setSubmittedLog] = useState('');
  const [failedLog, setFailedLog] = useState('');
  const [imagePromptsFile, setImagePromptsFile] = useState('');
  const [titlesFile, setTitlesFile] = useState('');
  const [titlesCursorFile, setTitlesCursorFile] = useState('');
  const [maxVideos, setMaxVideos] = useState(3);
  const [runDownloader, setRunDownloader] = useState(true);
  const [openDraftsFirst, setOpenDraftsFirst] = useState(true);
  const [imagesOnly, setImagesOnly] = useState(false);
  const [attachToSora, setAttachToSora] = useState(true);
  const [appConfigPath, setAppConfigPath] = useState('');
  const [youtubeChannel, setYoutubeChannel] = useState('');
  const [youtubeSrcDir, setYoutubeSrcDir] = useState('');
  const [tiktokProfile, setTiktokProfile] = useState('');
  const [tiktokSrcDir, setTiktokSrcDir] = useState('');
  const [watermarkSource, setWatermarkSource] = useState('downloads');
  const [watermarkOutput, setWatermarkOutput] = useState('restored');
  const [watermarkTemplate, setWatermarkTemplate] = useState('watermark.png');
  const [extraEnvText, setExtraEnvText] = useState('');
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [lastExitCode, setLastExitCode] = useState<number | null>(null);
  const [activeTask, setActiveTask] = useState<
    'idle' | 'autogen' | 'downloader' | 'pipeline' | 'watermark' | 'youtube' | 'tiktok'
  >('idle');
  const [bootProgress, setBootProgress] = useState(0);
  const [isBooting, setIsBooting] = useState(true);

  const countdown = useCountdown(isRunning && activeTask === 'pipeline');

  useEffect(() => {
    const dispose = window.electronAPI?.onLog((entry) => {
      setLogs((prev) => [
        ...prev.slice(-400),
        {
          id: `${Date.now()}-${prev.length}`,
          message: entry.message,
          source: entry.source,
          timestamp: entry.timestamp,
          pid: entry.pid,
        },
      ]);
      setBootProgress(100);
      setTimeout(() => setIsBooting(false), 300);
    });

    return () => {
      dispose?.();
    };
  }, []);

  useEffect(() => {
    let current = 0;
    const timer = setInterval(() => {
      current = Math.min(100, current + Math.random() * 18 + 5);
      setBootProgress(Math.round(current));
      if (current >= 100) {
        clearInterval(timer);
        setTimeout(() => setIsBooting(false), 300);
      }
    }, 180);

    return () => clearInterval(timer);
  }, []);

  const sendCommand = async (command: RunPythonCommand) => {
    setIsRunning(true);
    setActiveTask(command.task);
    setLastExitCode(null);
    try {
      const result = await window.electronAPI?.runPython(command);
      if (result && typeof result.code === 'number') {
        setLastExitCode(result.code);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setLogs((prev) => [
        ...prev,
        {
          id: `${Date.now()}-err`,
          message: `[renderer] ${message}`,
          source: 'stderr',
          timestamp: Date.now(),
        },
      ]);
      setLastExitCode(-1);
    } finally {
      setIsRunning(false);
      setActiveTask('idle');
    }
  };

  const parsedExtraEnv = useMemo(() => {
    const env: Record<string, string> = {};
    extraEnvText
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .forEach((line) => {
        const [key, ...rest] = line.split('=');
        if (key && rest.length > 0) {
          env[key.trim()] = rest.join('=').trim();
        }
      });
    return env;
  }, [extraEnvText]);

  const buildPayload = (overrides?: Record<string, unknown>) => ({
    prompt_text: promptText,
    prompts_file: promptsFile || undefined,
    submitted_log: submittedLog || undefined,
    failed_log: failedLog || undefined,
    image_prompts_file: imagePromptsFile || undefined,
    cdp_endpoint: cdpEndpoint,
    downloads_dir: downloadsDir,
    titles_file: titlesFile || undefined,
    titles_cursor_file: titlesCursorFile || undefined,
    max_videos: maxVideos,
    open_drafts_first: openDraftsFirst,
    run_downloader: runDownloader,
    images_only: imagesOnly,
    attach_to_sora: attachToSora,
    env: parsedExtraEnv,
    ...overrides,
  });

  const handleStartPipeline = async () => {
    await sendCommand({ task: 'pipeline', payload: buildPayload() });
  };

  const handleAutogenOnly = async () => {
    await sendCommand({ task: 'autogen', payload: buildPayload() });
  };

  const handleDownloaderOnly = async () => {
    await sendCommand({
      task: 'downloader',
      payload: buildPayload({ run_downloader: true }),
    });
  };

  const handleWatermark = async () => {
    const env = {
      ...parsedExtraEnv,
      WMR_SOURCE_DIR: watermarkSource,
      WMR_OUTPUT_DIR: watermarkOutput,
      WMR_TEMPLATE: watermarkTemplate,
    };
    await sendCommand({ task: 'watermark', payload: buildPayload({ env }) });
  };

  const handleYoutube = async () => {
    const env = {
      ...parsedExtraEnv,
      APP_CONFIG_PATH: appConfigPath || undefined,
      YOUTUBE_CHANNEL_NAME: youtubeChannel,
      YOUTUBE_SRC_DIR: youtubeSrcDir,
    };
    await sendCommand({ task: 'youtube', payload: buildPayload({ env }) });
  };

  const handleTiktok = async () => {
    const env = {
      ...parsedExtraEnv,
      APP_CONFIG_PATH: appConfigPath || undefined,
      TIKTOK_PROFILE_NAME: tiktokProfile,
      TIKTOK_SRC_DIR: tiktokSrcDir,
    };
    await sendCommand({ task: 'tiktok', payload: buildPayload({ env }) });
  };

  const statusBadge = useMemo(() => {
    if (isRunning) return { label: 'Running', color: 'bg-indigo-500/20 text-indigo-200 border-indigo-500/40' };
    if (lastExitCode === 0) return { label: 'OK', color: 'bg-emerald-500/20 text-emerald-200 border-emerald-500/40' };
    if (lastExitCode !== null) return { label: 'Error', color: 'bg-red-500/20 text-red-200 border-red-500/40' };
    return { label: 'Idle', color: 'bg-slate-700/40 text-slate-200 border-slate-700/80' };
  }, [isRunning, lastExitCode]);

  const metrics = [
    { label: 'Pipeline', value: isRunning ? 'Active' : 'Standby', icon: Cpu, accent: 'text-indigo-300', border: 'border-indigo-500/30' },
    { label: 'Autogen', value: imagesOnly ? 'Images only' : 'Full', icon: Zap, accent: 'text-blue-300', border: 'border-blue-500/30' },
    { label: 'Downloader', value: runDownloader ? 'Enabled' : 'Skipped', icon: Download, accent: 'text-emerald-300', border: 'border-emerald-500/30' },
  ];

  const infoCards = [
    {
      title: 'Python workers stay untouched',
      body: 'Electron only orchestrates: your autogen and downloader scripts remain the execution engine. Config is passed as ENV.',
      icon: Settings,
    },
    {
      title: 'Streaming logs',
      body: 'stdout and stderr from Python stream live into the log console with precise timestamps.',
      icon: Terminal,
    },
    {
      title: 'Cyberpunk shell',
      body: 'Frameless window, drag handle on the top bar, glassmorphism cards, and neon accents.',
      icon: Activity,
    },
  ];

  return (
    <div className="relative min-h-screen bg-gradient-to-br from-zinc-950 via-zinc-900 to-black text-slate-100">
      {isBooting && (
        <div className="pointer-events-auto absolute inset-0 z-50 flex flex-col items-center justify-center bg-gradient-to-br from-black via-zinc-950 to-black/90">
          <div className="mb-4 h-16 w-16 animate-pulse rounded-full border border-indigo-500/40 bg-indigo-500/10 shadow-[0_0_40px_rgba(59,130,246,0.45)]" />
          <p className="text-sm font-semibold text-indigo-100">Booting control lab…</p>
          <div className="mt-3 h-2 w-72 overflow-hidden rounded-full border border-white/10 bg-white/5">
            <div
              className="h-full rounded-full bg-gradient-to-r from-indigo-500 via-blue-400 to-emerald-400 shadow-[0_0_20px_rgba(59,130,246,0.6)] transition-all duration-200"
              style={{ width: `${Math.min(100, bootProgress)}%` }}
            />
          </div>
          <p className="mt-2 text-xs text-slate-300">{bootProgress}%</p>
        </div>
      )}
      <div className="grid min-h-screen grid-cols-[240px_1fr] overflow-hidden">
        <aside className="relative hidden bg-gradient-to-b from-black/80 via-zinc-950/90 to-black/70 backdrop-blur-xl md:block">
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_20%_20%,rgba(59,130,246,0.08),transparent_35%),radial-gradient(circle_at_80%_10%,rgba(16,185,129,0.06),transparent_30%)]" />
          <div className="relative flex h-full flex-col border-r border-white/5 px-5 py-6">
            <div className="mb-8 flex items-center gap-3 text-lg font-semibold text-white">
              <div className="h-3 w-3 rounded-full bg-indigo-400 shadow-glow" />
              Sora Control Lab
            </div>
            <nav className="space-y-2 text-sm text-slate-300">
              {[
                { label: 'Dashboard', icon: Cpu },
                { label: 'Pipeline', icon: Play },
                { label: 'Logs', icon: Terminal },
              ].map((item) => (
                <div
                  key={item.label}
                  className="flex items-center gap-2 rounded-lg border border-white/5 bg-white/5 px-3 py-2 text-slate-200 shadow-sm shadow-black/30"
                >
                  <item.icon className="h-4 w-4 text-indigo-300" />
                  <span>{item.label}</span>
                </div>
              ))}
            </nav>
            <div className="mt-auto space-y-3 rounded-xl border border-indigo-500/30 bg-indigo-500/5 p-4">
              <p className="text-xs uppercase tracking-[0.2em] text-indigo-200">Status</p>
              <p className="text-sm text-slate-200">Electron shell ready</p>
              <div className={`w-fit rounded-full border px-3 py-1 text-xs ${statusBadge.color}`}>{statusBadge.label}</div>
            </div>
          </div>
        </aside>

        <main className="relative flex flex-col overflow-hidden">
          <div className="pointer-events-none sticky top-0 z-20 border-b border-white/5 bg-black/60 px-6 py-4 backdrop-blur-md" style={{ WebkitAppRegion: 'drag' }}>
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex items-center gap-2 text-sm text-slate-300">
                <div className="h-2 w-2 animate-pulse rounded-full bg-indigo-400" />
                Cyberpunk Control Surface
              </div>
              <div className="pointer-events-auto ml-auto flex items-center gap-2" style={{ WebkitAppRegion: 'no-drag' }}>
                <div className={`rounded-full border px-3 py-1 text-xs ${statusBadge.color}`}>{statusBadge.label}</div>
                {lastExitCode !== null && !isRunning && (
                  <div className={`rounded-full border px-3 py-1 text-xs ${lastExitCode === 0 ? 'border-emerald-500/50 text-emerald-200' : 'border-red-500/50 text-red-200'}`}>
                    Exit {lastExitCode}
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className="relative grid flex-1 grid-cols-1 gap-6 overflow-y-auto px-4 py-6 md:grid-cols-[1.2fr_1fr] lg:px-8">
            <section className="space-y-6">
              <div className="rounded-2xl border border-white/5 bg-white/5 p-6 shadow-xl shadow-black/30 backdrop-blur-xl">
                <div className="mb-6 flex items-start justify-between gap-4">
                  <div>
                    <p className="text-xs uppercase tracking-[0.2em] text-slate-400">Sora Automation</p>
                    <h1 className="text-2xl font-semibold text-white">Pipeline Commander</h1>
                    <p className="mt-1 text-sm text-slate-400">
                      Launch the Python autogen workflow and optionally chain the downloader in a single click. ENV is passed directly to the workers.
                    </p>
                  </div>
                  <div className="hidden gap-2 md:flex">
                    <div className={`rounded-full border px-3 py-1 text-xs ${statusBadge.color}`}>{statusBadge.label}</div>
                    {countdown > 0 && isRunning && (
                      <div className="rounded-full border border-white/10 px-3 py-1 text-xs text-slate-200">{countdown}s</div>
                    )}
                  </div>
                </div>

                <div className="grid gap-4 md:grid-cols-2">
                  <label className="group relative flex flex-col gap-2 rounded-xl border border-white/10 bg-black/40 p-4 shadow-sm shadow-black/40 transition hover:border-indigo-500/50">
                    <div className="flex items-center gap-2 text-sm font-semibold text-slate-100">
                      <Zap className="h-4 w-4 text-indigo-300" /> Prompt (inline)
                    </div>
                    <textarea
                      className="min-h-[120px] rounded-lg border border-white/5 bg-white/5 px-3 py-2 text-sm text-slate-100 outline-none focus:border-indigo-500/60 focus:ring-2 focus:ring-indigo-500/30"
                      value={promptText}
                      onChange={(e) => setPromptText(e.target.value)}
                      placeholder="Paste a prompt to feed into the autogen worker"
                    />
                    <p className="text-xs text-slate-400">Stored as a temp prompts file and passed via SORA_PROMPTS_FILE.</p>
                  </label>

                  <div className="space-y-3 rounded-xl border border-white/10 bg-black/40 p-4 shadow-sm shadow-black/40">
                    <div className="flex items-center gap-2 text-sm font-semibold text-slate-100">
                      <Settings className="h-4 w-4 text-indigo-300" /> Worker Environment
                    </div>
                    <label className="space-y-1 text-sm text-slate-300">
                      <span className="text-xs uppercase tracking-[0.2em] text-slate-400">CDP Endpoint</span>
                      <input
                        className="w-full rounded-lg border border-white/5 bg-white/5 px-3 py-2 text-sm text-slate-100 outline-none focus:border-indigo-500/60 focus:ring-2 focus:ring-indigo-500/30"
                        value={cdpEndpoint}
                        onChange={(e) => setCdpEndpoint(e.target.value)}
                        placeholder="http://localhost:9222"
                      />
                    </label>
                    <div className="grid grid-cols-2 gap-3">
                      <label className="space-y-1 text-sm text-slate-300">
                        <span className="text-xs uppercase tracking-[0.2em] text-slate-400">Downloads dir</span>
                        <input
                          className="w-full rounded-lg border border-white/5 bg-white/5 px-3 py-2 text-sm text-slate-100 outline-none focus:border-indigo-500/60 focus:ring-2 focus:ring-indigo-500/30"
                          value={downloadsDir}
                          onChange={(e) => setDownloadsDir(e.target.value)}
                          placeholder="downloads"
                        />
                      </label>
                      <label className="space-y-1 text-sm text-slate-300">
                        <span className="text-xs uppercase tracking-[0.2em] text-slate-400">Max videos</span>
                        <input
                          type="number"
                          min={0}
                          className="w-full rounded-lg border border-white/5 bg-white/5 px-3 py-2 text-sm text-slate-100 outline-none focus:border-indigo-500/60 focus:ring-2 focus:ring-indigo-500/30"
                          value={maxVideos}
                          onChange={(e) => setMaxVideos(Number(e.target.value))}
                        />
                      </label>
                    </div>
                    <div className="flex flex-col gap-2 text-sm text-slate-200">
                      <label className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          checked={imagesOnly}
                          onChange={(e) => setImagesOnly(e.target.checked)}
                          className="h-4 w-4 rounded border border-white/30 bg-white/10 text-indigo-500 accent-indigo-500"
                        />
                        <span>Generate images only (skip Sora submission)</span>
                      </label>
                      <label className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          checked={attachToSora}
                          onChange={(e) => setAttachToSora(e.target.checked)}
                          className="h-4 w-4 rounded border border-white/30 bg-white/10 text-indigo-500 accent-indigo-500"
                        />
                        <span>Attach GenAI output to Sora queue</span>
                      </label>
                      <label className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          checked={runDownloader}
                          onChange={(e) => setRunDownloader(e.target.checked)}
                          className="h-4 w-4 rounded border border-white/30 bg-white/10 text-indigo-500 accent-indigo-500"
                        />
                        <span>Run downloader after autogen</span>
                      </label>
                      <label className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          checked={openDraftsFirst}
                          onChange={(e) => setOpenDraftsFirst(e.target.checked)}
                          className="h-4 w-4 rounded border border-white/30 bg-white/10 text-indigo-500 accent-indigo-500"
                        />
                        <span>Open drafts page first (existing Chrome session)</span>
                      </label>
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2">
                      <label className="space-y-1 text-sm text-slate-300">
                        <span className="text-xs uppercase tracking-[0.2em] text-slate-400">Prompts file</span>
                        <input
                          className="w-full rounded-lg border border-white/5 bg-white/5 px-3 py-2 text-sm text-slate-100 outline-none focus:border-indigo-500/60 focus:ring-2 focus:ring-indigo-500/30"
                          value={promptsFile}
                          onChange={(e) => setPromptsFile(e.target.value)}
                          placeholder="path/to/prompts.txt (optional)"
                        />
                      </label>
                      <label className="space-y-1 text-sm text-slate-300">
                        <span className="text-xs uppercase tracking-[0.2em] text-slate-400">Image prompts</span>
                        <input
                          className="w-full rounded-lg border border-white/5 bg-white/5 px-3 py-2 text-sm text-slate-100 outline-none focus:border-indigo-500/60 focus:ring-2 focus:ring-indigo-500/30"
                          value={imagePromptsFile}
                          onChange={(e) => setImagePromptsFile(e.target.value)}
                          placeholder="path/to/image_prompts.txt"
                        />
                      </label>
                      <label className="space-y-1 text-sm text-slate-300">
                        <span className="text-xs uppercase tracking-[0.2em] text-slate-400">Submitted log</span>
                        <input
                          className="w-full rounded-lg border border-white/5 bg-white/5 px-3 py-2 text-sm text-slate-100 outline-none focus:border-indigo-500/60 focus:ring-2 focus:ring-indigo-500/30"
                          value={submittedLog}
                          onChange={(e) => setSubmittedLog(e.target.value)}
                          placeholder="autogen/submitted.log"
                        />
                      </label>
                      <label className="space-y-1 text-sm text-slate-300">
                        <span className="text-xs uppercase tracking-[0.2em] text-slate-400">Failed log</span>
                        <input
                          className="w-full rounded-lg border border-white/5 bg-white/5 px-3 py-2 text-sm text-slate-100 outline-none focus:border-indigo-500/60 focus:ring-2 focus:ring-indigo-500/30"
                          value={failedLog}
                          onChange={(e) => setFailedLog(e.target.value)}
                          placeholder="autogen/failed.log"
                        />
                      </label>
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2">
                      <label className="space-y-1 text-sm text-slate-300">
                        <span className="text-xs uppercase tracking-[0.2em] text-slate-400">Titles list</span>
                        <input
                          className="w-full rounded-lg border border-white/5 bg-white/5 px-3 py-2 text-sm text-slate-100 outline-none focus:border-indigo-500/60 focus:ring-2 focus:ring-indigo-500/30"
                          value={titlesFile}
                          onChange={(e) => setTitlesFile(e.target.value)}
                          placeholder="titles.txt for downloader"
                        />
                      </label>
                      <label className="space-y-1 text-sm text-slate-300">
                        <span className="text-xs uppercase tracking-[0.2em] text-slate-400">Titles cursor</span>
                        <input
                          className="w-full rounded-lg border border-white/5 bg-white/5 px-3 py-2 text-sm text-slate-100 outline-none focus:border-indigo-500/60 focus:ring-2 focus:ring-indigo-500/30"
                          value={titlesCursorFile}
                          onChange={(e) => setTitlesCursorFile(e.target.value)}
                          placeholder="titles.cursor (optional)"
                        />
                      </label>
                    </div>
                  </div>
                </div>

                <div className="mt-6 flex flex-wrap items-center gap-4">
                  <div className="flex flex-wrap gap-3">
                    <button
                      type="button"
                      onClick={handleStartPipeline}
                      disabled={isRunning}
                      className={`flex items-center gap-2 rounded-xl px-4 py-3 text-sm font-semibold shadow-lg shadow-indigo-900 transition focus:outline-none focus:ring-2 focus:ring-indigo-500/60 ${
                        isRunning
                          ? 'cursor-not-allowed border border-white/10 bg-white/10 text-slate-300'
                          : 'border border-indigo-500/50 bg-indigo-500/20 text-indigo-100 hover:-translate-y-0.5 hover:border-indigo-400 hover:bg-indigo-500/25'
                      }`}
                    >
                      <Play className="h-4 w-4" />
                      {isRunning && activeTask === 'pipeline' ? 'Running…' : 'Start Pipeline'}
                    </button>
                    <button
                      type="button"
                      onClick={handleAutogenOnly}
                      disabled={isRunning}
                      className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/10 px-4 py-3 text-sm font-semibold text-slate-100 shadow-lg shadow-black/40 transition hover:border-blue-400/60 hover:bg-blue-500/10 focus:outline-none focus:ring-2 focus:ring-blue-500/40 disabled:cursor-not-allowed disabled:opacity-70"
                    >
                      <Zap className="h-4 w-4" />
                      Autogen only
                    </button>
                    <button
                      type="button"
                      onClick={handleDownloaderOnly}
                      disabled={isRunning}
                      className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/10 px-4 py-3 text-sm font-semibold text-slate-100 shadow-lg shadow-black/40 transition hover:border-emerald-400/60 hover:bg-emerald-500/10 focus:outline-none focus:ring-2 focus:ring-emerald-500/40 disabled:cursor-not-allowed disabled:opacity-70"
                    >
                      <Download className="h-4 w-4" />
                      Downloader only
                    </button>
                  </div>
                  <div className="flex flex-wrap gap-3 text-xs text-slate-400">
                    <span className="rounded-full border border-white/10 px-3 py-1">cdp: {cdpEndpoint}</span>
                    <span className="rounded-full border border-white/10 px-3 py-1">downloads: {downloadsDir}</span>
                    {maxVideos > 0 && <span className="rounded-full border border-white/10 px-3 py-1">max {maxVideos} videos</span>}
                  </div>
                </div>
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                {metrics.map((metric) => (
                  <div
                    key={metric.label}
                    className={`rounded-2xl border ${metric.border} bg-white/5 p-4 shadow-lg shadow-black/30 backdrop-blur`}
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2 text-sm font-semibold text-slate-100">
                        <metric.icon className={`h-4 w-4 ${metric.accent}`} />
                        {metric.label}
                      </div>
                      <div className="text-xs uppercase tracking-[0.2em] text-slate-400">Live</div>
                    </div>
                    <p className="mt-2 text-lg text-white">{metric.value}</p>
                  </div>
                ))}
              </div>

              <div className="rounded-2xl border border-white/5 bg-white/5 p-6 shadow-xl shadow-black/30">
                <div className="flex items-center gap-2 text-sm font-semibold text-slate-100">
                  <Terminal className="h-4 w-4 text-indigo-300" /> Log stream
                </div>
                <div className="mt-3 h-72 overflow-y-auto rounded-xl border border-white/5 bg-black/60 p-3 font-mono text-xs text-slate-200">
                  {logs.length === 0 && <p className="text-slate-500">Waiting for output…</p>}
                  {logs.map((log) => (
                    <div key={log.id} className="flex gap-2">
                      <span className="text-slate-500">[{new Date(log.timestamp).toLocaleTimeString()}]</span>
                      <span className={log.source === 'stderr' ? 'text-red-300' : 'text-slate-200'}>{log.message}</span>
                    </div>
                  ))}
                </div>
              </div>
            </section>

            <section className="space-y-4">
              <div className="rounded-2xl border border-indigo-500/30 bg-indigo-500/5 p-6 shadow-xl shadow-black/30">
                <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-indigo-100">
                  <Cpu className="h-4 w-4" /> Worker triggers
                </div>
                <div className="space-y-4">
                  <label className="space-y-1 text-sm text-slate-200">
                    <span className="text-xs uppercase tracking-[0.2em] text-slate-400">app_config.yaml (optional)</span>
                    <input
                      className="w-full rounded-lg border border-white/10 bg-black/40 px-3 py-2 text-sm text-slate-100 outline-none focus:border-indigo-400/60 focus:ring-2 focus:ring-indigo-500/30"
                      value={appConfigPath}
                      onChange={(e) => setAppConfigPath(e.target.value)}
                      placeholder="sora_suite/app_config.yaml"
                    />
                  </label>

                  <div className="grid gap-3 md:grid-cols-2">
                    <div className="rounded-xl border border-white/10 bg-black/40 p-4">
                      <p className="text-sm font-semibold text-white">Watermark cleaner</p>
                      <div className="mt-3 space-y-2 text-sm text-slate-300">
                        <input
                          className="w-full rounded-lg border border-white/10 bg-black/60 px-3 py-2 text-sm text-slate-100 outline-none focus:border-emerald-400/60 focus:ring-2 focus:ring-emerald-500/30"
                          value={watermarkSource}
                          onChange={(e) => setWatermarkSource(e.target.value)}
                          placeholder="WMR_SOURCE_DIR (input folder)"
                        />
                        <input
                          className="w-full rounded-lg border border-white/10 bg-black/60 px-3 py-2 text-sm text-slate-100 outline-none focus:border-emerald-400/60 focus:ring-2 focus:ring-emerald-500/30"
                          value={watermarkOutput}
                          onChange={(e) => setWatermarkOutput(e.target.value)}
                          placeholder="WMR_OUTPUT_DIR (output folder)"
                        />
                        <input
                          className="w-full rounded-lg border border-white/10 bg-black/60 px-3 py-2 text-sm text-slate-100 outline-none focus:border-emerald-400/60 focus:ring-2 focus:ring-emerald-500/30"
                          value={watermarkTemplate}
                          onChange={(e) => setWatermarkTemplate(e.target.value)}
                          placeholder="WMR_TEMPLATE (watermark.png)"
                        />
                        <button
                          type="button"
                          onClick={handleWatermark}
                          disabled={isRunning}
                          className="w-full rounded-lg border border-emerald-500/50 bg-emerald-500/20 px-3 py-2 text-sm font-semibold text-emerald-50 shadow-md shadow-emerald-900 transition hover:-translate-y-0.5 hover:border-emerald-400 hover:bg-emerald-500/25 focus:outline-none focus:ring-2 focus:ring-emerald-500/40 disabled:cursor-not-allowed disabled:opacity-70"
                        >
                          Run watermark cleaner
                        </button>
                      </div>
                    </div>

                    <div className="rounded-xl border border-white/10 bg-black/40 p-4 space-y-3">
                      <p className="text-sm font-semibold text-white">Upload queues</p>
                      <div className="space-y-2 text-sm text-slate-300">
                        <input
                          className="w-full rounded-lg border border-white/10 bg-black/60 px-3 py-2 text-sm text-slate-100 outline-none focus:border-blue-400/60 focus:ring-2 focus:ring-blue-500/30"
                          value={youtubeChannel}
                          onChange={(e) => setYoutubeChannel(e.target.value)}
                          placeholder="YouTube channel name"
                        />
                        <input
                          className="w-full rounded-lg border border-white/10 bg-black/60 px-3 py-2 text-sm text-slate-100 outline-none focus:border-blue-400/60 focus:ring-2 focus:ring-blue-500/30"
                          value={youtubeSrcDir}
                          onChange={(e) => setYoutubeSrcDir(e.target.value)}
                          placeholder="YouTube source dir"
                        />
                        <button
                          type="button"
                          onClick={handleYoutube}
                          disabled={isRunning}
                          className="w-full rounded-lg border border-blue-500/50 bg-blue-500/20 px-3 py-2 text-sm font-semibold text-blue-50 shadow-md shadow-blue-900 transition hover:-translate-y-0.5 hover:border-blue-400 hover:bg-blue-500/25 focus:outline-none focus:ring-2 focus:ring-blue-500/40 disabled:cursor-not-allowed disabled:opacity-70"
                        >
                          Run YouTube queue
                        </button>
                      </div>
                      <div className="space-y-2 text-sm text-slate-300">
                        <input
                          className="w-full rounded-lg border border-white/10 bg-black/60 px-3 py-2 text-sm text-slate-100 outline-none focus:border-pink-400/60 focus:ring-2 focus:ring-pink-500/30"
                          value={tiktokProfile}
                          onChange={(e) => setTiktokProfile(e.target.value)}
                          placeholder="TikTok profile name"
                        />
                        <input
                          className="w-full rounded-lg border border-white/10 bg-black/60 px-3 py-2 text-sm text-slate-100 outline-none focus:border-pink-400/60 focus:ring-2 focus:ring-pink-500/30"
                          value={tiktokSrcDir}
                          onChange={(e) => setTiktokSrcDir(e.target.value)}
                          placeholder="TikTok source dir"
                        />
                        <button
                          type="button"
                          onClick={handleTiktok}
                          disabled={isRunning}
                          className="w-full rounded-lg border border-pink-500/50 bg-pink-500/20 px-3 py-2 text-sm font-semibold text-pink-50 shadow-md shadow-pink-900 transition hover:-translate-y-0.5 hover:border-pink-400 hover:bg-pink-500/25 focus:outline-none focus:ring-2 focus:ring-pink-500/40 disabled:cursor-not-allowed disabled:opacity-70"
                        >
                          Run TikTok queue
                        </button>
                      </div>
                    </div>
                  </div>

                  <div className="rounded-xl border border-white/10 bg-black/40 p-4">
                    <p className="text-sm font-semibold text-white">Extra env overrides</p>
                    <p className="text-xs text-slate-400">One KEY=VALUE per line. These merge into every task run.</p>
                    <textarea
                      className="mt-2 h-28 w-full rounded-lg border border-white/10 bg-black/60 px-3 py-2 font-mono text-xs text-slate-100 outline-none focus:border-indigo-400/60 focus:ring-2 focus:ring-indigo-500/30"
                      value={extraEnvText}
                      onChange={(e) => setExtraEnvText(e.target.value)}
                      placeholder="GENAI_API_KEY=...\nTELEGRAM_BOT_TOKEN=..."
                    />
                  </div>
                </div>
              </div>

              <div className="rounded-2xl border border-white/5 bg-white/5 p-6 shadow-xl shadow-black/30">
                <div className="flex items-center gap-2 text-sm font-semibold text-slate-100">
                  <Cpu className="h-4 w-4 text-indigo-300" /> System status
                </div>
                <div className="mt-4 grid gap-3">
                  {infoCards.map((card) => (
                    <div
                      key={card.title}
                      className="flex items-start gap-3 rounded-xl border border-white/10 bg-black/30 p-3 shadow-inner shadow-black/40"
                    >
                      <card.icon className="h-4 w-4 text-indigo-300" />
                      <div>
                        <p className="text-sm font-semibold text-white">{card.title}</p>
                        <p className="text-sm text-slate-400">{card.body}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="rounded-2xl border border-emerald-500/30 bg-emerald-500/10 p-6 shadow-xl shadow-emerald-950/50">
                <div className="flex items-center gap-2 text-sm font-semibold text-emerald-100">
                  <CheckCircle2 className="h-4 w-4" /> Safety hints
                </div>
                <ul className="mt-3 space-y-2 text-sm text-emerald-50/90">
                  <li>Keep Chrome running with remote debugging enabled before starting the downloader.</li>
                  <li>Prompts are written to a temporary file and cleared on next run; provide a real file path if you need history.</li>
                  <li>Use the Python virtualenv recommended in the README to avoid missing Playwright or FFmpeg bindings.</li>
                </ul>
              </div>

              <div className="rounded-2xl border border-red-500/30 bg-red-500/10 p-6 shadow-xl shadow-red-950/50">
                <div className="flex items-center gap-2 text-sm font-semibold text-red-100">
                  <AlertTriangle className="h-4 w-4" /> Troubleshooting
                </div>
                <ul className="mt-3 space-y-2 text-sm text-red-50/90">
                  <li>If you see playwright timeouts, verify CDP_ENDPOINT and the Sora session are alive.</li>
                  <li>GENAI_* variables are passed through as-is; set them in your shell before launching Electron.</li>
                  <li>Logs in this panel include stderr; share them when reporting errors.</li>
                </ul>
              </div>
            </section>
          </div>
        </main>
      </div>
    </div>
  );
};

export default App;
