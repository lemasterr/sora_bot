import React, { useMemo, useState } from 'react';
import { RefreshCcw, Play, Square, Bug } from 'lucide-react';
import { WorkspaceProfile } from '../types';

const mockProfiles: WorkspaceProfile[] = [
  {
    id: 'profile-1',
    name: 'Creator Alpha',
    port: 9222,
    status: 'running',
    downloadLimit: 20,
    mergeLimit: 5,
    lastLog: {
      timestamp: new Date().toISOString(),
      level: 'info',
      message: 'Downloading latest batch…',
    },
  },
  {
    id: 'profile-2',
    name: 'Creator Beta',
    port: 9333,
    status: 'idle',
    downloadLimit: 10,
    mergeLimit: 3,
    lastLog: {
      timestamp: new Date().toISOString(),
      level: 'warn',
      message: 'Awaiting new prompts.',
    },
  },
];

const Workspaces: React.FC = () => {
  const [profiles, setProfiles] = useState<WorkspaceProfile[]>(mockProfiles);

  const handleRefresh = () => {
    // Placeholder: replace with IPC call to reload Chrome profiles
    setProfiles([...mockProfiles]);
  };

  const handleLimitChange = (id: string, field: 'downloadLimit' | 'mergeLimit', value: number) => {
    setProfiles((current) =>
      current.map((profile) =>
        profile.id === id ? { ...profile, [field]: isNaN(value) ? undefined : value } : profile,
      ),
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
              <div className="flex flex-1 items-center gap-2 rounded-lg bg-gray-800 px-3 py-2">
                <span className={`text-[10px] uppercase tracking-wide ${liveLogLabel[profile.lastLog?.level ?? 'info']}`}>
                  {profile.lastLog?.level ?? 'info'}
                </span>
                <p className="truncate text-gray-200">{profile.lastLog?.message ?? 'No recent logs'}</p>
              </div>
            </div>

            <div className="mt-4 flex items-center gap-3">
              <button className="flex flex-1 items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-blue-500 to-indigo-500 px-3 py-2 text-sm font-medium text-white shadow">
                <Play size={16} /> Start
              </button>
              <button className="flex items-center justify-center gap-2 rounded-lg border border-gray-700 px-3 py-2 text-sm font-medium text-gray-100">
                <Square size={16} /> Stop
              </button>
              <button className="flex items-center justify-center gap-2 rounded-lg border border-gray-700 px-3 py-2 text-sm font-medium text-gray-100">
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
