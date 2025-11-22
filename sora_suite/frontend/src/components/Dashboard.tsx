import React, { useEffect, useMemo, useState } from 'react';
import { History } from 'lucide-react';
import { AppConfig, BackendTaskEvent, HistoryEntry } from '../types';
import { loadConfig, onTaskEvent, tailHistory } from '../api/backend';

const Dashboard: React.FC = () => {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [running, setRunning] = useState<Record<string, BackendTaskEvent>>({});

  useEffect(() => {
    loadConfig()?.then((cfg) => setConfig(cfg || null));
    tailHistory()?.then((items) => setHistory(items || []));

    const unsub = onTaskEvent((event) => {
      if (event.kind === 'start') {
        setRunning((curr) => ({ ...curr, [event.pid]: event }));
      }
      if (event.kind === 'exit') {
        setRunning((curr) => {
          const next = { ...curr };
          delete next[event.pid];
          return next;
        });
        setHistory((prev) => [{
          id: `${event.task}-${event.pid}`,
          actor: event.task,
          action: 'Задача завершена',
          timestamp: new Date().toISOString(),
          status: event.code === 0 ? 'success' : 'failed',
          details: event.code === 0 ? undefined : `Код ${event.code}`,
        }, ...prev].slice(0, 50));
      }
    });
    return () => unsub?.();
  }, []);

  const stats = useMemo(() => {
    const sessions = config?.autogen?.sessions?.length ?? 0;
    const downloadsDir = config?.downloads_dir || '—';
    const mergedDir = config?.merged_dir || '—';
    const runningCount = Object.keys(running).length;
    const lastHistory = history.slice(0, 4);
    return { sessions, downloadsDir, mergedDir, runningCount, lastHistory };
  }, [config, history, running]);

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Dashboard</h1>
          <p className="text-sm text-gray-400">Быстрый обзор сессий, путей и активных задач.</p>
        </div>
        <div className="flex items-center gap-2 rounded-lg bg-gray-800 px-3 py-2 text-xs uppercase tracking-wide text-gray-300">
          <span className="text-indigo-300">{stats.runningCount}</span> Running
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4 shadow-lg">
          <p className="text-xs uppercase text-gray-400">Sessions</p>
          <p className="mt-2 text-3xl font-semibold text-white">{stats.sessions}</p>
          <p className="text-xs text-gray-500">Автоген/скачивание профилей</p>
        </div>
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4 shadow-lg">
          <p className="text-xs uppercase text-gray-400">Downloads dir</p>
          <p className="mt-2 truncate text-sm font-semibold text-white">{stats.downloadsDir}</p>
          <p className="text-xs text-gray-500">Где сохраняются RAW клипы</p>
        </div>
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4 shadow-lg">
          <p className="text-xs uppercase text-gray-400">Merged dir</p>
          <p className="mt-2 truncate text-sm font-semibold text-white">{stats.mergedDir}</p>
          <p className="text-xs text-gray-500">Готовые ролики для загрузки</p>
        </div>
      </div>

      <div className="rounded-xl border border-gray-800 bg-gray-900 p-4 shadow-lg">
        <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-white">
          <History size={16} /> Последние события
        </div>
        {stats.lastHistory.length === 0 && (
          <p className="text-sm text-gray-400">История пока пуста.</p>
        )}
        <div className="space-y-2">
          {stats.lastHistory.map((entry) => (
            <div key={entry.id} className="flex items-center justify-between rounded-lg border border-gray-800 bg-gray-800 px-3 py-2 text-sm text-gray-200">
              <div>
                <p className="font-semibold text-white">{entry.action}</p>
                <p className="text-xs text-gray-400">{entry.timestamp} • {entry.actor}</p>
              </div>
              <span className={`rounded-full px-3 py-1 text-[11px] font-semibold capitalize ${entry.status === 'success'
                ? 'bg-emerald-900 text-emerald-200'
                : entry.status === 'failed'
                  ? 'bg-red-900 text-red-200'
                  : 'bg-gray-800 text-gray-200'
              }`}>
                {entry.status}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
