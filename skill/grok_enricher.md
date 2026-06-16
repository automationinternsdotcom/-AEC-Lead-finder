# Grok Enricher (Codex port — @Chrome edition)

> Ported from the Claude Code subagent `grok_enricher.md`.
> **Orchestrator:** Codex CLI. **Browser:** Codex Chrome Extension, invoked via `@Chrome`.
>
> Codex's Chrome Extension is **not** a scriptable named-tool surface like
> Claude-in-Chrome. You do not call `computer.left_click` / `javascript_tool` /
> `get_page_text`. Instead you address the extension with `@Chrome` and describe the
> action in natural language; the model performs the clicking/typing/reading itself.
> The steps below are written as those instructions.
>
> **Confirmed during Stage 2:** typing into the grok.com TipTap editor via `@Chrome`
> preserves multiple spaces (the failure that forced the `execCommand` trick on Claude
> does NOT occur here). So no DOM-insert workaround is needed — plain `@Chrome` typing
> is safe.

You are performing contact enrichment. Given a company name (and optional
city/description), use the open SuperGrok session to find 1–3 decision-makers who would
have buying authority for asset-preservation and facility-services contracts at
commercial / multifamily properties, then produce clean JSON.

## Inputs (in scope from AGENTS.md Step 2d)

- `company_name`: string (e.g. `"Mark-Taylor Residential"`)
- `city`: string or null (e.g. `"Phoenix"`)
- `description`: string or null (e.g. `"multifamily property management"`)
- `owner_entity`: string or null — Maricopa Assessor's recorded owning entity (often a
  holding LLC like `"MT Phoenix Holdings LLC"` distinct from the operating company).
  When set, include in the prompt so Grok can correlate operating brand vs. legal owner.
- `article_summary`: string or null — the article's 2-sentence summary.
- `article_url`: string or null — the resolved publisher article URL (supporting context).

## Steps

### 1. Verify the session

`@Chrome on the grok.com tab, tell me: (a) does the URL contain grok.com, (b) is a user
shown logged in in the sidebar, and (c) what does the mode selector next to the chat
input read?`

Confirm all three: URL contains `grok.com`, user is logged in, mode reads **Fast** (not
Heavy/Expert/Auto).

If logged out OR mode is wrong, STOP. Return:

```json
{"error": "session_invalid", "details": "<one-sentence description of what you saw>"}
```

The parent routine (`AGENTS.md`) will handle re-auth.

### 2. Start a fresh chat

`@Chrome click "New Chat" in the grok.com sidebar` so prior queries don't contaminate
this response.

### 3. Inject the standardized prompt

`@Chrome type the following text into the grok.com chat input exactly as written,
preserving all spacing, then stop (do not submit yet):`

…followed by the filled Fast prompt template below.

> Spacing is preserved by `@Chrome` typing (confirmed in Stage 2), so no clipboard or
> JS workaround is required. Still instruct it to type "exactly as written" so the model
> doesn't paraphrase or reflow the prompt.

Note: the prompt deliberately uses the literal **"janitorial/cleaning service
contracts"** wording — that matches how these contracts are described in Grok's search
index (procurement databases, vendor directories) and produces higher-precision matches
than abstract "asset preservation" framing. The Aether brand voice is for the Pipedrive
note (operator-facing); the Grok prompt is for retrieval (search-tool-facing).

```
The goal is to identify 1-3 people who would likely have buying authority or influence for janitorial/cleaning service contracts, facilities services, property management operations, or asset-preservation decisions at {company_name}{city_phrase}{description_phrase}{owner_phrase}.

Article context: {article_summary}
Article URL: {article_url}

Prioritize: Owner, Chairman, CEO, Principal, COO, VP/Director of Facilities, Asset Manager, Investment Manager, Operations Manager, Property Manager.

For each person, return:
- Full name
- Current title
- LinkedIn URL if findable
- Professional email if findable (mark hedged/company-format guesses with a "Likely" prefix)
- Direct phone number if findable (direct dial only, NOT the main company switchboard)

Prefer contacts tied to ownership, asset management, property management, operations, or Arizona portfolio activity. Rank the best outreach contact first.

Cross-check sources such as the company website, LinkedIn, broker/property listings, chamber directories, offering memorandums, LoopNet, Zillow/property listings, press releases, and commercial real estate news.

Return a numbered list only, no preamble.
```

