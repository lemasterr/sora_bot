import { app, BrowserWindow, ipcMain, shell } from 'electron';
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';

const isDev = Boolean(process.env.VITE_DEV_SERVER_URL);
let mainWindow: any | null = null;

const createWindow = () => {
  mainWindow = new BrowserWindow({
    width: 1320,
    height: 820,
    minWidth: 1100,
    minHeight: 720,
    frame: false,
    titleBarStyle: 'hidden',
    transparent: false,
    backgroundColor: '#09090b',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  mainWindow.setMenuBarVisibility(false);

  if (isDev) {
    const devURL = process.env.VITE_DEV_SERVER_URL ?? 'http://localhost:5173';
    mainWindow.loadURL(devURL);
    mainWindow.webContents.openDevTools({ mode: 'detach' });
  } else {
    const indexPath = path.join(__dirname, '../dist/index.html');
    if (fs.existsSync(indexPath)) {
      mainWindow.loadFile(indexPath);
    } else {
      const message = `Renderer bundle missing at ${indexPath}. Run \`npm run build\` first.`;
      mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(`<pre style="background:#0b0b0f;color:#e5e7eb;padding:24px;font-family:Inter, monospace">${message}</pre>`)}`);
    }
  }

  mainWindow.webContents.setWindowOpenHandler(({ url }: { url: string }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
};

app.whenReady().then(() => {
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

type PythonTask = 'pipeline' | 'autogen' | 'downloader' | 'watermark' | 'youtube' | 'tiktok';

interface PythonCommand {
  task: PythonTask;
  payload?: Record<string, unknown>;
}

const sendLog = (payload: { message: string; source: 'stdout' | 'stderr'; pid?: number }) => {
  mainWindow?.webContents.send('python-log', {
    ...payload,
    timestamp: Date.now(),
  });
};

ipcMain.handle('run-python', async (_event: any, command: PythonCommand) => {
  const pythonExecutable = process.env.PYTHON || process.env.PYTHON_PATH || 'python';
  const payloadString = JSON.stringify(command?.payload ?? {});
  const args = ['-m', 'sora_suite.bridge', '--task', command?.task ?? 'pipeline', '--payload', payloadString];

  return new Promise<{ code?: number }>((resolve) => {
    const child = spawn(pythonExecutable, args, {
      env: {
        ...process.env,
        ELECTRON_RUN_AS_NODE: '0',
      },
      cwd: app.getAppPath(),
      windowsHide: true,
    });

    sendLog({ message: `[main] Spawned python pid=${child.pid}`, source: 'stdout', pid: child.pid ?? undefined });

    child.stdout?.on('data', (data: Buffer) => {
      data
        .toString()
        .split(/\r?\n/)
        .filter(Boolean)
        .forEach((line) => sendLog({ message: line, source: 'stdout', pid: child.pid ?? undefined }));
    });

    child.stderr?.on('data', (data: Buffer) => {
      data
        .toString()
        .split(/\r?\n/)
        .filter(Boolean)
        .forEach((line) => sendLog({ message: line, source: 'stderr', pid: child.pid ?? undefined }));
    });

    child.on('close', (code: number | null) => {
      sendLog({
        message: `[main] Python exited with code ${code ?? 'null'}`,
        source: code && code > 0 ? 'stderr' : 'stdout',
        pid: child.pid ?? undefined,
      });
      resolve({ code: code ?? undefined });
    });

    child.on('error', (err: any) => {
      sendLog({ message: `[main] Failed to start Python: ${err.message}`, source: 'stderr' });
      resolve({ code: -1 });
    });
  });
});

process.on('uncaughtException', (err) => {
  sendLog({ message: `[main] Uncaught exception: ${err.message}`, source: 'stderr' });
});

process.on('unhandledRejection', (reason) => {
  sendLog({ message: `[main] Unhandled rejection: ${String(reason)}`, source: 'stderr' });
});
