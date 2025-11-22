import React from 'react';

const Dashboard: React.FC = () => {
  return (
    <div className="p-6 space-y-6">
      <h1 className="text-2xl font-semibold">Dashboard</h1>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {["Active Profiles", "Queued Jobs", "Recent Errors"].map((label) => (
          <div key={label} className="rounded-xl border border-gray-800 bg-gray-900 p-4 shadow-lg">
            <p className="text-xs uppercase text-gray-400">{label}</p>
            <p className="mt-2 text-3xl font-semibold text-white">--</p>
          </div>
        ))}
      </div>
    </div>
  );
};

export default Dashboard;