Where:
- `city_phrase` = ` ({city})` if city is set, else `""`
- `description_phrase` = ` — {description}` if description is set, else `""`
- `owner_phrase` = ` (note: the property's recorded owner per Maricopa County records is "{owner_entity}" — this may be a holding LLC distinct from the operating company)` if `owner_entity` is set, else `""`
- `{article_summary}` and `{article_url}` are filled verbatim. If an input is null, write
  `(none)` so the line is never blank.

### 4. Submit

`@Chrome click the submit button (the up-arrow icon next to the grok.com chat input) to
send the message.`

### 5. Wait + capture

Wait ~10 seconds (Fast mode usually responds in 6–10s).

`@Chrome check the grok.com tab — has the response finished rendering? Look for "Thought
for Xs" or completed response text, not "Agents thinking". If still generating, wait and
check again.`

Then:

`@Chrome give me the full text of the current grok.com conversation as plain text.`

### 6. Parse via the CLI tool

The captured text contains both your prompt and Grok's response. Extract just Grok's
response — everything between the last instance of `"Thought for"` and the trailing meta
(e.g. `"NN sources"` line, or follow-up suggestions like `"Verify email deliverability"`).

Pipe that text through the parser with `--all`:

```bash
echo '<grok response text>' | uv run python -m pipeline.cli.grok_parse --all
```

The CLI returns a JSON array of up to 3 Lead objects (`[]` if none). Each entry has an
`is_generic` boolean — `true` when the parsed name is a job-title placeholder (e.g.,
"Property Manager") rather than a specific named person.

### 6b. Expert-mode fallback (when Fast contact data is low-confidence)

Fast tends to return emails as `Likely first.last@domain` guesses and almost never
returns phone numbers. Escalate to Expert when:

- The array is empty `[]`, OR
- **Every entry** has `is_generic: true` (only job-title placeholders), OR
- **Any entry** has `is_high_confidence: false` (missing verified email or direct phone)

Skip the Expert retry only when every parsed entry is both `is_generic: false` AND
`is_high_confidence: true` — real named people with both verified email AND direct phone.

Expert retry steps:

1. `@Chrome click the mode selector dropdown next to the grok.com chat input, then
   select "Expert".`
2. `@Chrome click "New Chat" in the grok.com sidebar.`
3. `@Chrome type the following text into the grok.com chat input exactly as written,
   preserving all spacing, then stop (do not submit yet):` …followed by the Expert
   prompt below.
4. `@Chrome click the submit button to send the message.` Expert responses take 3-5+
   minutes — poll every 30s with `@Chrome check the grok.com tab — is the response
   finished? Look for "Thought for X min Ys" or "Thought for Xs" with a duration, not
   "Agents thinking".` Cap at 8 min.
5. `@Chrome give me the full text of the current grok.com conversation as plain text.`
   Parse via `--all` again.
6. **Critical:** `@Chrome click the mode selector dropdown and switch it back to "Fast".`
   This leaves the session in the expected state for the next article.

Build `{fast_findings_block}` from the Fast leads you just parsed — one line per contact:
`N. <name> — <title> — LinkedIn: <url|null> — Email: <email|"Likely <email>"|null> — Phone: <phone|null>`.
If Fast returned no usable candidates, set the block to the single line
`The first-pass search returned no usable candidates — search fresh.`

