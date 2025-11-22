import { app, BrowserWindow, ipcMain } from 'electron';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import readline from 'node:readline';
import yaml from 'yaml';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const suiteRoot = path.resolve(__dirname, '..');
const appConfigPath = path.join(suiteRoot, 'app_config.yaml');

const deepMerge = (target, source) => {
  if (Array.isArray(source)) return source;
  if (source && typeof source === 'object') {
    const out = { ...(target || {}) };
    Object.entries(source).forEach(([key, value]) => {
      out[key] = deepMerge(out[key], value);
    });
    return out;
  }
  return source;
};

const readConfig = async () => {
  try {
    const raw = await fs.promises.readFile(appConfigPath, 'utf-8');
    return yaml.parse(raw) || {};
  } catch (error) {
    return {};
  }
};

const writeConfig = async (config) => {
  const serialized = yaml.stringify(config || {});
  await fs.promises.writeFile(appConfigPath, serialized, 'utf-8');
};

const taskCommands = {
  autogen: ['-m', 'workers.autogen.main'],
  downloader: [path.join(suiteRoot, 'workers', 'downloader', 'download_all.py')],
  watermarkCheck: [path.join(suiteRoot, 'watermark_detector.py')],
  watermarkClean: [path.join(suiteRoot, 'workers', 'watermark_cleaner', 'restore.py')],
  uploader: [path.join(suiteRoot, 'workers', 'uploader', 'upload_queue.py')],
  tiktok: [path.join(suiteRoot, 'workers', 'tiktok', 'upload_queue.py')],
};

const runningTasks = new Map();

const getStartUrl = () => {
  const devUrl = process.env.ELECTRON_START_URL;
  if (devUrl && devUrl.startsWith('http')) {
    return { devUrl };
  }
  const distDir = process.env.ELECTRON_DIST_DIR || path.join(__dirname, 'dist');
  return { distDir, indexPath: path.join(distDir, 'index.html') };
};

const createWindow = () => {
  const { devUrl, indexPath } = getStartUrl();
  const win = new BrowserWindow({
    width: 1400,
    height: 900,
    backgroundColor: '#0f172a',
    titleBarStyle: 'hiddenInset',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      preload: path.join(__dirname, 'electron-preload.js'),
    },
  });

  if (devUrl) {
    win.loadURL(devUrl);
    win.webContents.openDevTools({ mode: 'detach' });
    return;
  }

  if (!indexPath) {
    win.loadURL('data:text/plain,Не найден сборочный индекс. Запустите npm run build.');
    return;
  }

  win.loadFile(indexPath);
};

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.whenReady().then(() => {
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

const sendTaskEvent = (payload) => {
  BrowserWindow.getAllWindows().forEach((win) => {
    win.webContents.send('task:event', payload);
  });
};

ipcMain.handle('config:load', async () => readConfig());

ipcMain.handle('config:update', async (_event, patch) => {
  const current = await readConfig();
  const next = deepMerge(current, patch || {});
  await writeConfig(next);
  return next;
});

ipcMain.handle('task:start', async (_event, payload) => {
  const { task, args = [], env = {} } = payload || {};
  const cmd = taskCommands[task];
  if (!cmd) {
    throw new Error(`Unknown task: ${task}`);
  }

  const pythonBin = process.env.SORA_PYTHON || process.env.PYTHON || 'python3';
  const child = spawn(pythonBin, [...cmd, ...args], {
    cwd: suiteRoot,
    env: { ...process.env, ...env },
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  runningTasks.set(child.pid, child);
  sendTaskEvent({ kind: 'start', pid: child.pid, task });

  const pump = (stream, channel) => {
    const rl = readline.createInterface({ input: stream });
    rl.on('line', (line) => sendTaskEvent({ kind: 'log', pid: child.pid, task, channel, line }));
    stream.on('close', () => rl.close());
  };

  pump(child.stdout, 'stdout');
  pump(child.stderr, 'stderr');

  child.on('close', (code, signal) => {
    runningTasks.delete(child.pid);
    sendTaskEvent({ kind: 'exit', pid: child.pid, task, code, signal });
  });

  return { pid: child.pid };
});

ipcMain.handle('task:stop', async (_event, pid) => {
  const child = runningTasks.get(pid);
  if (!child) return false;
  child.kill();
  runningTasks.delete(pid);
  return true;
});

ipcMain.handle('history:tail', async (_event, limit = 50) => {
  const historyPath = path.join(suiteRoot, 'history.jsonl');
  if (!fs.existsSync(historyPath)) return [];
  const lines = (await fs.promises.readFile(historyPath, 'utf-8')).split(/\r?\n/).filter(Boolean);
  const parsed = lines
    .slice(-1 * limit)
    .map((line, idx) => {
      try {
        return JSON.parse(line);
      } catch (error) {
        return {
          id: `invalid-${idx}`,
          timestamp: new Date().toISOString(),
          actor: 'history',
          action: 'Parse error',
          status: 'failed',
          details: String(error),
        };
      }
    })
    .reverse();
  return parsed;
});

ipcMain.handle('content:load', async () => {
  const cfgRaw = await fs.promises.readFile(appConfigPath, 'utf-8');
  const cfg = yaml.parse(cfgRaw) || {};
  const autogenDir = path.resolve(suiteRoot, cfg?.autogen?.workdir || 'workers/autogen');
  const promptsPath = path.resolve(suiteRoot, cfg?.autogen?.prompts_file || path.join(autogenDir, 'prompts.txt'));
  const imagePromptsPath = path.resolve(
    suiteRoot,
    cfg?.autogen?.image_prompts_file || path.join(autogenDir, 'image_prompts.txt'),
  );
  const titlesPath = path.resolve(suiteRoot, cfg?.titles_file || path.join(suiteRoot, 'titles.txt'));

  const readSafe = async (p) => {
    try {
      return await fs.promises.readFile(p, 'utf-8');
    } catch (error) {
      return '';
    }
  };

  return {
    prompts: await readSafe(promptsPath),
    imagePrompts: await readSafe(imagePromptsPath),
    titles: await readSafe(titlesPath),
    promptsPath,
    imagePromptsPath,
    titlesPath,
    config: cfg,
  };
});

ipcMain.handle('content:save', async (_event, payload) => {
  const { prompts, imagePrompts, titles, promptsPath, imagePromptsPath, titlesPath } = payload || {};
  const writes = [];
  if (promptsPath && typeof prompts === 'string') {
    writes.push(fs.promises.writeFile(promptsPath, prompts, 'utf-8'));
  }
  if (imagePromptsPath && typeof imagePrompts === 'string') {
    writes.push(fs.promises.writeFile(imagePromptsPath, imagePrompts, 'utf-8'));
  }
  if (titlesPath && typeof titles === 'string') {
    writes.push(fs.promises.writeFile(titlesPath, titles, 'utf-8'));
  }
  await Promise.all(writes);
  return true;
});
