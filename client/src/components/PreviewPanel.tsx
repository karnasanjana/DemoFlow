import { useState } from 'react';
import { FlowStep, screenshotUrl } from '../types';

interface PreviewPanelProps {
  flowId: string;
  step: FlowStep;
  stepIndex: number;
  totalSteps: number;
}

const VIEWPORT_W = 1280;

export default function PreviewPanel({ flowId, step, stepIndex, totalSteps }: PreviewPanelProps) {
  const [imageHeight, setImageHeight] = useState<number | null>(null);

  // The screenshot is full-page (1280 x N). The highlight bbox is in page
  // pixel coordinates, so convert to percentages of the natural image size.
  const bbox = step.highlightBBox;
  const overlayStyle =
    bbox && imageHeight
      ? {
          left: `${(bbox.x / VIEWPORT_W) * 100}%`,
          top: `${(bbox.y / imageHeight) * 100}%`,
          width: `${(bbox.w / VIEWPORT_W) * 100}%`,
          height: `${(bbox.h / imageHeight) * 100}%`,
        }
      : null;

  return (
    <div className="flex-1 flex flex-col items-center justify-center p-6 overflow-hidden min-h-0">
      <div className="relative w-full max-w-3xl max-h-full overflow-y-auto bg-white rounded-xl shadow-lg border border-outline-variant">
        <div className="relative">
          <img
            alt={`Preview of ${step.name}`}
            className="w-full block"
            src={screenshotUrl(flowId, step)}
            onLoad={(e) => {
              const img = e.currentTarget;
              setImageHeight(Math.round((img.naturalHeight / img.naturalWidth) * VIEWPORT_W));
            }}
          />
          {overlayStyle && (
            <div
              className="absolute border-4 border-primary bg-primary/15 rounded-lg animate-pulse shadow-[0_0_20px_rgba(87,78,177,0.5)] pointer-events-none"
              style={overlayStyle}
            ></div>
          )}
        </div>
      </div>

      <div className="mt-6 w-full max-w-3xl bg-surface-container-lowest border border-outline-variant p-4 rounded-xl relative flex-shrink-0">
        <div className="absolute -top-3 left-4 bg-primary text-on-primary px-2 py-0.5 rounded text-[10px] uppercase font-bold tracking-wider">
          Narrator Script — Step {stepIndex + 1} of {totalSteps}
        </div>
        <p className="text-body-md text-on-surface leading-relaxed">
          {step.narrationScript ? `"${step.narrationScript}"` : <em className="text-on-surface-variant">No narration yet.</em>}
        </p>
        <div className="mt-2 flex items-center gap-3 text-label-sm text-on-surface-variant">
          <span className="material-symbols-outlined text-[16px]">animation</span>
          <span className="capitalize">{step.animationType}</span>
          <span>•</span>
          <span>{step.durationSeconds}s</span>
          <span>•</span>
          <span className="truncate">{step.url}</span>
        </div>
      </div>
    </div>
  );
}
