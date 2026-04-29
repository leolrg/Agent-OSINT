'use client';

import { useState } from 'react';
import type { AgentCatalog, ParamField } from '../lib/api';
import { createScan } from '../server-actions/createScan';

export function NewScanForm({ catalog }: { catalog: AgentCatalog }) {
  const [agentName, setAgentName] = useState(catalog.agents[0]?.name ?? '');
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const agent = catalog.agents.find((a) => a.name === agentName);

  const baseFields = (agent?.params ?? []).filter((p) => !p.advanced);
  const advancedFields = (agent?.params ?? []).filter((p) => p.advanced);

  return (
    <form
      className="max-w-[480px]"
      action={async (data) => {
        setSubmitting(true);
        setError(null);
        try {
          data.set('agent', agentName);
          await createScan(data);
        } catch (e) {
          setError(String(e instanceof Error ? e.message : e));
          setSubmitting(false);
        }
      }}
    >
      <div className="label-uppercase">NEW SCAN</div>
      <h1 className="text-[20px] font-extrabold heavy-rule pb-2 leading-[1.1]">
        Investigate someone.
      </h1>

      <div className="mt-3.5">
        <label className="label-uppercase block mb-1">SUBJECT</label>
        <input
          name="subject" required
          placeholder="e.g. Jane Doe, ML researcher"
          className="block w-full border-2 border-ink bg-white px-2.5 py-2 text-[14px]"
        />
      </div>

      <div className="mt-3.5">
        <label className="label-uppercase block mb-1.5">MODE</label>
        <div className="flex gap-1.5 flex-wrap">
          {catalog.agents.map((a) => (
            <button
              key={a.name} type="button"
              onClick={() => setAgentName(a.name)}
              className={
                'px-2.5 py-1.5 text-[10px] font-bold tracking-[0.1em] uppercase '
                + (a.name === agentName
                  ? 'bg-ink text-white'
                  : 'border-2 border-border text-muted')
              }
            >
              {a.display_name}
            </button>
          ))}
        </div>
        {agent?.description && (
          <p className="text-[11px] text-muted mt-1.5 leading-[1.4]">
            {agent.description} · {agent.estimated_duration}
          </p>
        )}
      </div>

      {agent && agent.params.length > 0 && (
        <div className="mt-4 p-3 bg-white border-2 border-ink">
          <div className="text-[9px] font-bold tracking-[0.12em] uppercase text-muted2 mb-2.5">
            {agent.display_name} settings
          </div>
          {baseFields.map((f) => <FieldInput key={f.name} f={f} />)}
          {advancedFields.length > 0 && (
            <details className="mt-3 border border-dashed border-dashed text-[11px]">
              <summary className="px-2 py-1.5 font-semibold text-muted2 uppercase tracking-[0.1em] cursor-pointer">
                ▸ Advanced
              </summary>
              <div className="px-2 py-2 space-y-2.5">
                {advancedFields.map((f) => <FieldInput key={f.name} f={f} />)}
              </div>
            </details>
          )}
        </div>
      )}

      <details
        className="mt-3.5 border border-dashed border-dashed"
        open={advancedOpen}
        onToggle={(e) => setAdvancedOpen((e.currentTarget as HTMLDetailsElement).open)}
      >
        <summary className="px-2 py-1.5 text-[11px] font-semibold text-muted2 uppercase tracking-[0.1em] cursor-pointer">
          ▸ Budget · limits
        </summary>
        <div className="px-2 py-2 space-y-2.5">
          {catalog.common_params.map((f) => <FieldInput key={f.name} f={f} />)}
        </div>
      </details>

      <div className="mt-4 flex items-center gap-2.5">
        <button
          type="submit" disabled={submitting}
          className="bg-ink text-white py-2 px-4 text-[10px] font-bold tracking-[0.12em] uppercase disabled:opacity-50"
        >
          {submitting ? 'Submitting…' : 'Run scan →'}
        </button>
      </div>

      {error && (
        <div className="mt-3 px-2.5 py-2 bg-amber border-l-[3px] border-danger text-[12px] text-danger">
          {error}
        </div>
      )}
    </form>
  );
}

function FieldInput({ f }: { f: ParamField }) {
  if (f.type === 'select') {
    return (
      <div>
        <label className="block text-[11px] font-semibold mb-0.5">
          {f.label}
        </label>
        <select
          name={f.name} defaultValue={String(f.default ?? '')}
          className="block w-full border-2 border-ink bg-white px-2 py-1.5 text-[12px]"
        >
          {f.options?.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
        {f.help && <p className="text-[10px] text-muted2 mt-0.5">{f.help}</p>}
      </div>
    );
  }
  if (f.type === 'int' || f.type === 'float') {
    return (
      <div>
        <label className="block text-[11px] font-semibold mb-0.5">
          {f.label}
        </label>
        <input
          name={f.name} type="number" step={f.type === 'float' ? '0.01' : '1'}
          min={f.min} max={f.max}
          defaultValue={f.default !== undefined ? String(f.default) : ''}
          className="block w-full border-2 border-ink bg-white px-2 py-1.5 text-[12px]"
        />
        {f.help && <p className="text-[10px] text-muted2 mt-0.5">{f.help}</p>}
      </div>
    );
  }
  return (
    <div>
      <label className="block text-[11px] font-semibold mb-0.5">{f.label}</label>
      <input
        name={f.name} type="text"
        defaultValue={String(f.default ?? '')}
        className="block w-full border-2 border-ink bg-white px-2 py-1.5 text-[12px]"
      />
      {f.help && <p className="text-[10px] text-muted2 mt-0.5">{f.help}</p>}
    </div>
  );
}
