import React, { useEffect, useState } from 'react';
import { TelegramConfig } from '../types';
import { loadConfig, updateConfig } from '../api/backend';

const Telegram: React.FC = () => {
  const [cfg, setCfg] = useState<TelegramConfig>({ botToken: '', chatId: '', notificationsEnabled: false, lastNotices: [] });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    loadConfig()?.then((config) => {
      setCfg({
        botToken: config?.telegram?.bot_token || '',
        chatId: config?.telegram?.chat_id || '',
        notificationsEnabled: !!config?.telegram?.enabled,
        lastNotices: [],
      });
    });
  }, []);

  const persist = async () => {
    setSaving(true);
    await updateConfig({ telegram: { bot_token: cfg.botToken, chat_id: cfg.chatId, enabled: cfg.notificationsEnabled } });
    setSaving(false);
  };

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Telegram</h1>
        <p className="text-sm text-gray-400">Configure the bot and monitor notifications.</p>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <p className="text-xs uppercase text-gray-400">Credentials</p>
          <div className="mt-3 space-y-3">
            <label className="block text-sm text-gray-200">
              <span className="text-xs text-gray-400">Bot Token</span>
              <input
                className="mt-1 w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
                value={cfg.botToken}
                onChange={(event) => setCfg((prev) => ({ ...prev, botToken: event.target.value }))}
              />
            </label>
            <label className="block text-sm text-gray-200">
              <span className="text-xs text-gray-400">Chat ID</span>
              <input
                className="mt-1 w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-gray-100 focus:border-indigo-500 focus:outline-none"
                value={cfg.chatId}
                onChange={(event) => setCfg((prev) => ({ ...prev, chatId: event.target.value }))}
              />
            </label>
            <label className="flex items-center gap-2 text-sm text-gray-200">
              <input
                type="checkbox"
                className="h-4 w-4 rounded border-gray-700 bg-gray-800 text-indigo-500"
                checked={cfg.notificationsEnabled}
                onChange={(event) => setCfg((prev) => ({ ...prev, notificationsEnabled: event.target.checked }))}
              />
              Enable notifications
            </label>
            <button
              className="w-full rounded-lg bg-gradient-to-r from-blue-500 to-indigo-500 px-4 py-2 text-sm font-medium text-white shadow"
              onClick={persist}
            >
              {saving ? 'Saving…' : 'Save Telegram Config'}
            </button>
          </div>
        </div>

        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <p className="text-xs uppercase text-gray-400">Recent Notices</p>
          <div className="mt-3 space-y-2">
            {(cfg.lastNotices || ['Автоуведомления будут отображаться здесь']).map((notice, index) => (
              <div key={index} className="rounded-lg bg-gray-800 px-3 py-2 text-sm text-gray-200">
                {notice}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};

export default Telegram;
