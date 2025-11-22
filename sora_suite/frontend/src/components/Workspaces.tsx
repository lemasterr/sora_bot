import React, { useEffect, useMemo, useState } from 'react';
import { RefreshCcw, Play, Square, Bug } from 'lucide-react';
import { AppConfigSession, BackendTaskEvent, ChromeProfileInfo, WorkspaceProfile } from '../types';
import { launchChrome, listChromeProfiles, loadConfig, onTaskEvent, startTask, stopTask, stopChrome } from '../api/backend';

const mapSessionToProfile = (session: AppConfigSession): WorkspaceProfile => ({
  id: session.id || session.name || session.chrome_profile || 'session',
  name: session.name || session.chrome_profile || 'Chrome Profile',
  port: session.cdp_port || 9222,
  status: 'idle',
  downloadLimit: session.max_videos,
  mergeLimit: undefined,
  chromeProfile: session.chrome_profile,
});

const Workspaces: React.FC = () => {
  const [profiles, setProfiles] = useState<WorkspaceProfile[]>([]);
  const [running, setRunning] = useState<Record<string, number>>({});
  const [chromeProfiles, setChromeProfiles] = useState<ChromeProfileInfo[]>([]);

  useEffect(() => {
    loadConfig()?.then((cfg) => {
      const sessions = cfg?.autogen?.sessions || [];
      const mapped = sessions.map(mapSessionToProfile);
      setProfiles(mapped);
    });

    listChromeProfiles()?.then((profiles) => {
      if (profiles) setChromeProfiles(profiles);
    });

    const unsub = onTaskEvent((event: BackendTaskEvent) => {
      if (event.kind === 'log') {
        setProfiles((current) =>
          current.map((profile) =>
            running[profile.id] === event.pid
              ? {
                  ...profile,
                  lastLog: {
                    timestamp: new Date().toISOString(),
                    level: event.channel === 'stderr' ? 'error' : 'info',
                    message: event.line || '',
                  },
                  status: 'running',
                }
              : profile,
          ),
        );
      }
      if (event.kind === 'exit') {
        setProfiles((current) =>
          current.map((profile) =>
            running[profile.id] === event.pid
              ? {
                  ...profile,
                  status: event.code === 0 ? 'idle' : 'error',
                  lastLog: {
                    timestamp: new Date().toISOString(),
                    level: event.code === 0 ? 'info' : 'error',
                    message: event.code === 0 ? 'Задача завершена' : `Ошибка (код ${event.code})`,
                  },
                }
              : profile,
          ),
        );
        setRunning((curr) => {
          const next = { ...curr };
          Object.entries(next).forEach(([key, pid]) => {
            if (pid === event.pid) delete next[key];
          });
          return next;
        });
      }
    });
    return () => {
      unsub?.();
    };
  }, [running]);

  const handleRefresh = () => {
    loadConfig()?.then((cfg) => {
      const sessions = cfg?.autogen?.sessions || [];
      setProfiles(sessions.map(mapSessionToProfile));
    });
  };

  const handleLimitChange = (id: string, field: 'downloadLimit' | 'mergeLimit', value: number) => {
    setProfiles((current) =>
      current.map((profile) =>
        profile.id === id ? { ...profile, [field]: isNaN(value) ? undefined : value } : profile,
      ),
    );
  };

  const handleLaunchChrome = async (profile: WorkspaceProfile) => {
    if (!profile.chromeProfile) return;
    const portOverride = profile.port || chromeProfiles.find((p) => p.name === profile.chromeProfile)?.cdp_port || 9222;
    const launched = await launchChrome({ profileName: profile.chromeProfile, port: portOverride });
    if (launched?.pid) {
      setProfiles((current) =>
        current.map((p) => (p.id === profile.id ? { ...p, chromePid: launched.pid, port: launched.port } : p)),
      );
    }
  };

  const handleStart = async (profile: WorkspaceProfile) => {
    if (profile.chromeProfile && !profile.chromePid) {
      const portOverride =
        profile.port || chromeProfiles.find((p) => p.name === profile.chromeProfile)?.cdp_port || 9222;
      const launched = await launchChrome({ profileName: profile.chromeProfile, port: portOverride });
      if (launched?.pid) {
        setProfiles((current) =>
          current.map((p) => (p.id === profile.id ? { ...p, chromePid: launched.pid, port: launched.port } : p)),
        );
      }
    }
    const env: Record<string, string> = {
      SORA_INSTANCE_NAME: profile.id,
    };
    if (profile.port) {
      env.CDP_ENDPOINT = `http://127.0.0.1:${profile.port}`;
    }
    const result = await startTask({ task: 'autogen', env });
    if (result?.pid) {
      setRunning((curr) => ({ ...curr, [profile.id]: result.pid }));
      setProfiles((current) =>
        current.map((p) => (p.id === profile.id ? { ...p, status: 'running' as const } : p)),
      );
    }
  };

  const handleStop = async (profile: WorkspaceProfile) => {
    const pid = running[profile.id];
    if (pid) {
      await stopTask(pid);
    }
    if (profile.chromePid) {
      await stopChrome(profile.chromePid);
    }
    setRunning((curr) => {
      const next = { ...curr };
      delete next[profile.id];
      return next;
    });
    setProfiles((current) =>
      current.map((p) => (p.id === profile.id ? { ...p, status: 'idle' as const, chromePid: undefined } : p)),
    );
  };

  const liveLogLabel = useMemo(
    () => ({
      info: 'text-blue-300',
      warn: 'text-amber-300',
      error: 'text-red-400',
    }),
    [],
  );

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Workspaces</h1>
          <p className="text-sm text-gray-400">Manage Chrome profiles, quick controls, and per-profile limits.</p>
        </div>
        <button
          className="flex items-center gap-2 rounded-lg bg-gradient-to-r from-blue-500 to-indigo-500 px-4 py-2 text-sm font-medium text-white shadow"
          onClick={handleRefresh}
        >
          <RefreshCcw size={16} /> Refresh Profiles
        </button>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {profiles.map((profile) => (
          <div key={profile.id} className="rounded-xl border border-gray-800 bg-gray-900 p-4 shadow-lg">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-lg font-semibold text-white">{profile.name}</h2>
                <p className="text-xs text-gray-400">DevTools: {profile.port}</p>
              </div>
              <span
                className={`rounded-full px-3 py-1 text-xs font-semibold capitalize ${
                  profile.status === 'running'
                    ? 'bg-emerald-900 text-emerald-200'
                    : profile.status === 'error'
                      ? 'bg-red-900 text-red-200'
                      : 'bg-gray-800 text-gray-200'
                }`}
              >
                {profile.status}
              </span>
            </div>

            <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
              <div className="space-y-1">
                <label className="text-xs text-gray-400">Download Limit</label>
                <input
                  type="number"
                  className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
                  value={profile.downloadLimit ?? ''}
                  onChange={(e) => handleLimitChange(profile.id, 'downloadLimit', parseInt(e.target.value, 10))}
                  placeholder="Unlimited"
                />
              </div>
              <div className="space-y-1">
                <label className="text-xs text-gray-400">Merge Limit</label>
                <input
                  type="number"
                  className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
                  value={profile.mergeLimit ?? ''}
                  onChange={(e) => handleLimitChange(profile.id, 'mergeLimit', parseInt(e.target.value, 10))}
                  placeholder="Unlimited"
                />
              </div>
            </div>

            <div className="mt-4 flex items-center gap-2 text-xs text-gray-400">
              <span className="rounded-md bg-gray-800 px-2 py-1">{profile.chromeProfile || 'No Chrome profile'}</span>
              <div className="flex flex-1 items-center gap-2 rounded-lg bg-gray-800 px-3 py-2">
                <span className={`text-[10px] uppercase tracking-wide ${liveLogLabel[profile.lastLog?.level ?? 'info']}`}>
                  {profile.lastLog?.level ?? 'info'}
                </span>
                <p className="truncate text-gray-200">{profile.lastLog?.message ?? 'No recent logs'}</p>
              </div>
            </div>

            <div className="mt-4 flex items-center gap-3">
              <button
                className="flex items-center justify-center gap-2 rounded-lg border border-gray-700 px-3 py-2 text-sm font-medium text-gray-100"
                onClick={() => handleLaunchChrome(profile)}
              >
                <Play size={16} /> Chrome
              </button>
              <button
                className="flex flex-1 items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-blue-500 to-indigo-500 px-3 py-2 text-sm font-medium text-white shadow"
                onClick={() => handleStart(profile)}
                disabled={profile.status === 'running'}
              >
                <Play size={16} /> Start
              </button>
              <button
                className="flex items-center justify-center gap-2 rounded-lg border border-gray-700 px-3 py-2 text-sm font-medium text-gray-100"
                onClick={() => handleStop(profile)}
                disabled={!running[profile.id]}
              >
                <Square size={16} /> Stop
              </button>
              <button
                className="flex items-center justify-center gap-2 rounded-lg border border-gray-700 px-3 py-2 text-sm font-medium text-gray-100"
                onClick={() => {
                  if (profile.port) {
                    window.open(`http://127.0.0.1:${profile.port}`, '_blank');
                  }
                }}
              >
                <Bug size={16} /> Debugger
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default Workspaces;
