import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { BBox, DemoFlow, FlowStep, HighlightArea } from '../types';

export type Tone = 'professional' | 'casual' | 'executive';

const VIEWPORT_W = 1280;
const VIEWPORT_H = 800;

/** Client-side mirror of the server's highlightArea → bbox heuristic. */
export function areaToBBox(area: HighlightArea): BBox | undefined {
  switch (area) {
    case 'navigation':
      return { x: 0, y: 0, w: VIEWPORT_W, h: 90 };
    case 'hero':
      return { x: 0, y: 80, w: VIEWPORT_W, h: Math.round(VIEWPORT_H * 0.6) };
    case 'cta-button':
      return { x: Math.round(VIEWPORT_W * 0.3), y: Math.round(VIEWPORT_H * 0.45), w: Math.round(VIEWPORT_W * 0.4), h: 90 };
    case 'form':
      return { x: Math.round(VIEWPORT_W * 0.25), y: Math.round(VIEWPORT_H * 0.3), w: Math.round(VIEWPORT_W * 0.5), h: Math.round(VIEWPORT_H * 0.4) };
    case 'feature-section':
    case 'pricing':
      return { x: 60, y: Math.round(VIEWPORT_H * 0.35), w: VIEWPORT_W - 120, h: Math.round(VIEWPORT_H * 0.5) };
    case 'full-page':
    default:
      return undefined;
  }
}

interface FlowContextValue {
  flow: DemoFlow | null;
  setFlow: (flow: DemoFlow | null) => void;
  selectedStepId: string | null;
  selectStep: (stepId: string) => void;
  selectedStep: FlowStep | null;
  updateStep: (stepId: string, patch: Partial<FlowStep>) => void;
  reorderSteps: (activeId: string, overId: string) => void;
  addStep: () => void;
  removeStep: (stepId: string) => void;
  tone: Tone;
  setTone: (tone: Tone) => void;
  resolution: string;
  setResolution: (res: string) => void;
  totalDuration: number;
}

const FlowContext = createContext<FlowContextValue | null>(null);

export function FlowProvider({ children }: { children: ReactNode }) {
  const [flow, setFlowState] = useState<DemoFlow | null>(null);
  const [selectedStepId, setSelectedStepId] = useState<string | null>(null);
  const [tone, setTone] = useState<Tone>('professional');
  const [resolution, setResolution] = useState('1280x800');

  const setFlow = useCallback((next: DemoFlow | null) => {
    setFlowState(next);
    setSelectedStepId(next?.steps[0]?.id ?? null);
  }, []);

  const updateStep = useCallback((stepId: string, patch: Partial<FlowStep>) => {
    setFlowState((prev) =>
      prev
        ? { ...prev, steps: prev.steps.map((s) => (s.id === stepId ? { ...s, ...patch } : s)) }
        : prev,
    );
  }, []);

  const reorderSteps = useCallback((activeId: string, overId: string) => {
    setFlowState((prev) => {
      if (!prev) return prev;
      const steps = [...prev.steps].sort((a, b) => a.order - b.order);
      const from = steps.findIndex((s) => s.id === activeId);
      const to = steps.findIndex((s) => s.id === overId);
      if (from === -1 || to === -1) return prev;
      const [moved] = steps.splice(from, 1);
      steps.splice(to, 0, moved);
      return { ...prev, steps: steps.map((s, i) => ({ ...s, order: i })) };
    });
  }, []);

  const addStep = useCallback(() => {
    setFlowState((prev) => {
      if (!prev || prev.steps.length === 0) return prev;
      const last = [...prev.steps].sort((a, b) => a.order - b.order)[prev.steps.length - 1];
      const newStep: FlowStep = {
        ...last,
        id: `step-${Date.now().toString(36)}`,
        order: prev.steps.length,
        name: 'New Step',
        narrationScript: 'Describe what this step shows.',
        audioPath: undefined,
        videoClipPath: undefined,
      };
      setSelectedStepId(newStep.id);
      return { ...prev, steps: [...prev.steps, newStep] };
    });
  }, []);

  const removeStep = useCallback((stepId: string) => {
    setFlowState((prev) => {
      if (!prev || prev.steps.length <= 1) return prev;
      const steps = prev.steps
        .filter((s) => s.id !== stepId)
        .sort((a, b) => a.order - b.order)
        .map((s, i) => ({ ...s, order: i }));
      setSelectedStepId((cur) => (cur === stepId ? steps[0].id : cur));
      return { ...prev, steps };
    });
  }, []);

  const selectedStep = useMemo(
    () => flow?.steps.find((s) => s.id === selectedStepId) ?? null,
    [flow, selectedStepId],
  );

  const totalDuration = useMemo(
    () => flow?.steps.reduce((sum, s) => sum + s.durationSeconds, 0) ?? 0,
    [flow],
  );

  const value = useMemo<FlowContextValue>(
    () => ({
      flow,
      setFlow,
      selectedStepId,
      selectStep: setSelectedStepId,
      selectedStep,
      updateStep,
      reorderSteps,
      addStep,
      removeStep,
      tone,
      setTone,
      resolution,
      setResolution,
      totalDuration,
    }),
    [flow, setFlow, selectedStepId, selectedStep, updateStep, reorderSteps, addStep, removeStep, tone, resolution, totalDuration],
  );

  return createElement(FlowContext.Provider, { value }, children);
}

export function useFlow(): FlowContextValue {
  const ctx = useContext(FlowContext);
  if (!ctx) throw new Error('useFlow must be used within a FlowProvider');
  return ctx;
}

export function formatDuration(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const s = Math.round(totalSeconds % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}
