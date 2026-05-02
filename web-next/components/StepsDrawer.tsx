'use client';

import { useEffect, useState } from 'react';

type Step = {
  ts: number;
  displayLabel: string;
  argSummary: string;
  fullArgs?: Record<string, unknown>;
  responsePreview?: string;
  responseS3Key?: string;
  isCritic?: boolean;
};

export function StepsDrawer({ scanId }: { scanId: string }) {
  const [open, setOpen] = useState(false);
  const [steps, setSteps] = useState<Step[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  useEffect(() => {
    if (!open || loaded) return;
    fetch(`/api/scans/${scanId}/steps`, { credentials: 'same-origin' })
      .then((r) => (r.ok ? r.json() : { steps: [] }))
      .then((d) => { setSteps(d.steps ?? []); setLoaded(true); })
      .catch(() => setLoaded(true));
  }, [open, loaded, scanId]);

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="mt-5 pt-2.5 dashed-rule w-full text-left text-[11px] font-semibold text-muted2 uppercase tracking-[0.1em]"
      >
        ▸ Show steps
      </button>
    );
  }
  return (
    <div className="mt-5 pt-2.5 dashed-rule">
      <button
        onClick={() => setOpen(false)}
        className="text-[11px] font-semibold text-muted2 uppercase tracking-[0.1em] mb-2"
      >
        ▾ Steps · {steps.length} actions
      </button>
      {!loaded && <div className="text-[11px] text-muted2">Loading…</div>}
      {steps.map((s, idx) => (
        <StepRow
          key={idx} step={s}
          expanded={expandedIdx === idx}
          onToggle={() => setExpandedIdx((c) => (c === idx ? null : idx))}
        />
      ))}
    </div>
  );
}

function StepRow({
  step, expanded, onToggle,
}: { step: Step; expanded: boolean; onToggle: () => void }) {
  if (step.isCritic) {
    return (
      <div className="bg-amber px-3 -mx-3 py-1.5 border-b border-border">
        <div className="flex items-center gap-2.5 text-[12px] text-amber2">
          <div className="font-mono text-[10px] min-w-[44px]">+{step.ts}s</div>
          <strong>CRITIC</strong> {step.argSummary}
        </div>
      </div>
    );
  }
  return (
    <div onClick={onToggle} className="cursor-pointer border-b border-border py-1.5">
      <div className="flex items-center gap-2.5 text-[12px]">
        <div className="font-mono text-[10px] text-muted2 min-w-[44px]">+{step.ts}s</div>
        <div className="flex-1">
          <strong>{step.displayLabel}</strong>{' '}
          <span className="text-muted font-mono">{step.argSummary}</span>
        </div>
        <div className="text-[10px] text-muted2">{expanded ? '▾' : '▸'}</div>
      </div>
      {expanded && (
        <div className="mt-1.5 ml-[52px] p-2 bg-ink text-muted2 font-mono text-[10px] leading-[1.5]">
          {step.fullArgs && (
            <>
              <div className="text-muted2">ARGS</div>
              <pre className="text-spotlight whitespace-pre-wrap">{JSON.stringify(step.fullArgs, null, 2)}</pre>
            </>
          )}
          {step.responsePreview && (
            <>
              <div className="text-muted2 mt-1.5">RESPONSE (TRUNCATED)</div>
              <pre className="text-white whitespace-pre-wrap">{step.responsePreview}</pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}
