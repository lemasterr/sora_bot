import React, { useEffect, useState } from 'react';
import { AppConfig } from '../types';
import { loadConfig, updateConfig } from '../api/backend';

const tabs = ['Directories', 'Chrome', 'FFmpeg', 'GenAI', 'Maintenance'];

const Settings: React.FC = () => {
  const [activeTab, setActiveTab] = useState<string>('Directories');
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    loadConfig()?.then((cfg) => setConfig(cfg || {}));
  }, []);

  const updateSetting = (path: string, value: string | number | boolean) => {
    setConfig((current) => {
      const next = { ...(current || {}) } as any;
      const segments = path.split('.');
      let node = next;
      for (let i = 0; i < segments.length - 1; i += 1) {
        const key = segments[i];
        node[key] = { ...(node[key] || {}) };
        node = node[key];
      }
      node[segments[segments.length - 1]] = value;
      return next;
    });
  };

  const persist = async () => {
    if (!config) return;
    setSaving(true);
    await updateConfig(config);
    setSaving(false);
  };

  if (!config) {
    return <div className="p-6 text-sm text-gray-300">Загрузка конфигурации...</div>;
  }

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Settings</h1>
          <p className="text-sm text-gray-400">Пути проекта, Chrome/CDP, ffmpeg, GenAI и обслуживание.</p>
        </div>
        <button
          className="rounded-lg bg-gradient-to-r from-blue-500 to-indigo-500 px-4 py-2 text-sm font-medium text-white shadow"
          onClick={persist}
        >
          {saving ? 'Saving…' : 'Save All'}
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
              ['RAW/Downloads', 'downloads_dir'],
              ['Blurred', 'blurred_dir'],
              ['Merged', 'merged_dir'],
              ['History file', 'history_file'],
            ] as const
          ).map(([label, key]) => (
            <label key={key} className="space-y-1 text-sm text-gray-200">
              <span className="text-xs text-gray-400">{label}</span>
              <input
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
                value={(config as any)[key] || ''}
                onChange={(event) => updateSetting(key, event.target.value)}
              />
            </label>
          ))}
        </div>
      )}

      {activeTab === 'Chrome' && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">DevTools port</span>
            <input
              type="number"
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={config.chrome?.cdp_port ?? ''}
              onChange={(event) => updateSetting('chrome.cdp_port', parseInt(event.target.value, 10))}
            />
          </label>
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">Chrome binary</span>
            <input
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={config.chrome?.binary || ''}
              onChange={(event) => updateSetting('chrome.binary', event.target.value)}
            />
          </label>
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">User data dir</span>
            <input
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={config.chrome?.user_data_dir || ''}
              onChange={(event) => updateSetting('chrome.user_data_dir', event.target.value)}
            />
          </label>
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">Active profile</span>
            <input
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={config.chrome?.active_profile || ''}
              onChange={(event) => updateSetting('chrome.active_profile', event.target.value)}
            />
          </label>
        </div>
      )}

      {activeTab === 'FFmpeg' && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">Binary</span>
            <input
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={config.ffmpeg?.binary || ''}
              onChange={(event) => updateSetting('ffmpeg.binary', event.target.value)}
            />
          </label>
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">Codec</span>
            <input
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={config.ffmpeg?.vcodec || ''}
              onChange={(event) => updateSetting('ffmpeg.vcodec', event.target.value)}
            />
          </label>
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">Preset</span>
            <input
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={config.ffmpeg?.preset || ''}
              onChange={(event) => updateSetting('ffmpeg.preset', event.target.value)}
            />
          </label>
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">CRF</span>
            <input
              type="number"
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={config.ffmpeg?.crf ?? ''}
              onChange={(event) => updateSetting('ffmpeg.crf', parseInt(event.target.value, 10))}
            />
          </label>
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">Format</span>
            <input
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={config.ffmpeg?.format || ''}
              onChange={(event) => updateSetting('ffmpeg.format', event.target.value)}
            />
          </label>
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">Post chain</span>
            <textarea
              className="h-24 w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={config.ffmpeg?.post_chain || ''}
              onChange={(event) => updateSetting('ffmpeg.post_chain', event.target.value)}
            />
          </label>
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">Copy audio</span>
            <input
              type="checkbox"
              className="h-4 w-4 rounded border-gray-700 bg-gray-800 text-indigo-500"
              checked={!!config.ffmpeg?.copy_audio}
              onChange={(event) => updateSetting('ffmpeg.copy_audio', event.target.checked)}
            />
          </label>
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">Blur threads</span>
            <input
              type="number"
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={config.ffmpeg?.blur_threads ?? ''}
              onChange={(event) => updateSetting('ffmpeg.blur_threads', parseInt(event.target.value, 10))}
            />
          </label>
        </div>
      )}

      {activeTab === 'GenAI' && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">API key</span>
            <input
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={config.google_genai?.api_key || ''}
              onChange={(event) => updateSetting('google_genai.api_key', event.target.value)}
            />
          </label>
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">Model</span>
            <input
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={config.google_genai?.model || ''}
              onChange={(event) => updateSetting('google_genai.model', event.target.value)}
            />
          </label>
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">Aspect ratio</span>
            <input
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={config.google_genai?.aspect_ratio || ''}
              onChange={(event) => updateSetting('google_genai.aspect_ratio', event.target.value)}
            />
          </label>
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">Image size</span>
            <input
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={config.google_genai?.image_size || ''}
              onChange={(event) => updateSetting('google_genai.image_size', event.target.value)}
            />
          </label>
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">Output dir</span>
            <input
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={config.google_genai?.output_dir || ''}
              onChange={(event) => updateSetting('google_genai.output_dir', event.target.value)}
            />
          </label>
          <label className="space-y-1 text-sm text-gray-200">
            <span className="text-xs text-gray-400">Manifest file</span>
            <input
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={config.google_genai?.manifest_file || ''}
              onChange={(event) => updateSetting('google_genai.manifest_file', event.target.value)}
            />
          </label>
        </div>
      )}

      {activeTab === 'Maintenance' && (
        <div className="space-y-3 rounded-xl border border-gray-800 bg-gray-900 p-4">
          <label className="flex items-center justify-between text-sm text-gray-200">
            <div>
              <p className="text-xs uppercase text-gray-400">Cleanup on start</p>
              <p className="text-sm text-gray-300">Автоочистка старых файлов при запуске</p>
            </div>
            <input
              type="checkbox"
              className="h-4 w-4 rounded border-gray-700 bg-gray-800 text-indigo-500"
              checked={!!config.maintenance?.auto_cleanup_on_start}
              onChange={(event) => updateSetting('maintenance.auto_cleanup_on_start', event.target.checked)}
            />
          </label>
        </div>
      )}
    </div>
  );
};

export default Settings;
