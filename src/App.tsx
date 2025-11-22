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
  const [maxVideos, setMaxVideos] = useState(3);
  const [runDownloader, setRunDownloader] = useState(true);
  const [openDraftsFirst, setOpenDraftsFirst] = useState(true);
  const [imagesOnly, setImagesOnly] = useState(false);
  const [attachToSora, setAttachToSora] = useState(true);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [lastExitCode, setLastExitCode] = useState<number | null>(null);
  const [activeTask, setActiveTask] = useState<'idle' | 'autogen' | 'downloader' | 'pipeline'>('idle');

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
    });

    return () => {
      dispose?.();
    };
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

  const handleStartPipeline = async () => {
    await sendCommand({
      task: 'pipeline',
      payload: {
        prompt_text: promptText,
        cdp_endpoint: cdpEndpoint,
        downloads_dir: downloadsDir,
        max_videos: maxVideos,
        open_drafts_first: openDraftsFirst,
        run_downloader: runDownloader,
        images_only: imagesOnly,
        attach_to_sora: attachToSora,
      },
    });
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
    <div className="min-h-screen bg-gradient-to-br from-zinc-950 via-zinc-900 to-black text-slate-100">
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
                  </div>
                </div>

                <div className="mt-6 flex flex-wrap items-center gap-4">
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
                    {isRunning ? 'Running…' : 'Start Pipeline'}
                  </button>
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
