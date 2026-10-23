import { useEffect, useRef, useState } from 'react';
import { useFlow } from '../hooks/useFlow';
import { DemoFlow, ProgressEvent } from '../types';

const PIPELINE_STEPS = [
  { label: 'Crawling', icon: 'travel_explore' },
  { label: 'Analyzing', icon: 'query_stats' },
  { label: 'Scripting', icon: 'description' },
  { label: 'Ready', icon: 'check_circle' },
];

const STAGE_TO_STEP: Record<string, number> = {
  crawling: 0,
  analyzing: 1,
  scripting: 2,
  ready: 3,
};

export default function URLInput() {
  const { setFlow } = useFlow();
  const [url, setUrl] = useState('');
  const [analyzing, setAnalyzing] = useState(false);
  const [currentStep, setCurrentStep] = useState(-1);
  const [statusMessage, setStatusMessage] = useState('');
  const [error, setError] = useState<string | null>(null);
  const sseRef = useRef<EventSource | null>(null);

  useEffect(() => () => sseRef.current?.close(), []);

  async function handleAnalyze() {
    if (analyzing) return;
    const trimmed = url.trim();
    if (!trimmed) {
      setError('Please enter a product URL to analyze.');
      return;
    }
    setAnalyzing(true);
    setError(null);
    setCurrentStep(0);
    setStatusMessage('Starting analysis…');

    try {
      const res = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: trimmed }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || `Analyze request failed (${res.status})`);
      }
      const { flowId } = (await res.json()) as { flowId: string };

      const sse = new EventSource(`/api/analyze/progress/${flowId}`);
      sseRef.current = sse;
      sse.onmessage = async (msg) => {
        const event = JSON.parse(msg.data) as ProgressEvent;
        if (event.stage in STAGE_TO_STEP) setCurrentStep(STAGE_TO_STEP[event.stage]);
        setStatusMessage(event.message);

        if (event.stage === 'ready') {
          sse.close();
          const flowRes = await fetch(`/api/flow/${flowId}`);
          const flow = (await flowRes.json()) as DemoFlow;
          setTimeout(() => setFlow(flow), 600);
        }
        if (event.stage === 'error') {
          sse.close();
          setError(event.message);
          setAnalyzing(false);
          setCurrentStep(-1);
        }
      };
      sse.onerror = () => {
        // EventSource auto-reconnects; only fail hard if the server is gone
        if (sse.readyState === EventSource.CLOSED) {
          setError('Lost connection to the server.');
          setAnalyzing(false);
        }
      };
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not reach the API server (port 3001).');
      setAnalyzing(false);
      setCurrentStep(-1);
    }
  }

  return (
    <div className="flex-1 flex flex-col items-center">
      <header className="flex justify-between items-center w-full px-8 h-16 sticky top-0 z-50 bg-surface border-b border-outline-variant">
        <span className="text-headline-md font-bold text-primary">DemoFlow</span>
        <span className="text-label-sm text-on-surface-variant">AI Product Demo Generator</span>
      </header>

      <main className="flex-1 w-full max-w-[1440px] flex flex-col items-center justify-center px-6 py-12">
        <div className="text-center mb-12 space-y-4 max-w-2xl">
          <h1 className="text-display-lg text-on-surface tracking-tight">
            Turn your product into a <span className="text-primary">narrative.</span>
          </h1>
          <p className="text-body-lg text-on-surface-variant">
            Paste a URL. DemoFlow crawls it, understands it with Gemini, writes the script, and renders a narrated MP4
            demo.
          </p>
        </div>

        <div className="w-full max-w-2xl glass-card p-8 md:p-10 rounded-xl shadow-sm space-y-8">
          <div className="relative">
            <div className="absolute inset-y-0 left-4 flex items-center pointer-events-none text-outline">
              <span className="material-symbols-outlined">link</span>
            </div>
            <input
              className="w-full pl-12 pr-12 py-5 bg-surface-container-lowest border border-outline-variant rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-body-lg text-on-surface placeholder:text-on-surface-variant/50"
              placeholder="Enter your product URL"
              type="url"
              value={url}
              disabled={analyzing}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleAnalyze()}
            />
            {analyzing && (
              <div className="absolute inset-y-0 right-4 flex items-center">
                <div className="loading-ring"></div>
              </div>
            )}
          </div>

          <button
            className="w-full bg-[#7F77DD] text-white text-headline-sm py-4 rounded-lg hover:shadow-lg hover:shadow-primary/20 active:scale-[0.98] transition-all flex items-center justify-center gap-3 disabled:opacity-60"
            onClick={handleAnalyze}
            disabled={analyzing}
          >
            {analyzing ? 'Analyzing…' : 'Analyze & Generate Demo'}
            <span className="material-symbols-outlined">magic_button</span>
          </button>

          {error && (
            <p className="text-body-sm text-error text-center bg-error-container/40 rounded-lg px-4 py-2">{error}</p>
          )}

          <div className="pt-4">
            <div className="flex justify-between items-start w-full relative">
              <div className="absolute top-4 left-0 w-full h-[2px] bg-surface-container-highest -z-10"></div>
              <div
                className="absolute top-4 left-0 h-[2px] bg-primary -z-10 transition-all duration-1000"
                style={{ width: currentStep < 0 ? '0%' : `${(currentStep / (PIPELINE_STEPS.length - 1)) * 100}%` }}
              ></div>
              {PIPELINE_STEPS.map((step, index) => {
                const isCompleted = index < currentStep;
                const isActive = index === currentStep;
                return (
                  <div className="flex flex-col items-center gap-3 flex-1" key={step.label}>
                    <div
                      className={`w-8 h-8 rounded-full flex items-center justify-center z-10 ${
                        isCompleted || isActive
                          ? 'bg-primary text-white shadow-sm'
                          : 'bg-surface-container-highest text-on-surface-variant'
                      } ${isActive ? 'step-pulse' : ''}`}
                    >
                      <span className="material-symbols-outlined text-[18px]">
                        {isCompleted ? 'check' : step.icon}
                      </span>
                    </div>
                    <span
                      className={`text-label-md ${
                        isActive ? 'text-primary font-bold' : isCompleted ? 'text-primary' : 'text-on-surface-variant'
                      }`}
                    >
                      {step.label}
                    </span>
                  </div>
                );
              })}
            </div>
            {analyzing && statusMessage && (
              <p className="text-label-sm text-on-surface-variant text-center mt-6 animate-pulse">{statusMessage}</p>
            )}
          </div>
        </div>

        <div className="mt-16 grid grid-cols-1 md:grid-cols-3 gap-8 w-full max-w-4xl text-center">
          <div className="space-y-2">
            <span className="material-symbols-outlined text-primary text-3xl">auto_fix_high</span>
            <h3 className="text-headline-sm text-on-surface">Auto-Discovery</h3>
            <p className="text-body-sm text-on-surface-variant">
              Playwright crawls your site and maps key pages automatically — no code integration.
            </p>
          </div>
          <div className="space-y-2">
            <span className="material-symbols-outlined text-primary text-3xl">movie_edit</span>
            <h3 className="text-headline-sm text-on-surface">AI Narration</h3>
            <p className="text-body-sm text-on-surface-variant">
              Gemini writes punchy, benefit-focused voiceover scripts; TTS narrates them.
            </p>
          </div>
          <div className="space-y-2">
            <span className="material-symbols-outlined text-primary text-3xl">share_windows</span>
            <h3 className="text-headline-sm text-on-surface">MP4 Export</h3>
            <p className="text-body-sm text-on-surface-variant">
              FFmpeg assembles narrated clips with transitions into a downloadable MP4.
            </p>
          </div>
        </div>

        <div className="fixed -bottom-24 -left-24 w-96 h-96 bg-primary/5 rounded-full blur-[100px] pointer-events-none"></div>
        <div className="fixed -top-24 -right-24 w-96 h-96 bg-primary/5 rounded-full blur-[100px] pointer-events-none"></div>
      </main>
    </div>
  );
}
