import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { FlowStep, screenshotUrl } from '../types';
import { formatDuration } from '../hooks/useFlow';

interface StepCardProps {
  step: FlowStep;
  index: number;
  flowId: string;
  isActive: boolean;
  onSelect: (stepId: string) => void;
}

export default function StepCard({ step, index, flowId, isActive, onSelect }: StepCardProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: step.id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.6 : 1,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      onClick={() => onSelect(step.id)}
      className={
        isActive
          ? 'flex items-center gap-3 px-4 py-3 bg-primary/10 text-primary border-l-4 border-primary cursor-pointer transition-colors'
          : 'flex items-center gap-3 px-4 py-3 text-on-surface-variant hover:bg-surface-container cursor-pointer transition-colors border-l-4 border-transparent'
      }
    >
      <span
        {...attributes}
        {...listeners}
        className="material-symbols-outlined text-[18px] text-outline cursor-grab active:cursor-grabbing flex-shrink-0"
        onClick={(e) => e.stopPropagation()}
        title="Drag to reorder"
      >
        drag_indicator
      </span>
      <div
        className={`relative w-16 h-10 bg-surface-container rounded border ${
          isActive ? 'border-primary/30' : 'border-outline-variant'
        } overflow-hidden flex-shrink-0`}
      >
        <img
          alt={`Step ${index + 1}`}
          className="w-full h-full object-cover object-top"
          src={screenshotUrl(flowId, step)}
          loading="lazy"
        />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex justify-between items-center gap-2">
          <span className={`text-label-md truncate ${isActive ? 'font-bold' : ''}`}>
            {index + 1}. {step.name}
          </span>
          <span
            className={`text-label-sm px-1.5 rounded flex-shrink-0 ${
              isActive ? 'bg-primary/20' : 'bg-surface-container-highest'
            }`}
          >
            {formatDuration(step.durationSeconds)}
          </span>
        </div>
        <span className="text-label-sm text-on-surface-variant/70 capitalize">{step.animationType}</span>
      </div>
    </div>
  );
}
