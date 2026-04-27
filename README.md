# Agent-OSINT

## Agents

The scanner has two agent versions, selectable via `--agent`:

- **`react_v1`** (default) — single ReAct loop with multi-pass deepen. Fast (~3-10 min), modest cost (~$0.30-0.70 per scan). Good for quick lookups.
- **`leadqueue_v2`** — priority-queue investigation with verifier loop. Slow (~30-60 min), higher cost (~$3-5). Designed for deep-dive scans where v1 returns shallow profiles.
- **`xai_multiagent_v1`** — Grok 4.20 multi-agent via xAI Responses API with xAI `web_search` / `x_search` for discovery, plus Apify Remote MCP restricted to `dev_fusion/linkedin-profile-scraper`, `apify/instagram-profile-scraper`, and `easyapi/all-in-one-rednote-xiaohongshu-scraper` for profile/social enrichment. Records xAI response usage for estimated LLM cost; Apify Actor billing remains visible in Apify.

Example:
```
python -m osint.cli scan "Subject Name" --agent leadqueue_v2
python -m osint.cli scan "Subject Name" --agent xai_multiagent_v1
```

The v2 scan JSON includes two extra fields not present in v1:
- `findings` — every claim discovered, with evidence + confidence
- `leads_log` — every investigation lead processed, with kind/priority/depth
