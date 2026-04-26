# Agent-OSINT

## Agents

The scanner has two agent versions, selectable via `--agent`:

- **`react_v1`** (default) — single ReAct loop with multi-pass deepen. Fast (~3-10 min), modest cost (~$0.30-0.70 per scan). Good for quick lookups.
- **`leadqueue_v2`** — priority-queue investigation with verifier loop. Slow (~30-60 min), higher cost (~$3-5). Designed for deep-dive scans where v1 returns shallow profiles.

Example:
```
python -m osint.cli scan "Subject Name" --agent leadqueue_v2
```

The v2 scan JSON includes two extra fields not present in v1:
- `findings` — every claim discovered, with evidence + confidence
- `leads_log` — every investigation lead processed, with kind/priority/depth
