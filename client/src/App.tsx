import { useEffect, useState } from 'react';
import { FlowProvider, useFlow } from './hooks/useFlow';
import URLInput from './components/URLInput';
import FlowEditor from './components/FlowEditor';
import { HealthInfo } from './types';

function AppInner() {
  const { flow } = useFlow();
  const [health, setHealth] = useState<HealthInfo | null>(null);

  useEffect(() => {
    fetch('/api/health')
      .then((r) => r.json())
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  return (
    <div className="min-h-screen flex flex-col">
      {health && !health.ffmpeg && (
        <div className="bg-error-container text-on-error-container px-6 py-3 text-body-sm flex items-center gap-3 z-[60]">
          <span className="material-symbols-outlined text-[20px]">error</span>
          <span>
            <strong>FFmpeg is not installed</strong> — video rendering will fail. Install it with{' '}
            <code className="bg-white/50 px-1.5 py-0.5 rounded font-mono">{health.ffmpegInstallHint}</code> and restart
            the server.
          </span>
        </div>
      )}
      {health && !health.geminiKey && (
        <div className="bg-tertiary-fixed text-on-surface px-6 py-3 text-body-sm flex items-center gap-3 z-[60]">
          <span className="material-symbols-outlined text-[20px]">key_off</span>
          <span>
            <strong>GEMINI_API_KEY missing</strong> — analysis will use a generic fallback flow. Copy{' '}
            <code className="bg-white/50 px-1.5 py-0.5 rounded font-mono">.env.example</code> to{' '}
            <code className="bg-white/50 px-1.5 py-0.5 rounded font-mono">.env</code> and add your key.
          </span>
        </div>
      )}
      {flow && flow.steps.length > 0 ? <FlowEditor /> : <URLInput />}
    </div>
  );
}

export default function App() {
  return (
    <FlowProvider>
      <AppInner />
    </FlowProvider>
  );
}
