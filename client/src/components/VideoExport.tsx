import { useEffect, useRef, useState } from 'react';
import { useFlow } from '../hooks/useFlow';
import { DemoFlow, ProgressEvent } from '../types';

const PHASE_WEIGHT: Record<string, number> = { recording: 0.2, audio: 0.5, composing: 0.85 };

export default function VideoExport() {
  const { flow, setFlow } = useFlow();
  const [rendering, setRendering] = useState(false);
  const [percent, setPercent] = useState(0);
  const [statusMessage, setStatusMessage] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const sseRef = useRef<EventSource | null>(null);

  useEffect(() => () => sseRef.current?.close(), []);

  if (!flow) return null;
  const videoReady = flow.status === 'done' && Boolean(flow.outputVideoPath);

  function openProgressStream(flowId: string) {
    sseRef.current?.close();
    const sse = new EventSource(`/api/progress/${flowId}`);
    sseRef.current = sse;
    sse.onmessage = (msg) => {
      const event = JSON.parse(msg.data) as ProgressEvent;
      setStatusMessage(event.message);
      if (event.stage in PHASE_WEIGHT && typeof event.stepIndex === 'number' && event.totalSteps) {
        setPercent(Math.round(((event.stepIndex + PHASE_WEIGHT[event.stage]) / event.totalSteps) * 80));
      } else if (event.stage === 'assembling') {
        setPercent(88);
      } else if (event.stage === 'done') {
        setPercent(100);
      }
    };
  }

  async function handleGenerateVideo() {
    if (!flow || rendering) return;
    setRendering(true);
    setError(null);
    setPercent(2);
    setStatusMessage('Preparing to record…');
    openProgressStream(flow.id);

    try {
      const genRes = await fetch('/api/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ flowId: flow.id, steps: flow.steps }),
      });
      if (genRes.status === 499) throw new Error('cancelled');
      if (!genRes.ok) {
        const body = await genRes.json().catch(() => ({}));
        throw new Error(body.error || `Clip generation failed (${genRes.status})`);
      }
      const updatedFlow = (await genRes.json()) as DemoFlow;

      const renderRes = await fetch('/api/render', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ flowId: flow.id }),
      });
      if (renderRes.status === 499) throw new Error('cancelled');
      if (!renderRes.ok) {
        const body = await renderRes.json().catch(() => ({}));
        throw new Error(body.error || `Final render failed (${renderRes.status})`);
      }
      const { outputVideoPath } = (await renderRes.json()) as { outputVideoPath: string };

      setFlow({ ...updatedFlow, status: 'done', outputVideoPath });
      setPercent(100);
      setTimeout(() => setRendering(false), 1200);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Rendering failed';
      if (message !== 'cancelled') setError(message);
      setRendering(false);
    } finally {
      sseRef.current?.close();
    }
  }

  async function handleCancel() {
    if (!flow) return;
    await fetch(`/api/cancel/${flow.id}`, { method: 'POST' }).catch(() => {});
    setRendering(false);
    sseRef.current?.close();
  }

  async function handleShare() {
    if (!flow?.outputVideoPath) return;
    // Future: upload to GCS and copy a signed URL instead
    await navigator.clipboard.writeText(flow.outputVideoPath).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <>
      <div className="flex items-center gap-3">
        {error && (
          <span className="text-label-sm text-error max-w-xs truncate" title={error}>
            {error}
          </span>
        )}
        <button
          className="px-4 py-2 bg-primary text-on-primary text-label-md rounded-lg hover:bg-primary-container transition-colors shadow-sm active:opacity-80 flex items-center gap-2 disabled:opacity-60"
          onClick={handleGenerateVideo}
          disabled={rendering}
        >
          <span className="material-symbols-outlined text-[18px]">movie</span>
          Generate Video
        </button>
        {videoReady && (
          <a
            className="px-4 py-2 border border-primary text-primary text-label-md rounded-lg hover:bg-primary/5 transition-colors flex items-center gap-2"
            href={`/api/output/${flow.id}/final-demo.mp4`}
            download="demo.mp4"
          >
            <span className="material-symbols-outlined text-[18px]">download</span>
            Download MP4
          </a>
        )}
        {videoReady && (
          <button
            className="px-4 py-2 border border-outline text-on-surface-variant text-label-md rounded-lg hover:bg-surface-container-low transition-colors flex items-center gap-2"
            onClick={handleShare}
          >
            <span className="material-symbols-outlined text-[18px]">{copied ? 'check' : 'share'}</span>
            {copied ? 'Path copied!' : 'Share'}
          </button>
        )}
      </div>

      {/* View 3 — full-screen rendering overlay */}
      {rendering && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-on-surface/20 backdrop-blur-md">
          <div className="w-full max-w-xl mx-4 bg-surface-container-lowest border border-outline-variant rounded-xl shadow-2xl overflow-hidden">
            <div className="p-10 flex flex-col items-center text-center">
              <div className="relative mb-10">
                <div className="w-24 h-24 rounded-full border-4 border-primary/20 border-t-primary animate-spin"></div>
                <div className="absolute inset-0 flex items-center justify-center">
                  <span className="material-symbols-outlined text-primary text-4xl animate-pulse">movie_edit</span>
                </div>
              </div>

              <div className="space-y-2 mb-8">
                <h2 className="text-headline-md text-on-surface">Rendering your demo</h2>
                <p className="text-body-md text-on-surface-variant min-h-[28px]">{statusMessage}</p>
              </div>

              <div className="w-full space-y-3 mb-10">
                <div className="flex justify-between items-end px-1">
                  <span className="text-label-md text-primary font-bold">Progress</span>
                  <span className="text-label-sm text-on-surface-variant">{percent}% Complete</span>
                </div>
                <div className="h-3 w-full bg-surface-container rounded-full overflow-hidden">
                  <div
                    className="h-full bg-primary rounded-full relative overflow-hidden transition-all duration-500"
                    style={{ width: `${percent}%` }}
                  >
                    <div className="absolute inset-0 progress-shimmer"></div>
                  </div>
                </div>
              </div>

              <div className="w-full flex items-center justify-between border-t border-outline-variant pt-6">
                <span className="text-body-sm text-on-surface-variant flex items-center gap-2">
                  <span className="material-symbols-outlined text-[18px] animate-spin">progress_activity</span>
                  Recording, narrating & composing…
                </span>
                <button
                  className="px-4 py-2 border border-outline text-on-surface-variant text-label-md rounded-lg hover:bg-error/5 hover:text-error hover:border-error transition-colors"
                  onClick={handleCancel}
                >
                  Cancel
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
