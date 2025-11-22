import React, { useState } from 'react';
import { SettingsState } from '../types';

const defaultSettings: SettingsState = {
  directories: {
    rawDir: '/data/raw',
    mergedDir: '/data/merged',
    logsDir: '/data/logs',
    assetsDir: '/data/assets',
    tempDir: '/data/tmp',
  },
  ffmpeg: {
    codec: 'h264',
    preset: 'medium',
    crf: 18,
  },
  imageGen: {
    model: 'gai-1.0',
    apiKey: '***',
    width: 1080,
    height: 1920,
  },
  autogen: {
    delayMs: 500,
    maxConcurrent: 3,
    retryLimit: 2,
  },
  interface: {
    theme: 'dark',
    density: 'comfortable',
    showRightPanel: true,
  },
  maintenance: {
    clearCache: false,
    autoUpdate: true,
  },
};

const tabs = [
  'Directories',
  'FFmpeg',
  'Image Gen',
  'Autogen',
  'Interface',
  'Maintenance',
];

const Settings: React.FC = () => {
  const [activeTab, setActiveTab] = useState<string>('Directories');
  const [settings, setSettings] = useState<SettingsState>(defaultSettings);

  const updateSetting = (path: string, value: string | number | boolean) => {
    setSettings((current) => {
      const segments = path.split('.');
      const next = { ...current } as any;
      let node = next;
      for (let i = 0; i < segments.length - 1; i += 1) {
        const key = segments[i];
        node[key] = { ...node[key] };
        node = node[key];
      }
      node[segments[segments.length - 1]] = value;
      return next;
    });
  };

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Settings</h1>
          <p className="text-sm text-gray-400">Configure paths, ffmpeg presets, image generation, autogen, and UI preferences.</p>
        </div>
        <button className="rounded-lg bg-gradient-to-r from-blue-500 to-indigo-500 px-4 py-2 text-sm font-medium text-white shadow">
          Save All
        </button>
      </div>

      <div className="flex gap-4 overflow-x-auto rounded-xl border border-gray-800 bg-gray-900 p-2">
        {tabs.map((tab) => (
          <button
            key={tab}
            className={`rounded-lg px-4 py-2 text-sm font-medium ${
              activeTab === tab ? 'bg-gray-800 text-white' : 'text-gray-400 hover:text-white'
            }`}
            onClick={() => setActiveTab(tab)}
          >
            {tab}
          </button>
        ))}
      </div>

      {activeTab === 'Directories' && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {(
            [
              ['RAW Folder', 'directories.rawDir'],
              ['Merged Folder', 'directories.mergedDir'],
              ['Logs Folder', 'directories.logsDir'],
              ['Assets Folder', 'directories.assetsDir'],
              ['Temp Folder', 'directories.tempDir'],
            ] as const
          ).map(([label, key]) => (
            <label key={key} className="space-y-1 text-sm text-gray-200">
              <span className="text-xs text-gray-400">{label}</span>
              <input
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
                value={(settings as any)[key.split('.')[0]][key.split('.')[1]]}
                onChange={(event) => updateSetting(key, event.target.value)}
              />
            </label>
          ))}
        </div>
      )}

      {activeTab === 'FFmpeg' && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">Codec</span>
            <input
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={settings.ffmpeg.codec}
              onChange={(event) => updateSetting('ffmpeg.codec', event.target.value)}
            />
          </label>
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">Preset</span>
            <input
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={settings.ffmpeg.preset}
              onChange={(event) => updateSetting('ffmpeg.preset', event.target.value)}
            />
          </label>
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">CRF</span>
            <input
              type="number"
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={settings.ffmpeg.crf ?? ''}
              onChange={(event) => updateSetting('ffmpeg.crf', parseInt(event.target.value, 10))}
            />
          </label>
        </div>
      )}

      {activeTab === 'Image Gen' && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">Model</span>
            <input
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={settings.imageGen.model}
              onChange={(event) => updateSetting('imageGen.model', event.target.value)}
            />
          </label>
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">API Key</span>
            <input
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={settings.imageGen.apiKey}
              onChange={(event) => updateSetting('imageGen.apiKey', event.target.value)}
            />
          </label>
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">Width</span>
            <input
              type="number"
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={settings.imageGen.width ?? ''}
              onChange={(event) => updateSetting('imageGen.width', parseInt(event.target.value, 10))}
            />
          </label>
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">Height</span>
            <input
              type="number"
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={settings.imageGen.height ?? ''}
              onChange={(event) => updateSetting('imageGen.height', parseInt(event.target.value, 10))}
            />
          </label>
        </div>
      )}

      {activeTab === 'Autogen' && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">Loop Delay (ms)</span>
            <input
              type="number"
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={settings.autogen.delayMs}
              onChange={(event) => updateSetting('autogen.delayMs', parseInt(event.target.value, 10))}
            />
          </label>
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">Max Concurrent</span>
            <input
              type="number"
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={settings.autogen.maxConcurrent}
              onChange={(event) => updateSetting('autogen.maxConcurrent', parseInt(event.target.value, 10))}
            />
          </label>
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">Retry Limit</span>
            <input
              type="number"
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={settings.autogen.retryLimit}
              onChange={(event) => updateSetting('autogen.retryLimit', parseInt(event.target.value, 10))}
            />
          </label>
        </div>
      )}

      {activeTab === 'Interface' && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">Theme</span>
            <select
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={settings.interface.theme}
              onChange={(event) => updateSetting('interface.theme', event.target.value)}
            >
              <option value="dark">Dark</option>
              <option value="light">Light</option>
            </select>
          </label>
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">Density</span>
            <select
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={settings.interface.density}
              onChange={(event) => updateSetting('interface.density', event.target.value)}
            >
              <option value="compact">Compact</option>
              <option value="comfortable">Comfortable</option>
            </select>
          </label>
          <label className="flex items-center gap-2 text-sm text-gray-200">
            <input
              type="checkbox"
              className="h-4 w-4 rounded border-gray-700 bg-gray-800 text-indigo-500"
              checked={settings.interface.showRightPanel}
              onChange={(event) => updateSetting('interface.showRightPanel', event.target.checked)}
            />
            <span className="text-xs text-gray-400">Show Right Panel</span>
          </label>
        </div>
      )}

      {activeTab === 'Maintenance' && (
        <div className="space-y-3 rounded-xl border border-gray-800 bg-gray-900 p-4">
          <label className="flex items-center justify-between text-sm text-gray-200">
            <div>
              <p className="text-xs uppercase text-gray-400">Cache</p>
              <p className="text-sm text-gray-300">Clear temporary files and thumbnails</p>
            </div>
            <input
              type="checkbox"
              className="h-4 w-4 rounded border-gray-700 bg-gray-800 text-indigo-500"
              checked={settings.maintenance.clearCache}
              onChange={(event) => updateSetting('maintenance.clearCache', event.target.checked)}
            />
          </label>
          <label className="flex items-center justify-between text-sm text-gray-200">
            <div>
              <p className="text-xs uppercase text-gray-400">Updates</p>
              <p className="text-sm text-gray-300">Auto-download latest patches</p>
            </div>
            <input
              type="checkbox"
              className="h-4 w-4 rounded border-gray-700 bg-gray-800 text-indigo-500"
              checked={settings.maintenance.autoUpdate}
              onChange={(event) => updateSetting('maintenance.autoUpdate', event.target.checked)}
            />
          </label>
        </div>
      )}
    </div>
  );
};

export default Settings;
