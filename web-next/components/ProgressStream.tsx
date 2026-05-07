'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
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
  const router = useRouter();
  const [status, setStatus] = useState<'connecting' | 'live' | 'done' | 'error'>(
    initialStatus === 'completed' || initialStatus === 'failed' ? 'done' : 'connecting',
  );
  const [active, setActive] = useState<{ displayLabel: string; argSummary: string } | undefined>();
  const [tail, setTail] = useState<TailItem[]>([]);
  // Initialize to 0 so SSR and first client render agree — the real
  // elapsed value uses Date.now() which differs between server render
  // time and hydration time, which would throw a hydration mismatch.
  // The effect below re-baselines against startedAt on the client.
  const [elapsed, setElapsed] = useState(0);
  const seenSeq = useRef<number>(-1);
  const elapsedRef = useRef(0);

  // Tick the elapsed timer + baseline against startedAt on mount.
  useEffect(() => {
    if (status === 'done') return;
    if (startedAt) {
      const baseline = Math.max(0, (Date.now() - new Date(startedAt).getTime()) / 1000);
      elapsedRef.current = baseline;
      setElapsed(baseline);
    }
    const t = setInterval(() => {
      setElapsed((e) => {
        const next = e + 1;
        elapsedRef.current = next;
        return next;
      });
    }, 1000);
    return () => clearInterval(t);
  }, [status, startedAt]);

  useEffect(() => {
    if (initialStatus === 'completed' || initialStatus === 'failed') return;
    const es = new EventSource(`/api/stream/scans/${scanId}`);
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
            displayLabel: evt.display_label ?? evt.tool_name ?? 'Tool',
            argSummary: evt.arg_summary ?? '',
          });
          break;
        case 'tool.finished':
          setActive(undefined);
          setTail((prev) => [
            {
              ts: elapsedRef.current,
              displayLabel: evt.display_label ?? evt.tool_name ?? 'Tool',
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
        case 'scan.pass.start':
          setActive({ displayLabel: 'Investigating', argSummary: '' });
          break;
        case 'scan.pass.synthesize':
          setActive({ displayLabel: 'Synthesizing report', argSummary: '' });
          break;
        case 'scan.pass.done':
          setActive({ displayLabel: 'Pass complete', argSummary: '' });
          break;
        case 'scan.completed':
          setStatus('done');
          onTerminal?.('completed');
          router.refresh();
          es.close();
          break;
        case 'scan.failed':
          setStatus('done');
          onTerminal?.('failed');
          router.refresh();
          es.close();
          break;
      }
    };
    es.onerror = () => setStatus('error');
    return () => es.close();
  }, [scanId, initialStatus, onTerminal, router]);

  if (status === 'done') return null;

  const displayedActive =
    active ??
    (status === 'error'
      ? { displayLabel: 'Reconnecting…', argSummary: '' }
      : status === 'connecting'
        ? { displayLabel: 'Connecting…', argSummary: '' }
        : undefined);

  return (
    <>
      <StatusPill
        active={displayedActive}
        elapsedSec={Math.floor(elapsed)}
      />
      <RecentTail items={tail} />
    </>
  );
}
