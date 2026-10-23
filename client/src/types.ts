// Mirror of server/types.ts — keep in sync.

export interface FlowStep {
  id: string;
  order: number;
  name: string;
  url: string;
  screenshotPath: string;
  narrationScript: string;
  highlightSelector?: string;
  highlightBBox?: BBox;
  animationType: 'highlight' | 'click' | 'scroll' | 'fade' | 'spotlight';
  clickTarget?: { x: number; y: number };
  scrollTo?: number;
  durationSeconds: number;
  transition: 'cut' | 'crossfade' | 'scroll';
  audioPath?: string;
  videoClipPath?: string;
}

export interface DemoFlow {
  id: string;
  url: string;
  title: string;
  description: string;
  steps: FlowStep[];
  createdAt: string;
  status: 'analyzing' | 'scripting' | 'recording' | 'rendering' | 'done' | 'error';
  outputVideoPath?: string;
}

export interface BBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

export type ProgressStage =
  | 'crawling'
  | 'analyzing'
  | 'scripting'
  | 'ready'
  | 'recording'
  | 'audio'
  | 'composing'
  | 'assembling'
  | 'done'
  | 'cancelled'
  | 'error';

export interface ProgressEvent {
  stage: ProgressStage;
  message: string;
  stepIndex?: number;
  totalSteps?: number;
  percent?: number;
}

export type HighlightArea =
  | 'full-page'
  | 'hero'
  | 'navigation'
  | 'cta-button'
  | 'feature-section'
  | 'pricing'
  | 'form';

export interface HealthInfo {
  ok: boolean;
  ffmpeg: boolean;
  ffmpegInstallHint?: string;
  geminiKey: boolean;
  ttsEngine: string;
  platform: string;
}

/** Build the preview URL for a step's screenshot served by the API. */
export function screenshotUrl(flowId: string, step: FlowStep): string {
  const basename = step.screenshotPath.split(/[\\/]/).pop() ?? '';
  return `/api/output/${flowId}/screenshot/${basename}`;
}
