---
name: content-generation
description: Use when the user wants personalised written content produced per entity — "write me an email", "draft outreach", "generate talking points", "create a sequence", "write LinkedIn comments", multi-channel campaigns, or A/B variants. Covers the maker-checker pattern, template sourcing, cohort-to-template mapping, structured output schemas, and model selection.
---

# Content Generation

Content generation produces personalised written content that is ready to send or publish — brand-consistent, factually grounded, tonality-adherent, and verified by a second AI pass. It answers: **what do I say to this entity, in what format, at what step?**

| Aspect | Research | Content Generation | GTM Automations |
|--------|----------|--------------------|-----------------|
| Question | What do I know? | What do I say? | How do I send it? |
| Output | Events, scores | Text per entity | Sent messages, CRM records |
| AI task | Extraction, classification | Creative synthesis | Routing, state management |
| Model class | Mini to core-fast | High-quality (gpt-5+) | Mini to core-fast |

**Content generation is never the starting operation.** It always requires upstream enrichment — generating against a bare list (name + company) produces generic content. Decompose compound prompts:

| User says | Operations in order |
|-----------|-------------------------------|
| "Write personalised emails to senior finance people at pharma companies" | List Building → possibly Research → Content Generation |
| "Draft outreach for my qualified leads" | (Entities exist) → Content Generation |
| "Research these prospects and write talking points" | Research → Content Generation |
| "Create a drip campaign for my prospect queue" | Content Generation → GTM Automations (queue drip) |

If entity data is thin, add Research upstream first. Ask the user for their tonality guide, ICP, product, and case-study context when the prompt needs it and it wasn't supplied.

## Core Pattern (template-driven generation)

```
[Template Source — Google Sheets or inline]
      ↓
[Template Preparation — group/compress into AI-consumable format]
      ↓
[Context Assembly — Merge templates with enriched entity data on the cohort key]
      ↓
[Optional: Pre-Generation Checks — API lookups, flag-setting]
      ↓
[Content Generator — Ask AI, high-quality model, structured output]
      ↓
[Quality Gate — Filter: sequence is_not_empty]
      ↓
[Content Verifier — Ask AI, maker-checker pass]
      ↓
[Final Quality Gate — Filter: verified_sequence is_not_empty]
```

### Variant selection

| Content type | Variant | Key configuration |
|-------------|---------------------|-------------------|
| Multi-step email sequence | Personalised Email Sequences | Array-based structured output, 3-5 steps generated in ONE call for cross-step consistency, escalation structure (later emails reference earlier without repeating) |
| Single cold email, multiple angles | Cold Emails (Angle-Based) | Named angle instructions (e.g. Competitor Threat, Dormant Goldmine); first sentence must contain a specific data point; 40-70 word cap; no em-dashes/semicolons/colons; PATH A/B conditional logic when an angle depends on variable data |
| Company-targeted messaging | Account-Based Messaging | Company-level enrichment (news, tech, hiring); company-level cohort key; shorter sequences (1-2 steps) |
| Email + LinkedIn + Slack | Multi-Channel Campaigns | One AI call, channel-specific output fields (`email_subject`, `email_body`, `linkedin_message`, `linkedin_connection_note`); per-channel tone and character limits checked by verifier |
| LinkedIn comments | LinkedIn Comments | Multi-task prompt: person relevance (Y/N vs ICP) → post relevance (hard reject: job listings, engagement bait, new-role announcements) → score 0-100 on 4 criteria → draft only if score >= 40; grade 5-6 reading level; 20-30 words; `comment_score` must be 8+ to accept; banned opener list ("Love this", "Great post", "Spot on"); batch rules: ≤1 in 3 ends with a question, ≤1 in 5 references the product, never echo the poster's words |
| Talking points / research briefs | Talking Points | Free text or light JSON, gpt-5.2; verifier may be skipped for internal-facing content |
| A/B variants | A/B Content Variants | Parallel fields (`subject_variant_a/b`, `body_variant_a/b`); downstream routing selects via an assignment column |
| Non-English | Locale/Language Variants | `{{sequence_language}}` variable; locale-specific URL mapping (en_US vs de_DE links); verifier checks `language_locale_correct`, `signup_text_url_verified` |

