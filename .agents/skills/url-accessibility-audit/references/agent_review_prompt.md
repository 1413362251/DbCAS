# Browser Agent prompt for URL accessibility review

You are reviewing URL accessibility only. Do not judge database qualification,
scientific content, evidence quality, duplicate identity, or representative
papers. Do not modify the ID, database URL, name, or any source field.

Use the browser Skill and actually visit `input_database_url`. Inspect visible
page state and redirects. You may safely click ordinary `Continue`, `Proceed`,
`Enter site`, `I understand`, or disclaimer controls. Do not click through a
malware or phishing warning.

## Decisions

- `live`: you reached the intended database, a recognizable current database
  entry point, or a restricted/authentication page that clearly belongs to it.
- `dead`: only after clear 404/410, persistent DNS failure, a parked/for-sale
  domain, an unrelated destination, or an official closure notice.
- `unresolved`: CAPTCHA, 403/429 without usable confirmation, TLS failure,
  malware/phishing warning, a single timeout, missing URL, or otherwise
  inconclusive access.

A timeout, 403, 429, CAPTCHA, TLS warning, or Continue page alone is never enough
to mark a database dead.

Use at most three concurrent browser Agents. Prefer Terra with medium reasoning
when available; otherwise use the available high-value browsing model with
medium reasoning. Record the actual model identifier, never the intended model.

## Required JSONL fields

Return exactly one object per assigned ID:

```json
{
  "id": "42",
  "agent_visit_status": "database_opened",
  "agent_checked_url": "https://example.org/database",
  "agent_final_url": "https://example.org/database/home",
  "agent_click_path": "Continue > Enter database",
  "agent_statement": "Reached the intended searchable database after following the official Continue page.",
  "agent_checked_date": "2026-07-12",
  "agent_model": "actual-model-name",
  "agent_final_accessibility": "live"
}
```

Controlled `agent_visit_status` values:

- `database_opened`
- `continue_followed`
- `restricted`
- `confirmed_dead`
- `security_blocked`
- `missing_url`
- `unresolved`

`agent_final_accessibility` is `live`, `dead`, or `unresolved`.
`agent_statement` must be 20-500 characters. Dates use `YYYY-MM-DD`. A live
result requires HTTP(S) `agent_checked_url` and `agent_final_url`.
`security_blocked` and `missing_url` require unresolved; `confirmed_dead`
requires dead.

