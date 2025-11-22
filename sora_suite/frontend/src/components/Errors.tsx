import React from 'react';
import { ErrorEvent } from '../types';

const sampleErrors: ErrorEvent[] = [
  {
    id: 'err-1',
    timestamp: new Date().toISOString(),
    level: 'error',
    message: 'Network timeout while downloading',
  },
  {
    id: 'err-2',
    timestamp: new Date().toISOString(),
    level: 'fatal',
    message: 'Worker crashed during merge',
    stack: 'Traceback...'
  },
];

const Errors: React.FC = () => (
  <div className="p-6 space-y-4">
    <div>
      <h1 className="text-2xl font-semibold">Errors</h1>
      <p className="text-sm text-gray-400">Review critical application errors.</p>
    </div>
    <div className="space-y-3">
      {sampleErrors.map((error) => (
        <div key={error.id} className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <div className="flex items-center justify-between">
            <p className="text-sm font-semibold text-white">{error.message}</p>
            <span className={`rounded-full px-3 py-1 text-xs font-semibold ${error.level === 'fatal' ? 'bg-red-900 text-red-200' : 'bg-amber-900 text-amber-200'}`}>
              {error.level}
            </span>
          </div>
          <p className="text-xs text-gray-400">{error.timestamp}</p>
          {error.stack && <pre className="mt-2 rounded bg-gray-800 p-2 text-xs text-gray-300">{error.stack}</pre>}
        </div>
      ))}
    </div>
  </div>
);

export default Errors;