### Template sourcing — external (Google Sheets) vs inline

| External (Sheets) when | Inline when |
|---------------------------|--------------------------|
| Multiple cohort variants (personas, languages, campaigns) | Single template, single variant |
| Non-technical stakeholders iterate on copy | Experimental, templates changing rapidly |
| Templates need A/B testing or versioning | Template is short (single message) |
| Complexity makes inline unwieldy | User supplies the full tonality guide/voice pillars/checklist directly |

Inline is fully valid and often the right default — a proper inline template includes role definition, voice pillars with examples, banned word/phrase lists, subject line categories with approved examples, CTA guidelines, per-step purposes, and a quality checklist. Sheet structure: one row per cohort with all steps as a JSON column; if the sheet is one-row-per-step, group-compress (Path B) before the merge.

### Cohort-to-template mapping

The merge joins entity rows to template rows on a **cohort key**: persona label from nomination ("Finance Leader" → template A), qualification score band ("High Fit" → aggressive CTA), campaign/source tag, or any matching column. **The merge is an inner join** — entities without a matching template variant are silently excluded; monitor row counts at the merge output and pre-filter null cohort keys.

### Model selection

| Stage | Model | Why |
|-------|-------|-----|
| Angle-based cold emails, LinkedIn comments with batch rules, nuanced persona voice | gpt-5.2 | Complex content, anti-AI tone rules |
| Multi-step sequences with tonality guide + checklist | gpt-5.1 / gpt-5 | Detailed guideline adherence |
| Verifier pass | gpt-5 or one tier below the generator | Needs the same nuance; never mini |
| Pre-generation flag checks | Mini or no AI | State lookup, not generation |
| Cohort classification (upstream) | gpt-4.1-mini | Lightweight categorisation |

**Never use mini models for any content-facing generation step** — tonality drift and guideline misses outweigh savings. See the node-settings skill for the exact Ask AI model values and per-row costs.

### Structured output schema

Always use structured output. Principles:
1. **Multi-entry array pattern for sequences** — `"sequence": [{stage, subject, body, ...}]`, not numbered flat keys; arrays survive step-count changes.
2. **Count verification** — top-level `sequence_steps_requested` / `sequence_steps_returned` catch truncated output.
3. **A justification field per non-negotiable constraint** — `no_em_dash_used`, `no_salutation_verified`, `case_study_verified`, `past_event_verification`. Forces the AI to prove compliance.
4. **Self-evaluation baked in** — `baseline_criteria_met`, `tonality_adherence_notes`, `email_size`, `formality_score`, `events_referred` (track cross-step repetition), `grammar_verified` (multi-language).

Verifier output adds audit fields: `what_was_missed`, `what_changes_you_made`, `tonality_parameter_evaluation` (per-parameter score). Force `what_changes_you_made` on every run and instruct "you ALWAYS have to return the updated sequence content; if no changes needed, return as-is."

### Pre-generation checks

When external system state affects generation, compute flags upstream and reference them as `{{variables}}` with conditional prompt rules. Implement as a Magic Node making the API call (Custom Code only as a last resort):

| Check | Flag | Conditional rule |
|-------|------|---------------------------|
| Lead in outreach tool? | `is_present: yes/no` | If no, omit tool-specific URLs from first email |
| Existing CRM record? | `is_crm_contact: yes/no` | If yes, reference the relationship |
| Last interaction date | `days_since_last_touch: N` | If > 90, re-engagement tone |
| Active LinkedIn? | `has_linkedin: yes/no` | If no, use company-level template |

## Prompt Construction (8 sections)

