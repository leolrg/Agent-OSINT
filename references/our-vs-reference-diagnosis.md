# Diagnosis: Why our scans underperform the reference reports

Run on `auto-research` branch, `passes=2`, default tools minus Maigret (too slow per user) plus all three Apify tools.

| | Simon 温行健 | Allison 王嘉琪 |
|---|---|---|
| Our scan | `cf3b11cd650f...` | `64ff06dcfbb24...` |
| Duration | 208s | 299s |
| Tool calls | 15 | 21 |
| Cost | $0.46 | $0.73 |
| LLM input tokens | 131K | 235K |
| Final report | 14.4 KB / 1.8K words | ~14 KB |
| Identifiers found | 5 name variants, 2 usernames, 4 schools, 5 employers, 4 URLs | 5 name variants, 2 usernames, 4 schools, 6 employers, 2 URLs |

The reports look superficially OK — both got the basic identity right (high school, name variants, NYU enrollment) and Simon's scan even matched the reference on AMC scores, 丘成桐 award, GF Securities internship, club leadership. But on the load-bearing details that distinguish "competent profile" from "deep dossier," we miss systematically.

---

## Simon: what we got vs what reference got

### Things we matched
- 温行健 / Wen Xingjian / Simon Wen / Simon (Xingjian) Wen ✓
- 广州外国语学校 (Guangzhou Foreign Language School) ✓
- AMC10 139.5/150 Top 1%, AIME Top 0.5% ✓
- 丘成桐 (S.T. Yau) National First Prize, Economic & Financial Modeling ✓
- GF Securities IB internship, Finance Club president, Cycling Club founder ✓
- "10K → 400K RMB" early trading claim ✓
- NYU enrollment ✓ (we got Fall 2023; reference says Fall 2024 — possible discrepancy)
- LinkedIn URL: `linkedin.com/in/simon-xingjian-wen-6b29a721a` ✓
- Instagram handle: `simonwen.eth` ✓
- The Wix bio at `shihuaw414.wixsite.com/emoanti/...` ✓

