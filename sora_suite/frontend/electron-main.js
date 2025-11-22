import { app, BrowserWindow, ipcMain } from 'electron';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import readline from 'node:readline';
import yaml from 'yaml';
import os from 'node:os';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const suiteRoot = path.resolve(__dirname, '..');
const appConfigPath = path.join(suiteRoot, 'app_config.yaml');
const shadowBase = path.join(os.homedir(), '.sora_suite', 'shadows');

const taskCommands = {
  autogen: ['-m', 'workers.autogen.main'],
  downloader: [path.join(suiteRoot, 'workers', 'downloader', 'download_all.py')],
  watermarkCheck: [path.join(suiteRoot, 'watermark_detector.py')],
  watermarkClean: [path.join(suiteRoot, 'workers', 'watermark_cleaner', 'restore.py')],
  uploader: [path.join(suiteRoot, 'workers', 'uploader', 'upload_queue.py')],
  tiktok: [path.join(suiteRoot, 'workers', 'tiktok', 'upload_queue.py')],
};

const runningTasks = new Map();
const runningChrome = new Map();

const resolvePath = (value = '') => {
  if (!value) return '';
  const envExpanded = value.replace(/%([^%]+)%/g, (_m, name) => process.env[name] || `/${name}`);
  const expanded = envExpanded.replace(/^~(?=$|\/)/, os.homedir());
  return path.isAbsolute(expanded) ? expanded : path.join(suiteRoot, expanded);
};

const readConfig = async () => {
  const raw = await fs.promises.readFile(appConfigPath, 'utf-8');
  return yaml.parse(raw) || {};
};

const getStartUrl = () => {
  const devUrl = process.env.ELECTRON_START_URL;
  if (devUrl && devUrl.startsWith('http')) {
    return { devUrl };
  }
  const distDir = process.env.ELECTRON_DIST_DIR || path.join(__dirname, 'dist');
  return { distDir, indexPath: path.join(distDir, 'index.html') };
};

const shouldSkipPath = (candidate) => {
  const parts = candidate.split(path.sep);
  return parts.some((segment) =>
    ['Cache', 'Code Cache', 'GPUCache', 'Service Worker', 'BrowserMetrics'].includes(segment),
  );
};

const copyProfile = async (src, dest) => {
  await fs.promises.mkdir(dest, { recursive: true });
  await fs.promises.cp(src, dest, {
    recursive: true,
    filter: (source) => !shouldSkipPath(source),
  });
};