1. **Role definition** — "You are a B2B cold email strategist"
2. **Input context** — entity data via `{{column_name}}` placeholders (never hardcode row data)
3. **Task breakdown** — number tasks explicitly (`## Task 1`, `## Task 2`) for complex prompts
4. **Guidelines and frameworks** — tonality guide, voice pillars, subject line categories, CTA rules as inline markdown
5. **Constraints and exclusions** — banned word/phrase lists, explicit "DO NOT" rules
6. **Examples and benchmarks** — approved subject lines, opening styles; examples show what right looks like
7. **Quality checklist** — final self-evaluation section the AI grades itself against
8. **Output format instruction** — "Return ONLY a valid JSON object matching the provided output format"

Advanced patterns:
- **Conditional paths** — when input varies meaningfully (LinkedIn active vs inactive), define PATH A / PATH B with detection logic ("If {{linkedin_buzz}} indicates no meaningful posts, use PATH B"); single-path prompts hallucinate missing data.
- **Workflow variable injection** — `<<wf_var.uuid>>` (via `manage_variables`) for shared content identical across rows (tonality guides, case study libraries).
- **Event priority ranking** — explicit ordered preference: 1. recent person posts (last 90 days), 2. company events (funding, expansion, leadership), 3. hiring signals, 4. tech stack if relevant, 5. firmographics only if nothing else. Cap usage: "Reference no more than 2 events per email; never the same event twice across the sequence."
- **Batch-level constraints** — cross-item rules for batch prompts ("no more than 1 in 3 comments end with a question").

Anti-patterns: vague single-block prompts; scoring criteria without examples; no banned-word list; hardcoded values instead of `{{placeholders}}`.

## Maker-Checker and Reference Materials

Two AI passes are not optional for production outreach. The generator most often misses: tonality drift in later steps, hallucinated case studies/URLs, word-count violations, locale URL substitution, em-dash rules. Skip the verifier only for internal-facing content, experimental workflows, or very high volume (10,000+) low-stakes content (or verify a random sample).

Reference materials (case studies, product URLs, customer names) belong in the prompt as explicit data, never assumed: store in Google Sheets → import with Get Values in Range → compress to one JSON column (Group Data or a Magic Node) → inject as `{{reference_materials}}` → instruct "Only reference case studies shared here. Do not hallucinate." → verifier requires `case_study_verified`.

## Failure Modes (symptom → fix)

- **Generic content despite rich context** → add personalisation priority ranking; cap signals per email; drop irrelevant columns before the merge.
- **Tonality drift to corporate** ("leverage", "streamline") → banned-word list; 10+ approved examples; `formality_score` field; reinforce examples in the verifier.
- **Em dash leakage** → "Using the character — is banned... DO NOT USE IT." plus required `no_em_dash_used` field.
- **Repeated events across steps** → `events_referred` per step + "Don't mention the same event twice."
- **Past events as upcoming** → `past_event_verification` field; pass event dates as variables.
- **Word count exceeded** → `email_size` field + "Count them. Do not exceed X words."
- **Cohorts silently dropped at merge** → validate cohort keys upstream; monitor merge row counts.
- **Verifier rubber-stamps or over-corrects** → force `what_changes_you_made`; audit `what_was_missed`; tighten rubric with examples.
- **High cost at volume** → >5,000 rows with low stakes: single-pass or sample-verify.
- **Structured output failures >5%** → simplify the schema; move static guides to workflow variables or sheets.

## Build and Verify

Configure the Ask AI generator/verifier with `update_node_settings` (structured output setup and model values: see node-settings skill). Test the generator with `run_node` on 2-3 rows and read the drafts plus self-evaluation fields via `get_node_output` before scaling. Wire delivery downstream per the gtm-automations skill: sequences → outreach tool; LinkedIn messages → drip queue; CRM fields → system writes; or write to a sheet with the verifier audit trail for human review.

## Boundaries

Content generation ends when verified content exists per entity row. List assembly is **List Building**; signal gathering is **Research**; fit determination is **Qualification**; selecting who gets content is **Nomination**; sending/logging/writing is **GTM Automations**. It may be triggered by an event-driven listener and feeds the send action as its payload.
