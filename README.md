# Agent-OSINT

## Agents

The scanner has four agent versions, selectable via `--agent`:

- **`react_v1`** (default) — single ReAct loop with multi-pass deepen. Fast (~3-10 min), modest cost (~$0.30-0.70 per scan). Good for quick lookups.
- **`leadqueue_v2`** — priority-queue investigation with verifier loop. Slow (~30-60 min), higher cost (~$0.30-0.50 per scan in practice). Designed for deep-dive scans where v1 returns shallow profiles.
- **`xai_multiagent_v1`** — Grok 4.20 multi-agent via xAI Responses API with xAI `web_search` / `x_search` for discovery, plus Apify Remote MCP restricted to `dev_fusion/linkedin-profile-scraper`, `apify/instagram-profile-scraper`, and `easyapi/all-in-one-rednote-xiaohongshu-scraper` for profile/social enrichment. Records xAI response usage for estimated LLM cost; Apify Actor billing remains visible in Apify.
- **`critic_react_v3`** — single ReAct loop + open-question ledger + adversarial critic. Goal-conditioned via `--preset` (coffee_career, coffee_personal, reconnect, sales_outreach, dossier, general) and free-form `--goal`. Fully general; no atom taxonomy or specialist roster. Designed to convert unused tool-call budget into recall via parallel tool emission and critic-driven re-engagement.

Example:
```
python -m osint.cli scan "Subject Name" --agent leadqueue_v2
python -m osint.cli scan "Subject Name" --agent xai_multiagent_v1
python -m osint.cli scan "Jane Doe" --agent critic_react_v3 --preset coffee_career --goal "Meeting about her transformer-inference work"
```

The v2 scan JSON includes two extra fields not present in v1:
- `findings` — every claim discovered, with evidence + confidence
- `leads_log` — every investigation lead processed, with kind/priority/depth
