import React, { useEffect, useMemo, useState } from 'react';
import { HashRouter, Route, Routes, Navigate, NavLink, useLocation } from 'react-router-dom';
import Dashboard from './components/Dashboard';
import Workspaces from './components/Workspaces';
import Automator from './components/Automator';
import WatermarkCheck from './components/WatermarkCheck';
import Content from './components/Content';
import Telegram from './components/Telegram';
import Publishing from './components/Publishing';
import Settings from './components/Settings';
import Errors from './components/Errors';
import Documentation from './components/Documentation';
import History from './components/History';

const navLinks = [
  { to: '/', label: 'Dashboard' },
  { to: '/workspaces', label: 'Workspaces' },
  { to: '/automator', label: 'Automator' },
  { to: '/watermark', label: 'Watermark Check' },
  { to: '/content', label: 'Content' },
  { to: '/publishing', label: 'Publishing' },
  { to: '/telegram', label: 'Telegram' },
  { to: '/settings', label: 'Settings' },
  { to: '/errors', label: 'Errors' },
  { to: '/docs', label: 'Docs' },
  { to: '/history', label: 'History' },
];

const App: React.FC = () => {
  const [loading, setLoading] = useState(true);
  const [progress, setProgress] = useState(0);
  const location = useLocation();

  useEffect(() => {
    let current = 0;
    const step = () => {
      current = Math.min(100, current + Math.max(5, Math.random() * 20));
      setProgress(Math.round(current));
      if (current >= 100) {
        setTimeout(() => setLoading(false), 180);
        return;
      }
      requestAnimationFrame(step);
    };

    const raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, []);

  const loadingMessage = useMemo(() => {
    if (progress < 35) return 'Подготавливаем интерфейс...';
    if (progress < 70) return 'Подгружаем модули и стили...';
    if (progress < 95) return 'Почти готово — связываем модули Electron';
    return 'Готово к работе';
  }, [progress]);

  return (
    <div className="relative min-h-screen bg-gray-950 text-gray-100">
      {loading && (
        <div className="absolute inset-0 z-50 flex flex-col items-center justify-center gap-4 bg-gradient-to-br from-slate-950 via-slate-900/90 to-slate-950">
          <div className="flex items-center gap-3 text-sm text-indigo-200">
            <div className="h-3 w-3 animate-ping rounded-full bg-indigo-400" aria-hidden />
            <span className="tracking-wide">Загрузка интерфейса</span>
          </div>
          <div className="w-72 overflow-hidden rounded-full border border-slate-800 bg-slate-900/80 shadow-lg">
            <div
              className="h-2 rounded-full bg-gradient-to-r from-indigo-400 via-blue-500 to-cyan-400 transition-all duration-150"
              style={{ width: `${progress}%` }}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={progress}
              role="progressbar"
            />
          </div>
          <p className="text-xs text-slate-400">{loadingMessage}</p>
          <p className="text-sm font-semibold text-indigo-100">{progress}%</p>
        </div>
      )}

      <div className={loading ? 'pointer-events-none opacity-0 transition-opacity duration-300' : 'opacity-100 transition-opacity duration-300'}>
        <div className="flex min-h-screen">
          <aside className="sticky top-0 hidden h-screen w-64 flex-shrink-0 flex-col border-r border-gray-900 bg-gray-900/70 px-4 py-6 shadow-xl lg:flex">
            <div className="mb-6 flex items-center gap-2 text-lg font-semibold text-white">
              <div className="h-3 w-3 rounded-full bg-indigo-400" />
              Sora Suite
            </div>
            <nav className="space-y-1 text-sm">
              {navLinks.map((link) => (
                <NavLink
                  key={link.to}
                  to={link.to}
                  className={({ isActive }) =>
                    `flex items-center justify-between rounded-lg px-3 py-2 transition hover:bg-gray-800/80 ${
                      isActive ? 'bg-indigo-500/10 text-indigo-200 ring-1 ring-indigo-400/40' : 'text-gray-300'
                    }`
                  }
                  end={link.to === '/'}
                >
                  <span>{link.label}</span>
                  <span className="text-[10px] uppercase tracking-wide text-gray-500">{link.to.replace('/', '') || 'home'}</span>
                </NavLink>
              ))}
            </nav>
          </aside>

          <main className="flex-1 overflow-y-auto bg-gray-950/80">
            <header className="sticky top-0 z-10 border-b border-gray-900/80 bg-gradient-to-r from-gray-950/95 via-gray-900/70 to-gray-950/95 px-4 py-4 backdrop-blur">
              <div className="flex flex-col gap-1">
                <p className="text-xs uppercase tracking-[0.2em] text-gray-500">{location.pathname === '/' ? 'Dashboard' : location.pathname.slice(1)}</p>
                <h1 className="text-xl font-semibold text-white">
                  {navLinks.find((link) => link.to === location.pathname)?.label || 'Sora Suite'}
                </h1>
              </div>
            </header>

            <div className="px-4 py-6 lg:px-6">
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/workspaces" element={<Workspaces />} />
                <Route path="/automator" element={<Automator />} />
                <Route path="/watermark" element={<WatermarkCheck />} />
                <Route path="/content" element={<Content />} />
                <Route path="/publishing" element={<Publishing />} />
                <Route path="/telegram" element={<Telegram />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="/errors" element={<Errors />} />
                <Route path="/docs" element={<Documentation />} />
                <Route path="/history" element={<History />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </div>
          </main>
        </div>
      </div>
    </div>
  );
};

const RoutedApp = () => (
  <HashRouter>
    <App />
  </HashRouter>
);

export default RoutedApp;
