import { app, BrowserWindow } from 'electron';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

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
      sandbox: true,
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
