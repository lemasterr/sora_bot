import React from 'react';
import { HistoryEntry } from '../types';

const demoHistory: HistoryEntry[] = [
  { id: 'h-1', timestamp: new Date().toISOString(), actor: 'Automator', action: 'Generate batch', status: 'success' },
  { id: 'h-2', timestamp: new Date().toISOString(), actor: 'Downloader', action: 'Fetch clips', status: 'running' },
];

const History: React.FC = () => (
  <div className="p-6 space-y-4">
    <div>
      <h1 className="text-2xl font-semibold">History</h1>
      <p className="text-sm text-gray-400">Track automation runs and their outcomes.</p>
    </div>
    <div className="space-y-3">
      {demoHistory.map((entry) => (
        <div key={entry.id} className="flex items-center justify-between rounded-xl border border-gray-800 bg-gray-900 p-4">
          <div>
            <p className="text-sm font-semibold text-white">{entry.action}</p>
            <p className="text-xs text-gray-400">{entry.timestamp} • {entry.actor}</p>
          </div>
          <span className={`rounded-full px-3 py-1 text-xs font-semibold capitalize ${entry.status === 'success' ? 'bg-emerald-900 text-emerald-200' : entry.status === 'failed' ? 'bg-red-900 text-red-200' : 'bg-gray-800 text-gray-200'}`}>
            {entry.status}
          </span>
        </div>
      ))}
    </div>
  </div>
);

export default History;
