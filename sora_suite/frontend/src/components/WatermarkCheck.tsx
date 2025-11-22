import React from 'react';
import { WatermarkCheckItem } from '../types';

const demoItems: WatermarkCheckItem[] = [
  { id: 'wm-1', fileName: 'clip-001.mp4', status: 'watermark_found', previewUrl: '' },
  { id: 'wm-2', fileName: 'clip-002.mp4', status: 'clean', previewUrl: '' },
];

const WatermarkCheck: React.FC = () => {
  return (
    <div className="p-6 space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">Watermark Check</h1>
        <p className="text-sm text-gray-400">Inspect frames and detection status for each video.</p>
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {demoItems.map((item) => (
          <div key={item.id} className="rounded-xl border border-gray-800 bg-gray-900 p-4 shadow">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs uppercase text-gray-500">{item.fileName}</p>
                <p className="text-sm text-gray-300">Status: {item.status.replace('_', ' ')}</p>
              </div>
              <button className="rounded-lg border border-gray-700 px-3 py-1 text-xs text-gray-200">Open Preview</button>
            </div>
            <div className="mt-3 h-32 rounded-lg bg-gray-800" />
          </div>
        ))}
      </div>
    </div>
  );
};

export default WatermarkCheck;
