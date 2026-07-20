import React, { useState } from 'react';

export default function SettingsDashboard() {
  const [emailNotifications, setEmailNotifications] = useState(true);
  const [darkMode, setDarkMode] = useState(false);
  const [defaultExport, setDefaultExport] = useState('pdf');

  const handleSave = () => {
    alert("Settings saved successfully!");
  };

  return (
    <div>
      <div style={{ color: '#2563eb', fontWeight: 700, fontSize: '13px', letterSpacing: '0.05em', marginBottom: '20px' }}>
        SETTINGS
      </div>

      <div style={{ maxWidth: '600px', background: 'white', border: '1px solid #e2e8f0', padding: '30px', borderRadius: '10px' }}>
        <h2 style={{ borderBottom: '2px solid #f1f5f9', paddingBottom: '10px', marginBottom: '20px', fontSize: '18px', color: '#1e293b' }}>
          Dashboard Settings
        </h2>

        <div style={{ marginBottom: '20px' }}>
          <label style={{ display: 'flex', alignItems: 'center', cursor: 'pointer', fontWeight: 'bold', color: '#334155' }}>
            <input
              type="checkbox"
              checked={emailNotifications}
              onChange={(e) => setEmailNotifications(e.target.checked)}
              style={{ marginRight: '10px', width: '18px', height: '18px' }}
            />
            Receive Email Notifications for completed analysis
          </label>
        </div>

        <div style={{ marginBottom: '20px' }}>
          <label style={{ display: 'flex', alignItems: 'center', cursor: 'pointer', fontWeight: 'bold', color: '#334155' }}>
            <input
              type="checkbox"
              checked={darkMode}
              onChange={(e) => setDarkMode(e.target.checked)}
              style={{ marginRight: '10px', width: '18px', height: '18px' }}
            />
            Enable Dark Mode (Beta)
          </label>
        </div>

        <div style={{ marginBottom: '30px', display: 'flex', flexDirection: 'column' }}>
          <label style={{ fontWeight: 'bold', marginBottom: '8px', color: '#334155' }}>Default Export Format</label>
          <select
            value={defaultExport}
            onChange={(e) => setDefaultExport(e.target.value)}
            style={{ padding: '10px', borderRadius: '6px', border: '1px solid #cbd5e1', fontSize: '16px' }}
          >
            <option value="pdf">PDF Document</option>
            <option value="html">Interactive HTML</option>
            <option value="csv">Raw CSV</option>
          </select>
        </div>

        <button
          onClick={handleSave}
          style={{ padding: '10px 20px', background: '#2563eb', color: 'white', border: 'none', borderRadius: '6px', cursor: 'pointer', fontSize: '16px', fontWeight: 'bold' }}
        >
          Save Preferences
        </button>
      </div>
    </div>
  );
}
