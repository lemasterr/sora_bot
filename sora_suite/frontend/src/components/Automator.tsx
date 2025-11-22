import React, { useMemo, useState } from 'react';
import { AlertTriangle, PlusCircle, Save } from 'lucide-react';
import { AutomatorSequence, AutomatorStep } from '../types';

const Automator: React.FC = () => {
  const [sequence, setSequence] = useState<AutomatorSequence>({
    id: 'draft',
    name: 'New Sequence',
    description: 'Define chained actions with resilient execution.',
    steps: [],
  });

  const createStep = (type: AutomatorStep['type']) => {
    setSequence((current) => ({
      ...current,
      steps: [
        ...current.steps,
        {
          id: `${type}-${Date.now()}`,
          type,
          label: `${type} step`,
          profileIds: [],
          params: {},
        },
      ],
    }));
  };

  const persistSequence = () => {
    try {
      // Replace with main-process IPC call guarded by try/catch to prevent crashes during long loops
      console.info('Persisting automator sequence', sequence);
    } catch (error) {
      console.error('Failed to persist sequence', error);
    }
  };

  const stepBadges = useMemo(
    () => ({
      generate: 'bg-blue-900 text-blue-100',
      wait: 'bg-amber-900 text-amber-100',
      download: 'bg-emerald-900 text-emerald-100',
      custom: 'bg-indigo-900 text-indigo-100',
    }),
    [],
  );

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Automator</h1>
          <p className="text-sm text-gray-400">Build resilient multi-step flows with guarded execution.</p>
        </div>
        <div className="flex gap-2">
          <button
            className="flex items-center gap-2 rounded-lg border border-gray-700 px-3 py-2 text-sm text-gray-100"
            onClick={() => createStep('wait')}
          >
            <PlusCircle size={16} /> Add Wait
          </button>
          <button
            className="flex items-center gap-2 rounded-lg border border-gray-700 px-3 py-2 text-sm text-gray-100"
            onClick={() => createStep('download')}
          >
            <PlusCircle size={16} /> Add Download
          </button>
          <button
            className="flex items-center gap-2 rounded-lg bg-gradient-to-r from-blue-500 to-indigo-500 px-4 py-2 text-sm font-medium text-white shadow"
            onClick={persistSequence}
          >
            <Save size={16} /> Save Sequence
          </button>
        </div>
      </div>

      <div className="rounded-xl border border-gray-800 bg-gray-900 p-4 shadow-lg">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <p className="text-xs uppercase text-gray-400">Sequence Name</p>
            <h2 className="text-lg font-semibold text-white">{sequence.name}</h2>
            <p className="text-sm text-gray-400">{sequence.description}</p>
          </div>
          <div className="flex items-center gap-2 rounded-lg bg-gray-800 px-3 py-2 text-sm text-amber-200">
            <AlertTriangle size={16} />
            Main-process IPC calls must handle errors to avoid crashes.
          </div>
        </div>

        <div className="space-y-3">
          {sequence.steps.length === 0 && (
            <div className="rounded-lg border border-dashed border-gray-700 bg-gray-800 px-4 py-6 text-center text-gray-400">
              Add steps to orchestrate generate → wait → download flows.
            </div>
          )}

          {sequence.steps.map((step) => (
            <div key={step.id} className="flex items-center justify-between rounded-lg border border-gray-800 bg-gray-800 px-4 py-3">
              <div className="flex items-center gap-3">
                <span className={`rounded-full px-3 py-1 text-xs font-semibold capitalize ${stepBadges[step.type]}`}>
                  {step.type}
                </span>
                <div>
                  <p className="text-sm font-medium text-white">{step.label}</p>
                  <p className="text-xs text-gray-400">Targets profiles: {step.profileIds.join(', ') || 'None selected'}</p>
                </div>
              </div>
              <button
                className="text-sm text-indigo-300 hover:text-indigo-200"
                onClick={() => console.log('Open step detail', step.id)}
              >
                Configure
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export default Automator;
