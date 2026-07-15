---
name: url-accessibility-audit
description: >-
  Audit database_url accessibility in an ID-keyed XLSX, CSV, TSV, or JSONL table. Use when the user asks to check database URLs, refresh live/dead accessibility, create a complete URL audit, or incrementally recheck only URL-risk changes using a previous audit. Produces a full audit workbook and an accessibility-only enhanced copy without changing IDs, names, URLs, or other fields.
---

# URL Accessibility Audit

## Overview

Check every unique `database_url` with the shared DbCAS HTTP checker, send only
ambiguous or changed risks to browser Agents, and deliver exactly two XLSX files:

- `<input_stem>_url_accessibility_audit.xlsx`
- `<input_stem>_accessibility_updated.xlsx`

Never modify the source file. The enhanced copy may only update an existing
accessibility column or append a plain `accessibility` column when none exists.

## Dependencies

- `spreadsheets:Spreadsheets`: mandatory for importing tables, preserving workbook
  structure/style/hyperlinks/hidden state, constructing both XLSX outputs, and
  performing formula and visual QA. Follow that Skill's complete instructions.
- `browser:control-in-app-browser`: mandatory for each queued risk record. It is
  used to open the supplied URL, inspect redirects and visible page state, and
  safely follow ordinary Continue/Proceed/disclaimer actions. Follow that Skill's
  browser-selection and setup rules before browsing.
- `Programmes/scripts/check_url_accessibility.py`: shared project checker used by
  the bundled CLI. Do not copy or replace its HTTP logic.

## Quick Start

For “检查这份表的 database_url，输出完整 audit 和 accessibility 增强副本”：

1. Use `spreadsheets:Spreadsheets` to import the source, identify one unambiguous
   worksheet and the ID/URL/accessibility columns, retain the workbook for the
   final copy, and export cell values to a temporary UTF-8 JSONL working file.
2. Run `auto-check` with `--workers 32 --timeout 120 --per-host-rps 1`.
3. Review every row in the generated review queue with at most three browser
   Agents concurrently. Prefer Terra with medium reasoning when it is actually
   available; otherwise use the current high-value browsing model with medium
   reasoning. Record the model actually used on every row.
4. Run `finalize`, then `validate`.
5. Use `spreadsheets:Spreadsheets` to build the `Summary` and `Audit` worksheets,
   apply the update manifest to a copy of the original workbook, export the two
   fixed-name XLSX files, scan for formula errors, and visually inspect every
   output sheet.

For “使用上次 audit 增量复查当前表，只让 Agent 检查发生变化的数据库”，
also export the prior workbook's `Audit` sheet to JSONL and pass it through
`--previous-audit`.

## Utility Scripts

Run all commands from the project root with `uv run` and always provide file
outputs. Working files are JSON/JSONL; XLSX import/export belongs to the
Spreadsheets dependency.

### `check-one`

```powershell
uv run .agents/skills/url-accessibility-audit/scripts/url_accessibility_audit.py check-one `
  --id 42 --url https://example.org/database `
  --output tmp/url_42.json --workers 1 --timeout 120 --per-host-rps 1
```

### `auto-check`

```powershell
uv run .agents/skills/url-accessibility-audit/scripts/url_accessibility_audit.py auto-check `
  --input tmp/current_table.jsonl `
  --output tmp/auto_audit.jsonl `
  --review-output tmp/agent_review_queue.jsonl `
  --workers 32 --timeout 120 --per-host-rps 1
```

Incremental mode:

```powershell
uv run .agents/skills/url-accessibility-audit/scripts/url_accessibility_audit.py auto-check `
  --input tmp/current_table.jsonl `
  --previous-audit tmp/previous_audit.jsonl `
  --output tmp/auto_audit.jsonl `
  --review-output tmp/agent_review_queue.jsonl `
  --workers 32 --timeout 120 --per-host-rps 1
```

Use `--sheet`, `--id-column`, `--url-column`, and `--accessibility-column` when
the user resolves an ambiguity explicitly. A worksheet name is recorded for
orchestration; worksheet selection itself is performed during spreadsheet import.

### `finalize`

