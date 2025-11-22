import React, { useEffect, useMemo, useState } from 'react';
import { HashRouter, Route, Routes, Navigate } from 'react-router-dom';
import Dashboard from './components/Dashboard';
import Workspaces from './components/Workspaces';
import Automator from './components/Automator';
import WatermarkCheck from './components/WatermarkCheck';
import Content from './components/Content';
import Telegram from './components/Telegram';
import Settings from './components/Settings';
import Errors from './components/Errors';
import Documentation from './components/Documentation';
import History from './components/History';

const App: React.FC = () => {
  const [loading, setLoading] = useState(true);
  const [progress, setProgress] = useState(0);

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
    <HashRouter>
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
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/workspaces" element={<Workspaces />} />
            <Route path="/automator" element={<Automator />} />
            <Route path="/watermark" element={<WatermarkCheck />} />
            <Route path="/content" element={<Content />} />
            <Route path="/telegram" element={<Telegram />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/errors" element={<Errors />} />
            <Route path="/docs" element={<Documentation />} />
            <Route path="/history" element={<History />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </div>
      </div>
    </HashRouter>
  );
};

export default App;
