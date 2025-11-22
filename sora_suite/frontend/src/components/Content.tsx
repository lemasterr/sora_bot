import React, { useMemo, useState } from 'react';
import { ContentFile, TitleEntry, WorkspaceProfile } from '../types';

const defaultProfiles: WorkspaceProfile[] = [
  { id: 'profile-1', name: 'Creator Alpha', port: 9222, status: 'running' },
  { id: 'profile-2', name: 'Creator Beta', port: 9333, status: 'idle' },
];

const initialContent: ContentFile[] = [
  { id: 'prompts', label: 'Prompts', value: '', path: '/prompts.txt' },
  { id: 'imagePrompts', label: 'Image Prompts', value: '', path: '/image_prompts.txt' },
];

const Content: React.FC = () => {
  const [files, setFiles] = useState<ContentFile[]>(initialContent);
  const [titles, setTitles] = useState<TitleEntry[]>([]);
  const [selectedProfile, setSelectedProfile] = useState<string>(defaultProfiles[0].id);

  const updateFile = (id: string, value: string) => {
    setFiles((prev) => prev.map((file) => (file.id === id ? { ...file, value } : file)));
  };

  const updateTitle = (value: string) => {
    setTitles((prev) => {
      const existing = prev.find((entry) => entry.profileId === selectedProfile);
      if (existing) {
        return prev.map((entry) => (entry.profileId === selectedProfile ? { ...entry, title: value } : entry));
      }
      return [...prev, { profileId: selectedProfile, title: value }];
    });
  };

  const currentTitle = useMemo(
    () => titles.find((entry) => entry.profileId === selectedProfile)?.title ?? '',
    [selectedProfile, titles],
  );

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Content Editor</h1>
          <p className="text-sm text-gray-400">Manage prompts, images, and profile-specific titles.</p>
        </div>
        <div className="flex items-center gap-2 rounded-lg bg-gray-800 px-3 py-2 text-sm text-gray-300">
          <span className="text-gray-400">Titles for profile:</span>
          <select
            className="rounded-md bg-gray-900 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none"
            value={selectedProfile}
            onChange={(event) => setSelectedProfile(event.target.value)}
          >
            {defaultProfiles.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.name}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {files.map((file) => (
          <div key={file.id} className="rounded-xl border border-gray-800 bg-gray-900 p-4 shadow-lg">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs uppercase text-gray-400">{file.path}</p>
                <h2 className="text-lg font-semibold text-white">{file.label}</h2>
              </div>
              <button className="rounded-lg border border-gray-700 px-3 py-1 text-xs text-gray-200">Save</button>
            </div>
            <textarea
              className="mt-3 h-48 w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none"
              value={file.value}
              onChange={(event) => updateFile(file.id, event.target.value)}
              placeholder={`Edit ${file.label.toLowerCase()} here...`}
            />
          </div>
        ))}

        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4 shadow-lg">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs uppercase text-gray-400">/titles.txt</p>
              <h2 className="text-lg font-semibold text-white">Titles (per profile)</h2>
              <p className="text-xs text-gray-400">Titles are stored for each Chrome profile independently.</p>
            </div>
            <button className="rounded-lg border border-gray-700 px-3 py-1 text-xs text-gray-200">Save</button>
          </div>
          <textarea
            className="mt-3 h-48 w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none"
            value={currentTitle}
            onChange={(event) => updateTitle(event.target.value)}
            placeholder="Enter titles for this profile..."
          />
        </div>
      </div>
    </div>
  );
};

export default Content;