```powershell
uv run .agents/skills/url-accessibility-audit/scripts/url_accessibility_audit.py finalize `
  --auto-audit tmp/auto_audit.jsonl `
  --agent-results tmp/agent_results.jsonl `
  --output tmp/final_audit.jsonl `
  --updates-output tmp/accessibility_updates.jsonl
```

The Agent result file must contain exactly one valid row for every queued ID and
no extra IDs. Follow [agent_review_prompt.md](references/agent_review_prompt.md).

### `validate`

```powershell
uv run .agents/skills/url-accessibility-audit/scripts/url_accessibility_audit.py validate `
  --input tmp/current_table.jsonl `
  --audit tmp/final_audit.jsonl `
  --updates tmp/accessibility_updates.jsonl `
  --output tmp/validation.json
```

After this data validation passes, the Spreadsheets Skill must independently
verify that the enhanced XLSX differs from the source only in accessibility,
that all HTTP(S) URLs are native clickable hyperlinks, that there are no formula
errors, and that row/sheet order, styles, filters, hyperlinks, hidden rows and
hidden columns were preserved.

## Workflow

### 1. Recognize the table

- Accept XLSX, CSV, TSV, or JSONL.
- ID candidates: `<main,t-word-id> id`, `id`, `database_id`.
- URL candidates: `<main,t-word-url> database_url`, `database_url`.
- Accessibility candidates: `<main,t-word-tag> accessibility`, `accessibility`.
- Stop on multiple candidate sheets/columns, blank IDs, or duplicate IDs.
- A missing accessibility column is valid; append it only in the enhanced copy.

### 2. Build the automatic audit

- Check each normalized URL only once and map diagnostics back to every ID.
- Clean `reachable` without TLS warning or cross-host redirect is automatically
  `live`.
- Queue `restricted`, `continue_required`, `unreachable`, `missing`, TLS warning,
  and cross-host redirect. Missing URLs remain unresolved; never invent a URL.
- Preserve every automatic diagnostic even after Agent review.

### 3. Compare with a previous audit

- Align strictly by unique ID.
- The risk fingerprint contains automatic status, normalized final URL, HTTP
  status class, TLS warning, and cross-host redirect.
- Queue changed fingerprints, changed input URLs, new IDs, IDs missing from the
  current table, and unchanged risk rows without a prior Agent conclusion.
- Reuse unchanged prior Agent conclusions. Elapsed time and error wording alone
  never trigger review.
- Keep missing-current IDs in the audit only, never in the enhanced copy.

### 4. Browser review

- Every queued row must be actually visited. Safely follow ordinary Continue,
  Proceed, Enter site, I understand, and disclaimer paths.
- Do not call 403, 429, CAPTCHA, TLS failure, or one timeout dead.
- Dead requires clear 404/410, persistent DNS failure, parked/sale domain,
  unrelated redirect, or an official closure notice.
- Never click through malware or phishing warnings; return unresolved.
- If unresolved, preserve the input accessibility; if absent, leave it blank.

### 5. Export and verify

- The audit workbook contains `Summary` and `Audit`; use the ordered fields in
  [audit_schema.md](references/audit_schema.md).
- Summary records current/prior source paths and SHA256, explicit parameters,
  automatic/comparison/review/final distributions, and actual Agent model counts.
- The enhanced copy uses the current input as its template and changes no field
  other than accessibility.
- Deliver only the two fixed-name XLSX outputs unless the user asks for working
  files.

## Rate Limiting

- Always pass 32 workers, a 120-second total URL budget, and 1 request/second per
  host unless the user explicitly changes them.
- The CLI uses a file lock plus `time.monotonic()` so concurrent processes share
  the same host limit. The gate runs before every HEAD and GET request.
- HTTP 429 is retained as `restricted`; do not aggressively retry it.

## Common Mistakes

- Do not treat “HTTP request failed” as equivalent to dead.
- Do not update `database_url`, database name, ID, or any non-accessibility cell.
- Do not omit unchanged or missing-current records from the full audit.
- Do not claim Terra was used unless the recorded browser Agent actually used it.
- Do not use the warning/interstitial page itself as evidence of a live database.