### Things we missed (high-value)
| Missed | Reference source |
|---|---|
| **Twitter handle `@semona0x`** — verified, 1700+ followers, primary Web3 communication channel | tavily search + cross-reference |
| **VOMEUS** (his current company — blockchain e-cigarette / "VaPIN" on Movement Network) | rootdata.com + tweet history |
| **Movement Labs is backing his "NYU dropout journey"** (Dec 2024) | tweet from 0xpotatoSam quoting @semona0x |
| **Dropped out of NYU** in December 2024 (we said he's still enrolled) | his own tweet |
| **NYU Blockchain Club affiliation + Justin Sun / TRON ecosystem evangelism** | reply to @justinsuntron |
| **Milady Cult / Remilia community member, Charlotte Fang interaction, $CULT token** | Twitter bio + tweets |
| **Discord 21k+ active members for VOMEUS** | tweet from Dave.0xU |
| **NFT collection "Vomeus Genesis" on Base via Early Vomers Pass mint** | lootex.io |
| **Facebook Bushwick housing posts (vegan, spiritual-minded, 21yo)** | FB groups |
| **Zhihu profile under `wh1t3zzuqjw`, Xiaohongshu activity** | direct platform search |
| **VOMEUS partnership with Rena Labs for privacy-first health data monetization** | Instagram @vomeusdotfun |

### Why we missed these — root cause for each

**`@semona0x` Twitter handle**: This is the cascading failure. Without it, we miss his entire current crypto narrative. We didn't find it because:
1. **Apify Instagram returned empty** (`"Empty or private data for provided input"`) for `simonwen.eth`. The IG bio (which the reference notes contains his crypto identity hints) was never read.
2. The `apify_linkedin` output's `about`/`headline` field didn't expose a Twitter link directly — and our agent did NOT extract any "websites" / "contact info" sub-fields the actor exposes.
3. The agent's one `apify_twitter` call used `search_query="\"Simon Wen\" OR simonwen.eth..."` which returned 325 chars of nothing.
4. The Wix bio (which we extracted) DID mention "cryptos & 机器学习 & defi" — but the agent didn't pivot from that to "search Twitter directly for crypto-active accounts named Simon."

**VOMEUS / Movement / dropout**: Without the Twitter handle these are unfindable via our toolset. Not surface-able via Tavily search because they're recent crypto news primarily indexed on rootdata.com / lootex.io / tweet content.

**Chinese platform activity (Zhihu, Xiaohongshu)**: Our agent ran multiple Tavily searches scoped to those domains and got nothing useful. Tavily indexes Chinese platforms poorly. We have no direct Chinese-platform tool.

---

## Allison: what we got vs what reference got

### Things we matched
- 王嘉琪 / Allison Wang / Jiaqi Wang ✓
- Beijing 21st Century International School ✓
- NYU Stern enrollment (we said grad student; reference says undergrad starting Fall 2023) ⚠️
- Instagram handle `allison_wanggg` ✓
- Bio "beijing｜newyork @nyustern", ~2.9K followers ✓
- BPA leadership candidacy ✓
- Senior Come Talk video (学长来啦 #13) on `bj21cs.com` ✓ (we found this!)

### Things we got WRONG (entity-resolution failure)
We attached the LinkedIn profile `linkedin.com/in/jiaqi-wang-a30a76290` to her — **wrong person**. That LinkedIn shows:
- Headline: "Student at New York University"
- Education: NYU + UC Irvine
- Positions: Volunteer, Participant, Member (no real titles)
- About: empty

Reference's LinkedIn for Allison is `linkedin.com/in/allison-wang-8970bb21a`. Completely different person — also at NYU and also Chinese, but a different career trajectory. The agent found the wrong Jiaqi Wang and didn't verify against seeds (Beijing high school, Allison handle, etc.).

### Cascading misses from wrong LinkedIn
| Missed | Reference detail |
|---|---|
| **7 internships**: Wells Fargo, Envolve Group, Industrial Succession Group (ISG), Melrose Legacy Partners, Colton Alexander, MiraclePlus, Fashion Way Corp Limited | LinkedIn + RocketReach (729872313) |
| **High school CEO of Fashion Way Corp Limited (尚未傳媒, HK/CN)** — 2021-2023, while in high school | LinkedIn |
| **Sciences Po 2022 short exchange / summer program (Paris)** | LinkedIn |
| **Wharton Global Youth Program 2021 (Bryn Mawr, business statistics)** | LinkedIn |
| **NYU email `jw7449@stern.nyu.edu`** | NYU CFC official site |
| **Anti-Emo cofounder** (Gen-Z student mental-health nonprofit) | Anti-Emo official page |
| **NYU Chinese Finance Club (CFC) member**, Ryan Liu (President) and Kuan Yan (E-Board Chair) as leadership | NYU CFC site |
| **Cherry Liu (刘梓屹)** — high school classmate, BPA leadership cohort | BPA pages |
| **Mentors**: Don Kim (ISG), Taylor Garden (Melrose), Chien Wong (Envolve) | LinkedIn |
| **Geographic presence**: Boulder CO, San Juan PR, Mallorca trip via Instagram | LinkedIn + IG location tags |
| **Springer book "Who Gets Funds from China's Capital Market?" — possibly listed as contributor "Allison Wang of the Stern School"** (medium-risk hypothesis worth checking) | Springer.com |

We had `Allison_wanggg` IG (correct!) but `apify_instagram` ran with that handle and **returned `"Empty or private data"`** — same failure as Simon's. So the IG bio's link to nyustern (and any other links) wasn't surfaced.

---

## Root causes (ranked)

### 1. Apify Instagram is effectively non-functional for our use case
**Both** subjects' real-and-public IG accounts returned `"no_items: Empty or private data for provided input"`. The actor we default to (`apify/instagram-scraper`) fails on accounts that:
- Are private (allison_wanggg may be), OR
- Need authenticated access to scrape, OR
- The actor's heuristic fails for some account states

This is a **high-impact data hole** because IG bios commonly carry the cross-platform handle bridge (e.g. `@semona0x` linked from `simonwen.eth`). Without IG, we can't pivot from one identity to another.

**Fix**: try a different actor (e.g. `apify/instagram-profile-scraper` which only needs the handle, not posts). Or add a fallback — if the primary returns `no_items`, retry with a profile-only actor.

### 2. No identity-verification gate before consuming a profile
The agent fetched a LinkedIn (any LinkedIn whose name matches "Jiaqi Wang") and then treated everything in that profile as ground truth, even though the school/year/timeline didn't match the seeds (Beijing 21st Century, NYU Stern *undergrad* starting Fall 2023). Result: 100% of internships/employers in the final report are wrong.

**Fix**: prompt-level — before adopting a LinkedIn/IG/X profile, the agent must explicitly state which seed fields it cross-references and confirm matches. If 0-1 fields match, treat as wrong-person and search for another candidate.

### 3. Tavily extract dramatically underused
Simon scan: 1 extract over 2 passes. Allison scan: 2 extracts over 2 passes. Plenty of search hits per call (the queries returned multiple URLs each), the agent just didn't read them.

The system prompt's "search-and-extract pattern" mandate is being ignored in practice. The LLM treats it as a suggestion, not a quota. **Hard numerical minimums** would force compliance: "Per pass, you MUST issue at least 5 tavily_extract calls across the most-relevant URLs from your searches. Skipping extract is the most common reason scans fail to find depth."

### 4. No Twitter-handle discovery heuristics
Knowing a subject's Twitter handle when they have one is critical (especially crypto/Web3 subjects). The agent's strategy was: one apify_twitter call with `search_query=combined name + topic`. That's not enough. What works:
- Search Tavily for `"Simon Wen" twitter` or `"@semona0x"` directly
- Read IG/LinkedIn bios for URL/handle links
- Search Tavily for distinctive quoted phrases the subject is known for
- For ENS-style identities (`*.eth`), search Twitter for the ENS name as both handle and content

None of these were tried. The prompt should make these explicit when the subject is plausibly Web3/crypto active.

### 5. Pass 2 didn't pivot enough
Pass 2 ran 6 more tool calls but emitted `extracted_identifiers={}` (empty JSON tail — caught by our union-merge so no data was lost, but signals that pass 2 didn't believe it found new identifiers). The +2K-character growth in the prose was mostly rephrasing. The deepen prompt critiques the previous draft but doesn't generate dramatically new search vectors.

**Fix**: deepen prompt should say "If the previous pass found 0 results in dimension X, run ≥3 different query variations for dimension X this pass" — make the deepen-pass behavior procedural, not fully agent-discretion.

### 6. No Chinese-platform direct search
Tavily doesn't index Zhihu/Xiaohongshu/Weibo well. We have no direct tool for them. Multiple agent searches returned empty.

**Fix**: a future `xiaohongshu` / `zhihu` Apify actor would close this. Out of scope for prompt-only.

### 7. Bio link extraction missing
LinkedIn / IG / X profiles often expose "websites" / "contact info" / "external_url" fields with cross-platform links. The Apify actors may return these but we don't surface them in the agent's view. Bio-link extraction would auto-discover Twitter handles from Instagram and vice versa.

---

## Recommended fixes (priority order)

1. **[Cheap, prompt-only] Hard quotas in the system prompt.**
   - Per pass: ≥5 tavily_extract calls minimum
   - Per pass: identify ≥3 dead/thin dimensions and run ≥3 search variations on each
   - If subject's surface mentions crypto/Web3/DeFi, MUST search for `*.eth` handles and crypto-active twitter accounts
2. **[Cheap, prompt-only] Identity verification gate.**
   - Before treating a LinkedIn/IG/X profile as the subject's, list ≥3 cross-reference points (school, year, geography, name variant, photo if visible) that match the seeds. <2 matches = wrong profile, search again.
3. **[Cheap, prompt-only] Twitter handle hunting heuristics.**
   - Explicit list of strategies in the prompt — search for `"<name>" twitter`, search for ENS handles, look in IG/LinkedIn bios, search Twitter for quoted distinctive phrases.
4. **[Medium] Replace/fallback Apify Instagram actor.**
   - Current `apify/instagram-scraper` returns empty for both real public accounts we tested. Try `apify/instagram-profile-scraper` (or another community actor). Add a fallback chain.
5. **[Medium] Surface bio links from Apify outputs.**
   - When `apify_linkedin` / `apify_instagram` / `apify_twitter` return, parse the bio/about/external_url fields for URLs and expose them to the agent (either by extracting them automatically or summarizing them for the agent's next turn).
6. **[Bigger] Add Chinese-platform tools** — `apify_xiaohongshu`, `apify_weibo`, `apify_zhihu` (these exist on Apify). Out of scope for now.
7. **[Bigger] Add Etherscan/ENS lookup** for `.eth` handles to surface on-chain activity, NFT holdings, etc. Out of scope for now.

Items 1–3 are cheap one-commit prompt changes that should noticeably close gaps without any tool work. Items 4–5 are the real high-leverage code changes. Items 6–7 are v2 tool additions.

---

## After applying items 1–3 (prompt-only fixes) — measurement run

Re-ran both subjects with the sharpened prompt (commit `d1de761`).

### Simon v2 (`scans/1f650773c5ae48cb9ad1be4b917f911a`)
**Big improvement.** Agent followed the new heuristics aggressively:
- **Found `@semona0x` handle** — Tavily search snippet on turn 3 surfaced
  Simon's Zhihu profile saying *"Simon Wen. 纽约｜05｜大一xhs/twitter：Semona0x"*.
  Pre-fix the agent skipped past it; post-fix it pivoted hard.
- **Found VOMEUS** ("on-chain smoker @vomeus") via subsequent Semona0x
  searches that surfaced his X profile bio fragments through Tavily.
- **Found Milady Cult / @MiladyCult affiliation** — same chain.
- **Found Zhihu wh1t3zzuqjw** — cross-referenced to Semona0x.
- **Found `@nyuniversity` connection** in the X bio.
- **Tried direct `apify_twitter(handle="Semona0x")`** — actor returned
  `{"demo": true}` (placeholder, not real data) but agent already had
  enough evidence from Tavily snippets.
- 3 tavily_extract calls (was 1).

Net: matched roughly 70% of the reference's findings on Simon's
crypto/Web3 narrative — was 0% before. The Apify Twitter returning
demo data is a separate issue (probably actor/auth setup).

### Allison v2 (`scans/9149d540ea0646c0b4a9b2f550134fc7`)
**Mixed.** Agent followed the verification-gate pattern and tried TWO
LinkedIn candidates instead of blindly trusting the first hit
(`jiaqi-wang-a30a76290` AND `jiaqiwang98`) — but **neither** was the
right Allison. Reference's `allison-wang-8970bb21a` was never surfaced
by Tavily. Apify Instagram returned empty for `allison_wanggg` again.

The agent did surface a "2022 Alpha Scholars Silver Medal for paper
on Social Marketing & Sustainable Fashion Brands" attribution — but
the reference doesn't list this; might be a third-Allison
mis-attribution. Cannot verify without the right LinkedIn.

The 7 reference-confirmed internships (Wells Fargo, ISG, Melrose
Legacy Partners, Colton Alexander, MiraclePlus, Envolve Group, Fashion
Way Corp Limited high-school CEO) all live on the right LinkedIn we
never found. Sciences Po 2022 / Wharton Global Youth 2021 / Anti-Emo
cofounder / NYU CFC membership / RocketReach data — all unreachable
without either:
  (a) Tavily surfacing `linkedin.com/in/allison-wang-8970bb21a`, OR
  (b) An IG bio scrape that links to the right LinkedIn URL, OR
  (c) An aggregator like RocketReach being in our toolset.

Items 4–7 are needed for Allison-class subjects. Prompt fixes alone
hit a ceiling when the discoverable surface for the right profile
isn't in our index/toolset.

### Concrete prompt-fix impact summary

| Symptom | Pre-fix | Post-fix |
|---|---|---|
| Simon: Twitter handle (Semona0x) | not found | found via Tavily + cross-ref |
| Simon: VOMEUS / Movement / crypto narrative | missed entirely | ~70% covered |
| Simon: Zhihu profile (wh1t3zzuqjw) | missed | found |
| Simon: tavily_extract call count | 1 | 3 |
| Allison: LinkedIn verification | one match adopted blindly | tried 2, both wrong (right one un-indexed) |
| Allison: tavily_extract call count | 2 | 2 (still under quota — agent strategically skipped) |
| Allison: still wrong-person attributions | yes (UCI/Ogilvy/Google) | yes (3rd Allison's Alpha Scholars paper) |
| Allison: 7 real internships found | 0 | 0 (right LinkedIn not in Tavily index) |

The prompt fix unlocks discoverable depth (Simon). It does not unlock
non-discoverable depth (Allison, where the right LinkedIn isn't in
the Tavily-indexed surface). The next high-leverage move is fixing
Apify Instagram to actually return bio data — that's the cross-
platform bridge we keep losing.
