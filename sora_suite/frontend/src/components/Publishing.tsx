import React, { useEffect, useMemo, useState } from 'react';
import { CalendarClock, Play, RefreshCcw, Save } from 'lucide-react';
import { AppConfig, TikTokProfile, YouTubeChannel } from '../types';
import { loadConfig, startTask, updateConfig } from '../api/backend';

const Publishing: React.FC = () => {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    loadConfig()?.then((cfg) => {
      setConfig(cfg || {});
      setLoading(false);
    });
  }, []);

  const youtube = useMemo(() => config?.youtube || {}, [config]);
  const tiktok = useMemo(() => config?.tiktok || {}, [config]);

  const setValue = (path: string, value: unknown) => {
    setConfig((prev) => {
      const next = { ...(prev || {}) } as any;
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

  const persistSection = async (section: 'youtube' | 'tiktok') => {
    setSaving(true);
    const patch: Partial<AppConfig> = { [section]: section === 'youtube' ? youtube : tiktok };
    await updateConfig(patch);
    setSaving(false);
  };

  const runYouTube = async () => {
    const env: Record<string, string> = {};
    const channel = youtube.channels?.find((c) => c.name === youtube.active_channel)?.name || youtube.active_channel;
    if (channel) env.YOUTUBE_CHANNEL_NAME = channel;
    if (youtube.upload_src_dir) env.YOUTUBE_SRC_DIR = youtube.upload_src_dir;
    if (youtube.archive_dir) env.YOUTUBE_ARCHIVE_DIR = youtube.archive_dir;
    if (youtube.batch_limit !== undefined) env.YOUTUBE_BATCH_LIMIT = String(youtube.batch_limit);
    if (youtube.batch_step_minutes !== undefined) env.YOUTUBE_BATCH_STEP_MINUTES = String(youtube.batch_step_minutes);
    if (youtube.last_publish_at) env.YOUTUBE_PUBLISH_AT = youtube.last_publish_at;
    if (youtube.draft_only) env.YOUTUBE_DRAFT_ONLY = '1';
    await startTask({ task: 'uploader', env });
  };

  const runTikTok = async () => {
    const env: Record<string, string> = {};
    const profile = tiktok.profiles?.find((p) => p.name === tiktok.active_profile);
    if (profile?.client_key) env.TIKTOK_CLIENT_KEY = profile.client_key;
    if (profile?.client_secret) env.TIKTOK_CLIENT_SECRET = profile.client_secret;
    if (profile?.refresh_token) env.TIKTOK_REFRESH_TOKEN = profile.refresh_token;
    if (profile?.open_id) env.TIKTOK_OPEN_ID = profile.open_id;
    if (tiktok.upload_src_dir) env.TIKTOK_SRC_DIR = tiktok.upload_src_dir;
    if (tiktok.archive_dir) env.TIKTOK_ARCHIVE_DIR = tiktok.archive_dir;
    if (tiktok.batch_limit !== undefined) env.TIKTOK_BATCH_LIMIT = String(tiktok.batch_limit);
    if (tiktok.batch_step_minutes !== undefined) env.TIKTOK_BATCH_STEP_MINUTES = String(tiktok.batch_step_minutes);
    if (tiktok.last_publish_at) env.TIKTOK_PUBLISH_AT = tiktok.last_publish_at;
    if (tiktok.draft_only) env.TIKTOK_DRAFT_ONLY = '1';
    await startTask({ task: 'tiktok', env });
  };

  if (loading || !config) {
    return (
      <div className="p-6 text-sm text-gray-300">
        <div className="flex items-center gap-2 text-gray-400"><RefreshCcw size={16} className="animate-spin" /> Загрузка конфигурации...</div>
      </div>
    );
  }

  const renderChannelOption = (channel: YouTubeChannel) => channel.name || 'channel';
  const renderProfileOption = (profile: TikTokProfile) => profile.name || 'profile';

  return (
    <div className="space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Publishing</h1>
          <p className="text-sm text-gray-400">Управление очередями YouTube и TikTok через единый интерфейс.</p>
        </div>
        <div className="flex items-center gap-2 rounded-lg bg-gray-800 px-3 py-2 text-xs text-gray-300">
          <CalendarClock size={14} />
          <span>{saving ? 'Saving…' : 'Ready'}</span>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="space-y-4 rounded-xl border border-gray-800 bg-gray-900 p-4 shadow-lg">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs uppercase text-gray-400">YouTube</p>
              <h2 className="text-lg font-semibold text-white">Upload queue</h2>
            </div>
            <button
              className="flex items-center gap-2 rounded-lg border border-gray-700 px-3 py-2 text-xs text-gray-200"
              onClick={() => persistSection('youtube')}
            >
              <Save size={14} /> Save
            </button>
          </div>

          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <label className="space-y-1 text-sm text-gray-200">
              <span className="text-xs text-gray-400">Channel</span>
              <select
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none"
                value={youtube.active_channel || ''}
                onChange={(event) => setValue('youtube.active_channel', event.target.value)}
              >
                <option value="">Select channel…</option>
                {(youtube.channels || []).map((channel) => (
                  <option key={renderChannelOption(channel)} value={channel.name}>
                    {renderChannelOption(channel)}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1 text-sm text-gray-200">
              <span className="text-xs text-gray-400">Source folder</span>
              <input
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none"
                value={youtube.upload_src_dir || ''}
                onChange={(event) => setValue('youtube.upload_src_dir', event.target.value)}
                placeholder="./merged"
              />
            </label>
            <label className="space-y-1 text-sm text-gray-200">
              <span className="text-xs text-gray-400">Archive folder</span>
              <input
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none"
                value={youtube.archive_dir || ''}
                onChange={(event) => setValue('youtube.archive_dir', event.target.value)}
                placeholder="./uploaded"
              />
            </label>
            <label className="space-y-1 text-sm text-gray-200">
              <span className="text-xs text-gray-400">Batch limit</span>
              <input
                type="number"
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none"
                value={youtube.batch_limit ?? ''}
                onChange={(event) => setValue('youtube.batch_limit', parseInt(event.target.value, 10))}
                placeholder="0 = все"
              />
            </label>
            <label className="space-y-1 text-sm text-gray-200">
              <span className="text-xs text-gray-400">Interval (min)</span>
              <input
                type="number"
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none"
                value={youtube.batch_step_minutes ?? ''}
                onChange={(event) => setValue('youtube.batch_step_minutes', parseInt(event.target.value, 10))}
              />
            </label>
            <label className="flex items-center gap-2 text-sm text-gray-200">
              <input
                type="checkbox"
                className="h-4 w-4 rounded border-gray-700 bg-gray-800 text-indigo-500"
                checked={!!youtube.draft_only}
                onChange={(event) => setValue('youtube.draft_only', event.target.checked)}
              />
              Черновики вместо публикации
            </label>
            <label className="space-y-1 text-sm text-gray-200 md:col-span-2">
              <span className="text-xs text-gray-400">Publish at (ISO)</span>
              <input
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none"
                value={youtube.last_publish_at || ''}
                onChange={(event) => setValue('youtube.last_publish_at', event.target.value)}
                placeholder="2024-06-01T10:00:00Z"
              />
            </label>
          </div>

          <button
            className="mt-2 flex items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-blue-500 to-indigo-500 px-4 py-2 text-sm font-medium text-white shadow"
            onClick={runYouTube}
          >
            <Play size={16} /> Запустить YouTube очередь
          </button>
        </div>

        <div className="space-y-4 rounded-xl border border-gray-800 bg-gray-900 p-4 shadow-lg">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs uppercase text-gray-400">TikTok</p>
              <h2 className="text-lg font-semibold text-white">Content Posting API</h2>
            </div>
            <button
              className="flex items-center gap-2 rounded-lg border border-gray-700 px-3 py-2 text-xs text-gray-200"
              onClick={() => persistSection('tiktok')}
            >
              <Save size={14} /> Save
            </button>
          </div>

          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <label className="space-y-1 text-sm text-gray-200">
              <span className="text-xs text-gray-400">Profile</span>
              <select
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none"
                value={tiktok.active_profile || ''}
                onChange={(event) => setValue('tiktok.active_profile', event.target.value)}
              >
                <option value="">Select profile…</option>
                {(tiktok.profiles || []).map((profile) => (
                  <option key={renderProfileOption(profile)} value={profile.name}>
                    {renderProfileOption(profile)}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1 text-sm text-gray-200">
              <span className="text-xs text-gray-400">Source folder</span>
              <input
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none"
                value={tiktok.upload_src_dir || ''}
                onChange={(event) => setValue('tiktok.upload_src_dir', event.target.value)}
                placeholder="./merged"
              />
            </label>
            <label className="space-y-1 text-sm text-gray-200">
              <span className="text-xs text-gray-400">Archive folder</span>
              <input
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none"
                value={tiktok.archive_dir || ''}
                onChange={(event) => setValue('tiktok.archive_dir', event.target.value)}
                placeholder="./uploaded_tiktok"
              />
            </label>
            <label className="space-y-1 text-sm text-gray-200">
              <span className="text-xs text-gray-400">Batch limit</span>
              <input
                type="number"
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none"
                value={tiktok.batch_limit ?? ''}
                onChange={(event) => setValue('tiktok.batch_limit', parseInt(event.target.value, 10))}
                placeholder="0 = все"
              />
            </label>
            <label className="space-y-1 text-sm text-gray-200">
              <span className="text-xs text-gray-400">Interval (min)</span>
              <input
                type="number"
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none"
                value={tiktok.batch_step_minutes ?? ''}
                onChange={(event) => setValue('tiktok.batch_step_minutes', parseInt(event.target.value, 10))}
              />
            </label>
            <label className="flex items-center gap-2 text-sm text-gray-200">
              <input
                type="checkbox"
                className="h-4 w-4 rounded border-gray-700 bg-gray-800 text-indigo-500"
                checked={!!tiktok.draft_only}
                onChange={(event) => setValue('tiktok.draft_only', event.target.checked)}
              />
              Черновики вместо публикации
            </label>
            <label className="space-y-1 text-sm text-gray-200 md:col-span-2">
              <span className="text-xs text-gray-400">Publish at (ISO)</span>
              <input
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none"
                value={tiktok.last_publish_at || ''}
                onChange={(event) => setValue('tiktok.last_publish_at', event.target.value)}
                placeholder="2024-06-01T10:00:00Z"
              />
            </label>
          </div>

          <button
            className="mt-2 flex items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-emerald-500 to-teal-500 px-4 py-2 text-sm font-medium text-white shadow"
            onClick={runTikTok}
          >
            <Play size={16} /> Запустить TikTok очередь
          </button>
        </div>
      </div>
    </div>
  );
};

export default Publishing;