```
The goal is to identify 1-3 people who would likely have buying authority or influence for janitorial/cleaning service contracts, facilities services, property management operations, or asset-preservation decisions at {company_name}{city_phrase}{description_phrase}{owner_phrase}. I need verified contact info, not pattern guesses.

Article context: {article_summary}
Article URL: {article_url}

A faster first-pass search already returned the following candidates for this company. Verify, correct, and improve on them — confirm each person is still in role, replace any "Likely"/guessed emails with a publicly verified email or null, and add direct-dial phones where you can verify them:
{fast_findings_block}

Prioritize: Owner, Chairman, CEO, Principal, COO, VP/Director of Facilities, Asset Manager, Investment Manager, Operations Manager, Property Manager.

For each person, return:
- Full name (specific person, not a job title)
- Current title (verify currency via LinkedIn or company site — confirm they're still in that role at this company)
- LinkedIn URL
- Professional email — only if publicly verified from at least one of: company directory, LinkedIn contact info, RocketReach, Apollo.io, ZoomInfo, broker/property listings, chamber directories, offering memorandums, press releases. Do NOT guess emails. Do NOT infer emails only from company format. If you cannot verify the email, return null.
- Direct phone number — only if publicly verified as a direct line (NOT the main company switchboard). If you cannot verify a direct dial, return null.

Prefer contacts tied to ownership, asset management, property management, operations, or Arizona portfolio activity. Rank the best outreach contact first.

Cross-check sources such as the company website, LinkedIn, broker/property listings, chamber directories, offering memorandums, LoopNet, Zillow/property listings, press releases, and commercial real estate news. Cross-reference at least two sources per person when possible.

Return a numbered list only, no preamble.
```

### 7. Pick the best result

If Expert ran, compare the two results:

- **Take Expert** if it has *more* contacts with `is_high_confidence: true` than Fast did.
- **Otherwise take Fast** — Expert sometimes returns fewer entries or strips contacts
  when it can't verify them, and Fast's pattern-guess emails are better than nothing.

Track this decision in the response (`mode` field) so the parent can log the breakdown.

### 8. Return

Return up to 3 leads as a JSON array. Drop any `is_generic: true` entries from the final
output. Strip the `is_generic` / `is_high_confidence` flags from each saved entry —
they're internal control signals, not contact data.

```json
{"company_name": "<input company_name>", "mode": "fast" | "expert", "leads": [<Lead>, <Lead>, <Lead>]}
```

Examples:

```json
{"company_name": "Mark-Taylor Residential", "mode": "expert", "leads": [{"name": "Michael Wilson", "title": "COO", "email": "michael.wilson@mark-taylor.com", "phone": "(480) 991-9111", "linkedin_url": "https://www.linkedin.com/in/michael-wilson-2a982625a", "seniority": "c_suite", "apollo_id": "grok"}]}
```

```json
{"company_name": "Tiny Unknown LLC", "mode": "fast", "leads": []}
```

## Failure modes

| Symptom | Return |
|---|---|
| Session expired / logged out | `{"error": "session_invalid", ...}` |
| Wrong mode (Heavy/Expert) | `{"error": "session_invalid", "details": "mode is X, expected Fast"}` |
| Grok refused or returned no entries | `{"company_name": "...", "mode": "fast", "leads": []}` |
| `@Chrome` action failed / page flake on first try | Retry once. If still failing → `{"error": "ui_flake", ...}` |
| Parser returned `null` | `{"company_name": "...", "mode": "fast", "leads": []}` (correct — don't fabricate) |

## Quality bar

- **Never fabricate contact info.** If Grok says "no email findable," the email field stays null.
- **The parser is deterministic.** Don't second-guess its output.
- **One Grok query per company per mode.** No follow-up clarifications. (The Expert retry
  in 6b counts as a separate query for a different mode, not a follow-up.)
- **Fast mode is the default.** Escalate to Expert only via 6b.

## `@Chrome` reliability notes (Codex-specific)

- `@Chrome` actions are natural-language; the model chooses how to click/type/read.
  Phrase each instruction as a single concrete action with the target named
  ("the up-arrow submit button", "the mode selector dropdown") so it doesn't improvise.
- Because the model decides execution, occasional misclicks are more likely than with a
  scripted tool. The single-retry rule in the failure table matters more here than it
  did on Claude — keep it.
- The session-verify step (1) and the mode-reset step (6b.6) are the two that most
  protect the next article's run. Don't skip them even when iterating.
