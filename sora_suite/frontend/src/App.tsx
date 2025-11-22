import React from 'react';
import { BrowserRouter, Route, Routes, Navigate } from 'react-router-dom';
import Dashboard from './components/Dashboard';
import Workspaces from './components/Workspaces';
import Automator from './components/Automator';
import WatermarkCheck from './components/WatermarkCheck';
import Content from './components/Content';
import Telegram from './components/Telegram';
import Settings from './components/Settings';
import Errors from './components/Errors';
import Documentation from './components/Documentation';
import History from './components/History';

const App: React.FC = () => {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-gray-950 text-gray-100">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/workspaces" element={<Workspaces />} />
          <Route path="/automator" element={<Automator />} />
          <Route path="/watermark" element={<WatermarkCheck />} />
          <Route path="/content" element={<Content />} />
          <Route path="/telegram" element={<Telegram />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/errors" element={<Errors />} />
          <Route path="/docs" element={<Documentation />} />
          <Route path="/history" element={<History />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
    </BrowserRouter>
  );
};

export default App;
