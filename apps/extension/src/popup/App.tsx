import React, { useState } from 'react';

export const App: React.FC = () => {
  const [status] = useState('SkillMirror Extension Ready (P0)');

  return (
    <div style={{ width: '320px', padding: '16px', fontFamily: 'system-ui, sans-serif', backgroundColor: '#0f172a', color: '#f8fafc' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
        <div style={{ width: '12px', height: '12px', borderRadius: '50%', backgroundColor: '#22c55e' }} />
        <h2 style={{ margin: 0, fontSize: '18px', fontWeight: 'bold' }}>SkillMirror</h2>
      </div>
      <p style={{ fontSize: '13px', color: '#94a3b8', margin: '0 0 16px 0' }}>
        Capture & Intelligence Engine
      </p>
      <div style={{ padding: '12px', borderRadius: '8px', backgroundColor: '#1e293b', border: '1px solid #334155' }}>
        <div style={{ fontSize: '12px', color: '#cbd5e1' }}>Status: {status}</div>
        <div style={{ fontSize: '11px', color: '#64748b', marginTop: '4px' }}>Phase: P0 Foundation</div>
      </div>
    </div>
  );
};
