'use client';

import { useEffect, useRef, useState } from 'react';
import { StatusPill } from './StatusPill';
import { RecentTail, type TailItem } from './RecentTail';

type Event = {
  event: string;
  seq: number;
  ts: number;
  display_label?: string;
  arg_summary?: string;
  tool_name?: string;
  // result fields when finished:
  result_count?: number;
  result_size_bytes?: number;
  // terminal:
  s3_key?: string;
  error?: string;
};

type Props = {
  scanId: string;
  initialStatus: 'queued' | 'running' | 'completed' | 'failed';
  startedAt?: string | null;
  onTerminal?: (event: 'completed' | 'failed') => void;
};

export function ProgressStream({ scanId, initialStatus, startedAt, onTerminal }: Props) {
  const [status, setStatus] = useState<'connecting' | 'live' | 'done' | 'error'>(
    initialStatus === 'completed' || initialStatus === 'failed' ? 'done' : 'connecting',
  );
  const [active, setActive] = useState<{ displayLabel: string; argSummary: string } | undefined>();
  const [tail, setTail] = useState<TailItem[]>([]);
  const [elapsed, setElapsed] = useState(
    startedAt ? Math.max(0, (Date.now() - new Date(startedAt).getTime()) / 1000) : 0,
  );
  const seenSeq = useRef<number>(-1);
  const apiBase = process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000';

  // Tick the elapsed timer.
  useEffect(() => {
    if (status === 'done') return;
    const t = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(t);
  }, [status]);

  useEffect(() => {
    if (status === 'done') return;
    const es = new EventSource(`${apiBase}/api/stream/scans/${scanId}`, {
      withCredentials: true,
    });
    es.onopen = () => setStatus('live');
    es.onmessage = (msg) => {
      let evt: Event;
      try {
        evt = JSON.parse(msg.data);
      } catch {
        return;
      }
      if (typeof evt.seq === 'number' && evt.seq <= seenSeq.current) return;
      seenSeq.current = evt.seq ?? seenSeq.current;
      switch (evt.event) {
        case 'tool.started':
          setActive({
            displayLabel: evt.display_label ?? 'Tool',
            argSummary: evt.arg_summary ?? '',
          });
          break;
        case 'tool.finished':
          setActive(undefined);
          setTail((prev) => [
            {
              ts: elapsed,
              displayLabel: evt.display_label ?? 'Tool',
              argSummary: evt.arg_summary ?? '',
              resultSummary:
                evt.result_count !== undefined ? `${evt.result_count} results` :
                evt.result_size_bytes !== undefined
                  ? `${(evt.result_size_bytes / 1024).toFixed(1)} KB`
                  : '',
            },
            ...prev,
          ]);
          break;
        case 'scan.completed':
          setStatus('done');
          onTerminal?.('completed');
          es.close();
          break;
        case 'scan.failed':
          setStatus('done');
          onTerminal?.('failed');
          es.close();
          break;
      }
    };
    es.onerror = () => setStatus('error');
    return () => es.close();
  }, [scanId, apiBase, status]);

  if (status === 'done') return null;

  return (
    <>
      <StatusPill
        active={active}
        elapsedSec={Math.floor(elapsed)}
      />
      <RecentTail items={tail} />
    </>
  );
}