const launchChrome = async ({ profileName, overridePort }) => {
  const cfg = await readConfig();
  const chromeCfg = cfg.chrome || {};
  const profiles = chromeCfg.profiles || [];
  const port = overridePort || chromeCfg.cdp_port || 9222;
  const match = profiles.find((p) => p.name === profileName) || profiles.find((p) => p.profile_directory === profileName);
  const fallbackUserDir = chromeCfg.user_data_dir || '';
  const chromeBinary = chromeCfg.binary ||
    (process.platform === 'darwin'
      ? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
      : process.platform.startsWith('win')
        ? 'C:/Program Files/Google/Chrome/Application/chrome.exe'
        : 'google-chrome');

  const sourceRoot = match?.user_data_dir || fallbackUserDir;
  const profileDir = match?.profile_directory || 'Default';
  if (!sourceRoot) {
    throw new Error('chrome.user_data_dir не задан в app_config.yaml');
  }
  const srcPath = resolvePath(path.join(sourceRoot, profileDir));
  if (!fs.existsSync(srcPath)) {
    throw new Error(`Профиль Chrome не найден по пути ${srcPath}`);
  }
  const destRoot = path.join(shadowBase, match?.name || profileDir);
  const destProfile = path.join(destRoot, profileDir);
  await copyProfile(srcPath, destProfile);

  const child = spawn(chromeBinary, [
    `--remote-debugging-port=${port}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-popup-blocking',
    `--user-data-dir=${destRoot}`,
    `--profile-directory=${profileDir}`,
  ], {
    stdio: 'ignore',
  });

  runningChrome.set(child.pid, child);
  return { pid: child.pid, port, profile: profileName };
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

ipcMain.handle('config:load', async () => {
  return readConfig();
});

ipcMain.handle('chrome:list', async () => {
  const cfg = await readConfig();
  const chromeCfg = cfg.chrome || {};
  const profiles = chromeCfg.profiles || [];
  return profiles.map((p) => ({
    name: p.name,
    user_data_dir: resolvePath(p.user_data_dir || chromeCfg.user_data_dir || ''),
    profile_directory: p.profile_directory || 'Default',
    cdp_port: p.cdp_port || chromeCfg.cdp_port || 9222,
  }));
});

ipcMain.handle('chrome:launch', async (_event, payload) => {
  const { profileName, port } = payload || {};
  return launchChrome({ profileName, overridePort: port });
});

ipcMain.handle('chrome:stop', async (_event, pid) => {
  const proc = runningChrome.get(pid);
  if (!proc) return false;
  proc.kill();
  runningChrome.delete(pid);
  return true;
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
  const cfg = await readConfig();
  const autogenDir = path.resolve(suiteRoot, cfg?.autogen?.workdir || 'workers/autogen');
  const defaultPromptsPath = resolvePath(cfg?.autogen?.prompts_file || path.join(autogenDir, 'prompts.txt'));
  const defaultImagePromptsPath = resolvePath(
    cfg?.autogen?.image_prompts_file || path.join(autogenDir, 'image_prompts.txt'),
  );
  const defaultTitlesPath = resolvePath(cfg?.titles_file || path.join(suiteRoot, 'titles.txt'));
  const sessions = cfg?.autogen?.sessions || [];

  const readSafe = async (p) => {
    try {
      return await fs.promises.readFile(p, 'utf-8');
    } catch (error) {
      return '';
    }
  };

  const titlesByProfile = {};
  const promptsByProfile = {};
  const imagePromptsByProfile = {};
  const sessionPaths = {};

  for (const session of sessions) {
    const id = session.id || session.name || session.chrome_profile || session.prompt_profile || 'session';
    const promptsPath = resolvePath(session.prompts_file || defaultPromptsPath);
    const imagePath = resolvePath(session.image_prompts_file || defaultImagePromptsPath);
    const titlesPath = resolvePath(session.titles_file || defaultTitlesPath);
    sessionPaths[id] = { promptsPath, imagePromptsPath: imagePath, titlesPath };
    promptsByProfile[id] = await readSafe(promptsPath);
    imagePromptsByProfile[id] = await readSafe(imagePath);
    titlesByProfile[id] = await readSafe(titlesPath);
  }

  return {
    prompts: await readSafe(defaultPromptsPath),
    imagePrompts: await readSafe(defaultImagePromptsPath),
    titles: await readSafe(defaultTitlesPath),
    promptsPath: defaultPromptsPath,
    imagePromptsPath: defaultImagePromptsPath,
    titlesPath: defaultTitlesPath,
    config: cfg,
    titlesByProfile,
    promptsByProfile,
    imagePromptsByProfile,
    sessionPaths,
  };
});

ipcMain.handle('content:save', async (_event, payload) => {
  const {
    prompts,
    imagePrompts,
    titles,
    promptsPath,
    imagePromptsPath,
    titlesPath,
    titlesByProfile = {},
    promptsByProfile = {},
    imagePromptsByProfile = {},
    sessionPaths = {},
  } = payload || {};

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

  for (const [profileId, paths] of Object.entries(sessionPaths)) {
    const cast = paths;
    if (cast.promptsPath && typeof promptsByProfile[profileId] === 'string') {
      writes.push(fs.promises.writeFile(cast.promptsPath, promptsByProfile[profileId], 'utf-8'));
    }
    if (cast.imagePromptsPath && typeof imagePromptsByProfile[profileId] === 'string') {
      writes.push(fs.promises.writeFile(cast.imagePromptsPath, imagePromptsByProfile[profileId], 'utf-8'));
    }
    if (cast.titlesPath && typeof titlesByProfile[profileId] === 'string') {
      writes.push(fs.promises.writeFile(cast.titlesPath, titlesByProfile[profileId], 'utf-8'));
    }
  }

  await Promise.all(writes);
  return true;
});
