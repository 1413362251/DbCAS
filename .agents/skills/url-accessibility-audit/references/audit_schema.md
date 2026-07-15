# URL accessibility audit schema

The final audit workbook has exactly two worksheets: `Summary` and `Audit`.
Working JSONL uses the same field names. Boolean cells must be typed booleans;
HTTP status and elapsed seconds must be numeric when present; checked dates use
`YYYY-MM-DD`.

## Summary

Include these keys in a two-column key/value table:

1. `input_path`
2. `input_sha256`
3. `previous_audit_path`
4. `previous_audit_sha256`
5. `input_sheet`
6. `id_column`
7. `url_column`
8. `accessibility_column`
9. `workers`
10. `timeout_seconds`
11. `per_host_requests_per_second`
12. `input_record_count`
13. `audit_record_count`
14. automatic status counts
15. comparison status counts
16. `agent_review_count`
17. actual `agent_model` counts
18. final `live`, `dead`, and `unresolved` counts
19. `enhanced_copy_change_count`
20. `checked_date`

An absent previous audit is recorded as blank path/hash, not as a fabricated
value.

## Audit

Keep the following columns in this order:

1. `id`
2. `record_state`
3. `input_database_url`
4. `input_accessibility`
5. `auto_status`
6. `auto_accessible`
7. `auto_checked_url`
8. `auto_final_url`
9. `auto_http_status`
10. `auto_http_status_class`
11. `auto_redirect_chain`
12. `auto_cross_host_redirect`
13. `auto_elapsed_seconds`
14. `auto_error_category`
15. `auto_error_message`
16. `auto_tls_warning`
17. `auto_checked_date`
18. `risk_fingerprint`
19. `previous_auto_status`
20. `previous_auto_final_url`
21. `previous_http_status_class`
22. `previous_tls_warning`
23. `previous_cross_host_redirect`
24. `previous_agent_final_accessibility`
25. `previous_final_accessibility`
26. `comparison_status`
27. `comparison_reason`
28. `agent_review_required`
29. `agent_review_reason`
30. `agent_visit_status`
31. `agent_checked_url`
32. `agent_final_url`
33. `agent_click_path`
34. `agent_statement`
35. `agent_checked_date`
36. `agent_model`
37. `agent_final_accessibility`
38. `final_accessibility`
39. `final_decision_source`
40. `enhanced_copy_changed`

`record_state` is `current` or `missing_current`. `comparison_status` is one of
`first_run`, `unchanged`, `changed`, `new_id`, `url_changed`, or
`missing_current`.

Automatic status is one of `reachable`, `restricted`, `continue_required`,
`unreachable`, or `missing`. Final accessibility is `live`, `dead`, or
`unresolved` in the audit. An unresolved Agent result preserves the source value
in the enhanced copy; if the source had no accessibility column/value, it stays
blank.

`input_database_url`, `auto_checked_url`, `auto_final_url`, `agent_checked_url`,
and `agent_final_url` must be native clickable hyperlinks whenever they contain
valid HTTP(S) URLs. Preserve their exact display text.

