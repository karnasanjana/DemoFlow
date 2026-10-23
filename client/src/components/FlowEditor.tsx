import { useMemo, useState } from 'react';
import {
  DndContext,
  DragEndEvent,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
} from '@dnd-kit/core';
import { SortableContext, verticalListSortingStrategy } from '@dnd-kit/sortable';
import { areaToBBox, formatDuration, Tone, useFlow } from '../hooks/useFlow';
import { FlowStep, HighlightArea, screenshotUrl } from '../types';
import StepCard from './StepCard';
import PreviewPanel from './PreviewPanel';
import VideoExport from './VideoExport';

const HIGHLIGHT_AREAS: HighlightArea[] = [
  'full-page',
  'hero',
  'navigation',
  'cta-button',
  'feature-section',
  'pricing',
  'form',
];

const ANIMATION_TYPES: FlowStep['animationType'][] = ['highlight', 'click', 'scroll', 'fade', 'spotlight'];
const TRANSITIONS: FlowStep['transition'][] = ['cut', 'crossfade', 'scroll'];

export default function FlowEditor() {
  const {
    flow,
    setFlow,
    selectedStep,
    selectStep,
    updateStep,
    reorderSteps,
    addStep,
    removeStep,
    tone,
    setTone,
    resolution,
    setResolution,
    totalDuration,
  } = useFlow();

  const [regenerating, setRegenerating] = useState(false);
  const [areaByStep, setAreaByStep] = useState<Record<string, HighlightArea>>({});

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));

  const steps = useMemo(() => (flow ? [...flow.steps].sort((a, b) => a.order - b.order) : []), [flow]);

  if (!flow || !selectedStep) return null;
  const selectedIndex = steps.findIndex((s) => s.id === selectedStep.id);

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (over && active.id !== over.id) {
      reorderSteps(String(active.id), String(over.id));
    }
  }

  async function handleRegenerateScript() {
    if (!flow || !selectedStep || regenerating) return;
    setRegenerating(true);
    try {
      const res = await fetch('/api/regenerate-script', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ flowId: flow.id, stepId: selectedStep.id, tone }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || 'Script regeneration failed');
      }
      const { narrationScript } = (await res.json()) as { narrationScript: string };
      updateStep(selectedStep.id, { narrationScript });
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Script regeneration failed — check the server log.');
    } finally {
      setRegenerating(false);
    }
  }

  function handleAreaChange(area: HighlightArea) {
    if (!selectedStep) return;
    setAreaByStep((prev) => ({ ...prev, [selectedStep.id]: area }));
    const bbox = areaToBBox(area);
    const patch: Partial<FlowStep> = { highlightBBox: bbox };
    if (selectedStep.animationType === 'click' && bbox) {
      patch.clickTarget = { x: Math.round(bbox.x + bbox.w / 2), y: Math.round(bbox.y + bbox.h / 2) };
    }
    if (selectedStep.animationType === 'scroll') {
      patch.scrollTo = bbox ? Math.max(0, bbox.y - 120) : 600;
    }
    updateStep(selectedStep.id, patch);
  }

  const currentArea: HighlightArea = areaByStep[selectedStep.id] ?? (selectedStep.highlightBBox ? 'hero' : 'full-page');

  return (
    <div className="bg-background text-on-surface h-screen flex flex-col overflow-hidden">
      {/* Top bar */}
      <header className="bg-surface border-b border-outline-variant flex justify-between items-center w-full px-6 h-16 flex-shrink-0 z-50">
        <div className="flex items-center gap-4 min-w-0">
          <button
            className="text-headline-md font-bold text-primary"
            onClick={() => setFlow(null)}
            title="Start over with a new URL"
          >
            DemoFlow
          </button>
          <div className="h-6 w-px bg-outline-variant"></div>
          <div className="min-w-0">
            <p className="text-label-md font-bold truncate">{flow.title}</p>
            <p className="text-label-sm text-on-surface-variant truncate">
              {flow.url} • {steps.length} steps • {formatDuration(totalDuration)}
            </p>
          </div>
        </div>
        <VideoExport />
      </header>

      <div className="flex flex-1 overflow-hidden">
        {/* Left sidebar — step list */}
        <aside className="bg-surface-container-lowest border-r border-outline-variant w-sidebar_width flex flex-col h-full flex-shrink-0">
          <div className="p-4 border-b border-outline-variant">
            <h3 className="text-headline-sm font-black text-on-surface">Steps</h3>
            <p className="text-label-sm text-on-surface-variant">Drag to reorder</p>
          </div>
          <div className="flex-1 overflow-y-auto py-2">
            <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
              <SortableContext items={steps.map((s) => s.id)} strategy={verticalListSortingStrategy}>
                {steps.map((step, index) => (
                  <StepCard
                    key={step.id}
                    step={step}
                    index={index}
                    flowId={flow.id}
                    isActive={step.id === selectedStep.id}
                    onSelect={selectStep}
                  />
                ))}
              </SortableContext>
            </DndContext>
          </div>
          <div className="p-4 border-t border-outline-variant bg-surface-container-low space-y-3">
            <button
              className="w-full py-2.5 bg-surface-container-lowest border border-primary/30 text-primary text-label-md rounded-lg flex items-center justify-center gap-2 hover:bg-primary-fixed transition-colors"
              onClick={addStep}
            >
              <span className="material-symbols-outlined">add_circle</span>
              Add Step
            </button>
            <div>
              <label className="text-label-sm text-on-surface-variant mb-1 block">Voice Tone</label>
              <select
                className="w-full bg-surface-container-lowest border border-outline-variant rounded-lg px-3 py-1.5 text-body-sm outline-none focus:ring-2 focus:ring-primary/20"
                value={tone}
                onChange={(e) => setTone(e.target.value as Tone)}
              >
                <option value="professional">Professional</option>
                <option value="casual">Casual</option>
                <option value="executive">Executive</option>
              </select>
            </div>
            <div>
              <label className="text-label-sm text-on-surface-variant mb-1 block">Output Resolution</label>
              <select
                className="w-full bg-surface-container-lowest border border-outline-variant rounded-lg px-3 py-1.5 text-body-sm outline-none focus:ring-2 focus:ring-primary/20"
                value={resolution}
                onChange={(e) => setResolution(e.target.value)}
              >
                <option value="1280x800">1280 × 800</option>
              </select>
            </div>
          </div>
        </aside>

        {/* Main canvas */}
        <main className="flex-1 flex flex-col bg-surface-container-low overflow-hidden min-w-0">
          <PreviewPanel flowId={flow.id} step={selectedStep} stepIndex={selectedIndex} totalSteps={steps.length} />

          {/* Bottom timeline */}
          <div className="h-28 bg-surface-container-lowest border-t border-outline-variant px-6 flex items-center gap-4 overflow-x-auto flex-shrink-0">
            <div className="flex-shrink-0 text-label-md font-bold text-on-surface-variant w-24">
              Total: {formatDuration(totalDuration)}
            </div>
            <div className="flex items-center gap-2">
              {steps.map((step, index) => (
                <div
                  key={step.id}
                  onClick={() => selectStep(step.id)}
                  className={`h-16 rounded-lg overflow-hidden relative cursor-pointer flex-shrink-0 ${
                    step.id === selectedStep.id
                      ? 'border-2 border-primary w-32'
                      : 'border border-outline-variant hover:border-primary transition-colors w-28'
                  }`}
                  title={step.name}
                >
                  <img
                    alt={`Step ${index + 1}`}
                    className={`w-full h-full object-cover object-top ${step.id === selectedStep.id ? '' : 'opacity-60'}`}
                    src={screenshotUrl(flow.id, step)}
                    loading="lazy"
                  />
                  <div
                    className={`absolute bottom-1 right-1 text-[10px] px-1 rounded ${
                      step.id === selectedStep.id ? 'bg-primary text-on-primary' : 'bg-on-surface/50 text-white'
                    }`}
                  >
                    {formatDuration(step.durationSeconds)}
                  </div>
                </div>
              ))}
              <div
                className="w-16 h-16 rounded-lg border-2 border-dashed border-outline-variant flex items-center justify-center text-outline hover:text-primary hover:border-primary transition-all cursor-pointer flex-shrink-0"
                onClick={addStep}
              >
                <span className="material-symbols-outlined">add</span>
              </div>
            </div>
          </div>
        </main>

        {/* Right panel — step details */}
        <aside className="w-right_panel_width bg-surface-container-lowest border-l border-outline-variant h-full overflow-y-auto p-4 flex flex-col gap-5 flex-shrink-0">
          <h4 className="text-headline-sm text-on-surface">Step Details</h4>

          <div>
            <label className="text-label-sm text-on-surface-variant mb-1 block">Step Name</label>
            <input
              className="w-full bg-surface border border-outline-variant rounded-lg px-3 py-2 text-body-sm focus:border-primary focus:ring-1 focus:ring-primary outline-none"
              type="text"
              value={selectedStep.name}
              onChange={(e) => updateStep(selectedStep.id, { name: e.target.value })}
            />
          </div>

          <div>
            <label className="text-label-sm text-on-surface-variant mb-1 block">Narration Script</label>
            <textarea
              className="w-full bg-surface border border-outline-variant rounded-lg px-3 py-2 text-body-sm focus:border-primary focus:ring-1 focus:ring-primary outline-none resize-none"
              rows={5}
              value={selectedStep.narrationScript}
              onChange={(e) => updateStep(selectedStep.id, { narrationScript: e.target.value })}
            ></textarea>
            <button
              className="w-full mt-2 py-2 bg-primary/10 text-primary text-label-md rounded-lg flex items-center justify-center gap-2 hover:bg-primary/20 transition-colors disabled:opacity-50"
              onClick={handleRegenerateScript}
              disabled={regenerating}
            >
              <span className={`material-symbols-outlined text-[18px] ${regenerating ? 'animate-spin' : ''}`}>
                {regenerating ? 'progress_activity' : 'auto_awesome'}
              </span>
              {regenerating ? 'Regenerating…' : 'Regenerate script ✨'}
            </button>
          </div>

          <div className="h-px bg-outline-variant"></div>

          <div>
            <label className="text-label-sm text-on-surface-variant mb-1 block">Animation Type</label>
            <select
              className="w-full bg-surface border border-outline-variant rounded-lg px-3 py-2 text-body-sm outline-none capitalize"
              value={selectedStep.animationType}
              onChange={(e) => updateStep(selectedStep.id, { animationType: e.target.value as FlowStep['animationType'] })}
            >
              {ANIMATION_TYPES.map((t) => (
                <option key={t} value={t} className="capitalize">
                  {t}
                </option>
              ))}
            </select>
          </div>

          <div>
            <div className="flex justify-between items-center mb-1">
              <label className="text-label-sm text-on-surface-variant">Duration</label>
              <span className="text-label-sm text-primary font-bold">{selectedStep.durationSeconds}s</span>
            </div>
            <input
              className="w-full h-1.5 bg-surface-container-highest rounded-lg appearance-none cursor-pointer accent-primary"
              type="range"
              min={1}
              max={15}
              value={selectedStep.durationSeconds}
              onChange={(e) => updateStep(selectedStep.id, { durationSeconds: Number(e.target.value) })}
            />
            <div className="flex justify-between text-[10px] text-on-surface-variant mt-1">
              <span>1s</span>
              <span>15s</span>
            </div>
          </div>

          <div>
            <label className="text-label-sm text-on-surface-variant mb-1 block">Highlight Area</label>
            <select
              className="w-full bg-surface border border-outline-variant rounded-lg px-3 py-2 text-body-sm outline-none"
              value={currentArea}
              onChange={(e) => handleAreaChange(e.target.value as HighlightArea)}
            >
              {HIGHLIGHT_AREAS.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="text-label-sm text-on-surface-variant mb-1 block">Transition Out</label>
            <select
              className="w-full bg-surface border border-outline-variant rounded-lg px-3 py-2 text-body-sm outline-none capitalize"
              value={selectedStep.transition}
              onChange={(e) => updateStep(selectedStep.id, { transition: e.target.value as FlowStep['transition'] })}
            >
              {TRANSITIONS.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>

          <div className="mt-auto pt-4 border-t border-outline-variant">
            <button
              className="w-full py-2.5 text-error text-label-md rounded-lg flex items-center justify-center gap-2 hover:bg-error/5 transition-colors disabled:opacity-40"
              onClick={() => removeStep(selectedStep.id)}
              disabled={steps.length <= 1}
            >
              <span className="material-symbols-outlined">delete</span>
              Delete Step
            </button>
          </div>
        </aside>
      </div>
    </div>
  );
}
