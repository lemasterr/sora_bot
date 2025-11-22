import React, { useEffect, useMemo, useState } from 'react';
import { ContentPayload, ContentState, WorkspaceProfile } from '../types';
import { loadContent, loadConfig, saveContent } from '../api/backend';

const Content: React.FC = () => {
  const [selectedProfile, setSelectedProfile] = useState<string>('');
  const [paths, setPaths] = useState<ContentPayload>({});
  const [profiles, setProfiles] = useState<WorkspaceProfile[]>([]);
  const [promptsByProfile, setPromptsByProfile] = useState<Record<string, string>>({});
  const [imagePromptsByProfile, setImagePromptsByProfile] = useState<Record<string, string>>({});
  const [titlesByProfile, setTitlesByProfile] = useState<Record<string, string>>({});
  const [sessionPaths, setSessionPaths] = useState<ContentPayload['sessionPaths']>({});

  useEffect(() => {
    loadContent()?.then((content: ContentState | undefined) => {
      if (!content) return;
      setPaths({
        promptsPath: content.promptsPath,
        imagePromptsPath: content.imagePromptsPath,
        titlesPath: content.titlesPath,
      });
      setSessionPaths(content.sessionPaths || {});

      const defaults: Record<string, string> = { global: content.prompts };
      setPromptsByProfile({ ...defaults, ...(content.promptsByProfile || {}) });
      setImagePromptsByProfile({ global: content.imagePrompts, ...(content.imagePromptsByProfile || {}) });
      setTitlesByProfile({ global: content.titles, ...(content.titlesByProfile || {}) });
      setSelectedProfile('global');
    });

    loadConfig()?.then((cfg) => {
      const sessions = cfg?.autogen?.sessions || [];
      const mapped = sessions.map((session) => ({
        id: session.id || session.name || session.chrome_profile || 'session',
        name: session.name || session.chrome_profile || 'Chrome Profile',
        port: session.cdp_port || 0,
        status: 'idle' as const,
      }));
      setProfiles(mapped.length > 0 ? mapped : [{ id: 'global', name: 'General', port: 0, status: 'idle' }]);
      if (mapped.length > 0) {
        setSelectedProfile(mapped[0].id);
      }
    });
  }, []);

  const currentTitle = useMemo(() => titlesByProfile[selectedProfile] ?? titlesByProfile.global ?? '', [
    selectedProfile,
    titlesByProfile,
  ]);

  const currentPrompts = useMemo(
    () => promptsByProfile[selectedProfile] ?? promptsByProfile.global ?? '',
    [promptsByProfile, selectedProfile],
  );

  const currentImagePrompts = useMemo(
    () => imagePromptsByProfile[selectedProfile] ?? imagePromptsByProfile.global ?? '',
    [imagePromptsByProfile, selectedProfile],
  );

  const updatePrompts = (value: string) => {
    setPromptsByProfile((prev) => ({ ...prev, [selectedProfile]: value }));
  };

  const updateImagePrompts = (value: string) => {
    setImagePromptsByProfile((prev) => ({ ...prev, [selectedProfile]: value }));
  };

  const updateTitle = (value: string) => {
    setTitlesByProfile((prev) => ({ ...prev, [selectedProfile]: value }));
  };

  const persist = (payload: Partial<ContentPayload>) => {
    const merged: ContentPayload = {
      ...paths,
      prompts: promptsByProfile.global,
      imagePrompts: imagePromptsByProfile.global,
      titles: titlesByProfile.global,
      titlesByProfile,
      promptsByProfile,
      imagePromptsByProfile,
      sessionPaths,
      ...payload,
    };
    void saveContent(merged);
  };

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
            {profiles.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.name}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4 shadow-lg">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs uppercase text-gray-400">
                {sessionPaths?.[selectedProfile || '']?.promptsPath || paths.promptsPath || '/prompts.txt'}
              </p>
              <h2 className="text-lg font-semibold text-white">Prompts</h2>
            </div>
            <button
              className="rounded-lg border border-gray-700 px-3 py-1 text-xs text-gray-200"
              onClick={() => persist({})}
            >
              Save
            </button>
          </div>
          <textarea
            className="mt-3 h-48 w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none"
            value={currentPrompts}
            onChange={(event) => updatePrompts(event.target.value)}
            placeholder="Edit prompts here..."
          />
        </div>

        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4 shadow-lg">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs uppercase text-gray-400">
                {sessionPaths?.[selectedProfile || '']?.imagePromptsPath || paths.imagePromptsPath || '/image_prompts.txt'}
              </p>
              <h2 className="text-lg font-semibold text-white">Image Prompts</h2>
            </div>
            <button
              className="rounded-lg border border-gray-700 px-3 py-1 text-xs text-gray-200"
              onClick={() => persist({})}
            >
              Save
            </button>
          </div>
          <textarea
            className="mt-3 h-48 w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-indigo-500 focus:outline-none"
            value={currentImagePrompts}
            onChange={(event) => updateImagePrompts(event.target.value)}
            placeholder="Edit image prompts here..."
          />
        </div>

        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4 shadow-lg">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs uppercase text-gray-400">
                {sessionPaths?.[selectedProfile || '']?.titlesPath || paths.titlesPath || '/titles.txt'}
              </p>
              <h2 className="text-lg font-semibold text-white">Titles (per profile)</h2>
              <p className="text-xs text-gray-400">Titles are stored for each Chrome profile independently.</p>
            </div>
            <button
              className="rounded-lg border border-gray-700 px-3 py-1 text-xs text-gray-200"
              onClick={() => persist({})}
            >
              Save
            </button>
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
