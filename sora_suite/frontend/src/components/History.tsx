import React, { useEffect, useState } from 'react';
import { HistoryEntry } from '../types';
import { tailHistory } from '../api/backend';

const badge = {
  success: 'bg-emerald-900 text-emerald-200',
  failed: 'bg-red-900 text-red-200',
  pending: 'bg-amber-900 text-amber-100',
  running: 'bg-blue-900 text-blue-100',
};

const History: React.FC = () => {
  const [items, setItems] = useState<HistoryEntry[]>([]);

  useEffect(() => {
    tailHistory()?.then((list) => setItems(list || []));
  }, []);

  return (
    <div className="p-6 space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">History</h1>
        <p className="text-sm text-gray-400">Track automation runs and their outcomes.</p>
      </div>
      <div className="space-y-3">
        {items.length === 0 && (
          <div className="rounded-xl border border-dashed border-gray-800 bg-gray-900/70 p-4 text-sm text-gray-400">
            История пуста. Запустите автогенерацию/скачивание, чтобы увидеть записи.
          </div>
        )}
        {items.map((entry) => (
          <div key={entry.id} className="flex items-center justify-between rounded-xl border border-gray-800 bg-gray-900 p-4">
            <div>
              <p className="text-sm font-semibold text-white">{entry.action}</p>
              <p className="text-xs text-gray-400">{entry.timestamp} • {entry.actor}</p>
              {entry.details && <p className="mt-1 text-xs text-gray-500">{entry.details}</p>}
            </div>
            <span className={`rounded-full px-3 py-1 text-xs font-semibold capitalize ${badge[entry.status] || 'bg-gray-800 text-gray-200'}`}>
              {entry.status}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
};

export default History;
