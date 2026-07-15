# /// script
# requires-python = ">=3.11"
# dependencies = ["requests>=2.31"]
# ///
"""Data engine for the url-accessibility-audit project skill.

The script deliberately emits JSON/JSONL working files.  The Spreadsheets
skill imports and exports XLSX so the source workbook's formatting and hidden
state can be preserved without duplicating spreadsheet-authoring logic here.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Programmes.scripts.check_url_accessibility import (  # noqa: E402
    check_urls,
    normalize_url_key,
)


AUTO_STATUSES = {
    "reachable",
    "restricted",
    "continue_required",
    "unreachable",
    "missing",
}
AGENT_VISIT_STATUSES = {
    "database_opened",
    "continue_followed",
    "restricted",
    "confirmed_dead",
    "security_blocked",
    "missing_url",
    "unresolved",
}
FINAL_ACCESSIBILITY = {"live", "dead", "unresolved"}
URL_RE = re.compile(r"^https?://\S+$", flags=re.IGNORECASE)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

AUDIT_FIELDS = (
    "id",
    "record_state",
    "input_database_url",
    "input_accessibility",
    "auto_status",
    "auto_accessible",
    "auto_checked_url",
    "auto_final_url",
    "auto_http_status",
    "auto_http_status_class",
    "auto_redirect_chain",
    "auto_cross_host_redirect",
    "auto_elapsed_seconds",
    "auto_error_category",
    "auto_error_message",
    "auto_tls_warning",
    "auto_checked_date",
    "risk_fingerprint",
    "previous_auto_status",
    "previous_auto_final_url",
    "previous_http_status_class",
    "previous_tls_warning",
    "previous_cross_host_redirect",
    "previous_agent_final_accessibility",
    "previous_final_accessibility",
    "comparison_status",
    "comparison_reason",
    "agent_review_required",
    "agent_review_reason",
    "agent_visit_status",
    "agent_checked_url",
    "agent_final_url",
    "agent_click_path",
    "agent_statement",
    "agent_checked_date",
    "agent_model",
    "agent_final_accessibility",
    "final_accessibility",
    "final_decision_source",
    "enhanced_copy_changed",
)


class AuditError(ValueError):
    """Raised for deterministic input or schema failures."""


def clean(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def truthy(value: object) -> bool:
    return clean(value).lower() in {"1", "true", "yes", "y"}


def canonical_host(url: object) -> str:
    text = clean(url)
    if not text:
        return ""
    candidate = text if "://" in text else f"https://{text}"
    try:
        host = (urlsplit(candidate).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def http_status_class(status: object) -> str:
    text = clean(status)
    if not text:
        return "none"
    try:
        code = int(float(text))
    except ValueError:
        return "none"
    return f"{code // 100}xx"


def risk_fingerprint(row: dict) -> str:
    payload = {
        "auto_status": clean(row.get("auto_status")),
        "normalized_final_url": normalize_url_key(row.get("auto_final_url")),
        "http_status_class": clean(row.get("auto_http_status_class")),
        "tls_warning": truthy(row.get("auto_tls_warning")),
        "cross_host_redirect": truthy(row.get("auto_cross_host_redirect")),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _header_token(header: str) -> str:
    return clean(header).lower()


def _header_matches(header: str, logical_name: str) -> bool:
    token = _header_token(header)
    if logical_name == "id":
        return token in {"id", "database_id", "<main,t-word-id> id"}
    if logical_name == "database_url":
        return token in {"database_url", "<main,t-word-url> database_url"}
    if logical_name == "accessibility":
        return token in {"accessibility", "<main,t-word-tag> accessibility"}
    return False


def detect_column(headers: Iterable[str], logical_name: str, override: str = "") -> str:
    fields = [clean(item) for item in headers]
    if override:
        if override not in fields:
            raise AuditError(f"Column override not found: {override}")
        return override
    matches = [field for field in fields if _header_matches(field, logical_name)]
    if len(matches) > 1:
        raise AuditError(
            f"Ambiguous {logical_name} columns: {', '.join(matches)}; use an explicit override"
        )
    if not matches:
        if logical_name == "accessibility":
            return ""
        raise AuditError(f"Required {logical_name} column not found")
    return matches[0]


def read_records(path: Path) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix == ".xlsx" or suffix == ".xls":
        raise AuditError(
            "XLSX/XLS must first be imported by spreadsheets:Spreadsheets and exported "
            "to a UTF-8 JSONL working file"
        )
    if suffix in {".json", ".jsonl"}:
        text = path.read_text(encoding="utf-8-sig")
        if suffix == ".json":
            value = json.loads(text)
            if isinstance(value, dict) and "rows" in value:
                value = value["rows"]
            if not isinstance(value, list):
                raise AuditError("JSON input must be a list or an object containing rows")
            rows = value
        else:
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        if not all(isinstance(row, dict) for row in rows):
            raise AuditError("Every input record must be a JSON object")
        return [dict(row) for row in rows]
    if suffix not in {".csv", ".tsv"}:
        raise AuditError(f"Unsupported working-file format: {suffix or '<none>'}")
    delimiter = "\t" if suffix == ".tsv" else ","
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter=delimiter)]


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    output = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    path.write_text(output + ("\n" if output else ""), encoding="utf-8")


def prepare_input_rows(
    path: Path,
    id_column: str = "",
    url_column: str = "",
    accessibility_column: str = "",
) -> tuple[list[dict], dict[str, str]]:
    records = read_records(path)
    if not records:
        raise AuditError("Input table has no records")
    headers = list(records[0])
    if any(list(row) != headers for row in records):
        all_headers = list(dict.fromkeys(key for row in records for key in row))
        headers = all_headers
    mapping = {
        "id": detect_column(headers, "id", id_column),
        "database_url": detect_column(headers, "database_url", url_column),
        "accessibility": detect_column(
            headers, "accessibility", accessibility_column
        ),
    }
    prepared = []
    seen = set()
    for index, row in enumerate(records, start=2):
        row_id = clean(row.get(mapping["id"]))
        if not row_id:
            raise AuditError(f"Blank ID at data row {index}")
        if row_id in seen:
            raise AuditError(f"Duplicate ID: {row_id}")
        seen.add(row_id)
        prepared.append(
            {
                "id": row_id,
                "input_database_url": clean(row.get(mapping["database_url"])),
                "input_accessibility": (
                    clean(row.get(mapping["accessibility"]))
                    if mapping["accessibility"]
                    else ""
                ),
            }
        )
    return prepared, mapping


@contextmanager
def locked_state_file(path: Path):
    """Lock the first byte of a state file across threads and processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0\n0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield handle
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield handle
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


class HostFileRateLimiter:
    """Cross-process per-host rate limiter backed by monotonic timestamps."""

    def __init__(self, requests_per_second: float, state_dir: Path | None = None):
        if requests_per_second <= 0:
            raise AuditError("per-host-rps must be greater than zero")
        self.minimum_interval = 1.0 / requests_per_second
        self.state_dir = state_dir or (
            Path(tempfile.gettempdir()) / "dbcas-url-accessibility-rate-limit"
        )

    def _path_for(self, url: str) -> Path:
        host = canonical_host(url) or "invalid-host"
        digest = hashlib.sha256(host.encode("utf-8")).hexdigest()[:24]
        return self.state_dir / f"{digest}.lock"

    def __call__(self, url: str) -> None:
        path = self._path_for(url)
        with locked_state_file(path) as handle:
            handle.seek(0)
            parts = handle.read().decode("ascii", errors="ignore").splitlines()
            try:
                last_request = float(parts[1]) if len(parts) > 1 else 0.0
            except ValueError:
                last_request = 0.0
            now = time.monotonic()
            wait = self.minimum_interval - (now - last_request)
            if wait > 0:
                time.sleep(wait)
            current = time.monotonic()
            handle.seek(0)
            handle.truncate()
            handle.write(f"0\n{current:.9f}".encode("ascii"))
            handle.flush()
            os.fsync(handle.fileno())


def is_risk(row: dict) -> bool:
    return (
        clean(row.get("auto_status")) != "reachable"
        or truthy(row.get("auto_tls_warning"))
        or truthy(row.get("auto_cross_host_redirect"))
    )


def audit_from_auto(input_row: dict, auto: dict) -> dict:
    input_host = canonical_host(input_row["input_database_url"])
    final_host = canonical_host(auto.get("final_url"))
    cross_host = bool(input_host and final_host and input_host != final_host)
    row = {field: "" for field in AUDIT_FIELDS}
    row.update(
        {
            "id": input_row["id"],
            "record_state": "current",
            "input_database_url": input_row["input_database_url"],
            "input_accessibility": input_row["input_accessibility"],
            "auto_status": clean(auto.get("status")),
            "auto_accessible": auto.get("accessible"),
            "auto_checked_url": clean(auto.get("checked_url")),
            "auto_final_url": clean(auto.get("final_url")),
            "auto_http_status": auto.get("http_status"),
            "auto_http_status_class": http_status_class(auto.get("http_status")),
            "auto_redirect_chain": auto.get("redirect_chain") or [],
            "auto_cross_host_redirect": cross_host,
            "auto_elapsed_seconds": auto.get("elapsed_seconds", 0),
            "auto_error_category": clean(auto.get("error_category")),
            "auto_error_message": clean(auto.get("error_message")),
            "auto_tls_warning": bool(auto.get("tls_warning")),
            "auto_checked_date": clean(auto.get("checked_date")),
            "agent_review_required": False,
            "enhanced_copy_changed": False,
        }
    )
    row["risk_fingerprint"] = risk_fingerprint(row)
    return row


def previous_by_id(path: Path | None) -> dict[str, dict]:
    if path is None:
        return {}
    rows = read_records(path)
    result = {}
    for index, row in enumerate(rows, start=2):
        row_id = clean(row.get("id"))
        if not row_id:
            raise AuditError(f"Previous audit has blank ID at data row {index}")
        if row_id in result:
            raise AuditError(f"Previous audit has duplicate ID: {row_id}")
        result[row_id] = row
    return result


def _copy_previous_fields(row: dict, previous: dict) -> None:
    row.update(
        {
            "previous_auto_status": clean(previous.get("auto_status")),
            "previous_auto_final_url": clean(previous.get("auto_final_url")),
            "previous_http_status_class": clean(
                previous.get("auto_http_status_class")
            ),
            "previous_tls_warning": truthy(previous.get("auto_tls_warning")),
            "previous_cross_host_redirect": truthy(
                previous.get("auto_cross_host_redirect")
            ),
            "previous_agent_final_accessibility": clean(
                previous.get("agent_final_accessibility")
            ),
            "previous_final_accessibility": clean(
                previous.get("final_accessibility")
            ),
        }
    )


def classify_comparison(row: dict, previous: dict | None) -> None:
    risk = is_risk(row)
    if previous is None:
        row["comparison_status"] = "first_run"
        row["comparison_reason"] = "no previous audit"
        if risk:
            row["agent_review_required"] = True
            row["agent_review_reason"] = "automatic risk"
            row["final_accessibility"] = "unresolved"
            row["final_decision_source"] = "pending_agent"
        else:
            row["final_accessibility"] = "live"
            row["final_decision_source"] = "automatic_reachable"
        return

    _copy_previous_fields(row, previous)
    prior_url = clean(previous.get("input_database_url"))
    if row["input_database_url"] != prior_url:
        status, reason = "url_changed", "database_url changed"
    elif row["risk_fingerprint"] != clean(previous.get("risk_fingerprint")):
        status, reason = "changed", "risk fingerprint changed"
    else:
        status, reason = "unchanged", "risk fingerprint unchanged"
    row["comparison_status"] = status
    row["comparison_reason"] = reason

    prior_agent = clean(previous.get("agent_final_accessibility")).lower()
    if status in {"url_changed", "changed"}:
        row["agent_review_required"] = True
        row["agent_review_reason"] = reason
    elif risk and prior_agent not in FINAL_ACCESSIBILITY:
        row["agent_review_required"] = True
        row["agent_review_reason"] = "previous risk record lacks final Agent conclusion"

    if row["agent_review_required"]:
        row["final_accessibility"] = "unresolved"
        row["final_decision_source"] = "pending_agent"
    elif risk:
        prior_final = clean(previous.get("final_accessibility")).lower()
        row["agent_final_accessibility"] = prior_agent
        row["final_accessibility"] = (
            prior_final if prior_final in FINAL_ACCESSIBILITY else "unresolved"
        )
        row["final_decision_source"] = "previous_agent_reused"
    else:
        row["final_accessibility"] = "live"
        row["final_decision_source"] = "automatic_reachable"


def build_missing_current(previous: dict) -> dict:
    row = {field: "" for field in AUDIT_FIELDS}
    row.update(
        {
            "id": clean(previous.get("id")),
            "record_state": "missing_current",
            "input_database_url": clean(previous.get("input_database_url")),
            "input_accessibility": clean(previous.get("input_accessibility")),
            "auto_status": "missing",
            "auto_http_status_class": "none",
            "auto_checked_date": date.today().isoformat(),
            "comparison_status": "missing_current",
            "comparison_reason": "ID exists only in previous audit",
            "agent_review_required": True,
            "agent_review_reason": "missing from current table",
            "final_accessibility": "unresolved",
            "final_decision_source": "pending_agent",
            "enhanced_copy_changed": False,
        }
    )
    row["risk_fingerprint"] = risk_fingerprint(row)
    _copy_previous_fields(row, previous)
    return row


def run_auto_check(args) -> list[dict]:
    input_rows, _ = prepare_input_rows(
        args.input,
        id_column=args.id_column,
        url_column=args.url_column,
        accessibility_column=args.accessibility_column,
    )
    prior = previous_by_id(args.previous_audit)
    limiter = HostFileRateLimiter(args.per_host_rps)
    automatic = check_urls(
        [row["input_database_url"] for row in input_rows],
        workers=args.workers,
        timeout=args.timeout,
        request_gate=limiter,
    )
    audit = []
    current_ids = set()
    for input_row, auto in zip(input_rows, automatic, strict=True):
        current_ids.add(input_row["id"])
        row = audit_from_auto(input_row, auto)
        if prior and input_row["id"] not in prior:
            row["comparison_status"] = "new_id"
            row["comparison_reason"] = "ID not present in previous audit"
            row["agent_review_required"] = True
            row["agent_review_reason"] = "new ID"
            row["final_accessibility"] = "unresolved"
            row["final_decision_source"] = "pending_agent"
        else:
            classify_comparison(row, prior.get(input_row["id"]))
        audit.append(row)
    for row_id, previous in prior.items():
        if row_id not in current_ids:
            audit.append(build_missing_current(previous))
    write_jsonl(args.output, audit)
    write_jsonl(
        args.review_output,
        [row for row in audit if truthy(row.get("agent_review_required"))],
    )
    return audit


def validate_agent_result(row: dict) -> list[str]:
    errors = []
    row_id = clean(row.get("id")) or "<blank>"
    visit = clean(row.get("agent_visit_status"))
    final = clean(row.get("agent_final_accessibility")).lower()
    checked_url = clean(row.get("agent_checked_url"))
    final_url = clean(row.get("agent_final_url"))
    statement = clean(row.get("agent_statement"))
    checked_date = clean(row.get("agent_checked_date"))
    model = clean(row.get("agent_model"))
    if visit not in AGENT_VISIT_STATUSES:
        errors.append(f"id {row_id}: invalid agent_visit_status")
    if final not in FINAL_ACCESSIBILITY:
        errors.append(f"id {row_id}: invalid agent_final_accessibility")
    for field, value in (
        ("agent_checked_url", checked_url),
        ("agent_final_url", final_url),
    ):
        if value and not URL_RE.fullmatch(value):
            errors.append(f"id {row_id}: invalid {field}")
    if final == "live" and (not checked_url or not final_url):
        errors.append(f"id {row_id}: live requires checked and final HTTP(S) URLs")
    if not 20 <= len(statement) <= 500:
        errors.append(f"id {row_id}: agent_statement must be 20-500 characters")
    if not DATE_RE.fullmatch(checked_date):
        errors.append(f"id {row_id}: invalid agent_checked_date")
    else:
        try:
            date.fromisoformat(checked_date)
        except ValueError:
            errors.append(f"id {row_id}: invalid agent_checked_date")
    if not model:
        errors.append(f"id {row_id}: agent_model is required")
    if visit == "security_blocked" and final != "unresolved":
        errors.append(f"id {row_id}: security_blocked must be unresolved")
    if visit == "confirmed_dead" and final != "dead":
        errors.append(f"id {row_id}: confirmed_dead must be dead")
    if visit == "missing_url" and final != "unresolved":
        errors.append(f"id {row_id}: missing_url must be unresolved")
    return errors


def finalize(args) -> list[dict]:
    audit = read_records(args.auto_audit)
    agent_rows = read_records(args.agent_results)
    required = {
        clean(row.get("id"))
        for row in audit
        if truthy(row.get("agent_review_required"))
    }
    by_id = {}
    errors = []
    for row in agent_rows:
        row_id = clean(row.get("id"))
        if not row_id:
            errors.append("Agent result contains a blank ID")
            continue
        if row_id in by_id:
            errors.append(f"Agent result contains duplicate ID: {row_id}")
            continue
        by_id[row_id] = row
        errors.extend(validate_agent_result(row))
    if set(by_id) != required:
        missing = sorted(required - set(by_id))
        extras = sorted(set(by_id) - required)
        if missing:
            errors.append(f"Missing Agent result IDs: {', '.join(missing[:20])}")
        if extras:
            errors.append(f"Unexpected Agent result IDs: {', '.join(extras[:20])}")
    if errors:
        raise AuditError("; ".join(errors))

    updates = []
    for row in audit:
        row_id = clean(row.get("id"))
        if row_id in by_id:
            result = by_id[row_id]
            for field in (
                "agent_visit_status",
                "agent_checked_url",
                "agent_final_url",
                "agent_click_path",
                "agent_statement",
                "agent_checked_date",
                "agent_model",
                "agent_final_accessibility",
            ):
                row[field] = clean(result.get(field))
            agent_final = clean(result.get("agent_final_accessibility")).lower()
            if agent_final in {"live", "dead"}:
                row["final_accessibility"] = agent_final
                row["final_decision_source"] = "agent_browser"
            else:
                original = clean(row.get("input_accessibility")).lower()
                row["final_accessibility"] = (
                    original if original in {"live", "dead"} else "unresolved"
                )
                row["final_decision_source"] = "input_preserved_after_unresolved"
        original = clean(row.get("input_accessibility")).lower()
        final = clean(row.get("final_accessibility")).lower()
        changed = (
            clean(row.get("record_state")) != "missing_current"
            and final in {"live", "dead"}
            and final != original
        )
        row["enhanced_copy_changed"] = changed
        if clean(row.get("record_state")) != "missing_current":
            apply_update = (
                final in {"live", "dead"}
                and clean(row.get("final_decision_source"))
                != "input_preserved_after_unresolved"
            )
            updates.append(
                {
                    "id": row_id,
                    "final_accessibility": final,
                    "apply_update": apply_update,
                    "changed": changed,
                }
            )
    write_jsonl(args.output, audit)
    write_jsonl(args.updates_output, updates)
    return audit


def validate_outputs(args) -> dict:
    input_rows, _ = prepare_input_rows(
        args.input,
        id_column=args.id_column,
        url_column=args.url_column,
        accessibility_column=args.accessibility_column,
    )
    audit = read_records(args.audit)
    updates = read_records(args.updates)
    errors = []
    input_ids = [row["id"] for row in input_rows]
    audit_current = [
        clean(row.get("id"))
        for row in audit
        if clean(row.get("record_state")) != "missing_current"
    ]
    update_ids = [clean(row.get("id")) for row in updates]
    if audit_current != input_ids:
        errors.append("Current audit IDs/order differ from input")
    if update_ids != input_ids:
        errors.append("Update manifest IDs/order differ from input")
    for row in audit:
        if clean(row.get("auto_status")) not in AUTO_STATUSES:
            errors.append(f"id {clean(row.get('id'))}: invalid auto_status")
        final = clean(row.get("final_accessibility")).lower()
        if final not in FINAL_ACCESSIBILITY:
            errors.append(f"id {clean(row.get('id'))}: invalid final accessibility")
        if clean(row.get("agent_model")):
            errors.extend(validate_agent_result(row))
    for row in updates:
        final = clean(row.get("final_accessibility")).lower()
        if truthy(row.get("apply_update")) and final not in {"live", "dead"}:
            errors.append(f"id {clean(row.get('id'))}: invalid applied update")
    report = {
        "valid": not errors,
        "input_rows": len(input_rows),
        "audit_rows": len(audit),
        "missing_current_rows": sum(
            clean(row.get("record_state")) == "missing_current" for row in audit
        ),
        "update_rows": len(updates),
        "errors": errors,
    }
    write_json(args.output, report)
    if errors:
        raise AuditError("Validation failed; see output report")
    return report


def check_one(args) -> None:
    limiter = HostFileRateLimiter(args.per_host_rps)
    result = check_urls(
        [args.url],
        workers=args.workers,
        timeout=args.timeout,
        request_gate=limiter,
    )[0]
    write_json(args.output, {"id": clean(args.id), **result})


def add_column_overrides(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--sheet", default="", help="Source worksheet name recorded by the orchestrating Skill")
    parser.add_argument("--id-column", default="")
    parser.add_argument("--url-column", default="")
    parser.add_argument("--accessibility-column", default="")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    one = subparsers.add_parser("check-one", help="Run the shared checker for one ID and URL")
    one.add_argument("--id", required=True)
    one.add_argument("--url", required=True)
    one.add_argument("--output", type=Path, required=True)
    one.add_argument("--workers", type=int, required=True)
    one.add_argument("--timeout", type=float, required=True)
    one.add_argument("--per-host-rps", type=float, required=True)

    auto = subparsers.add_parser("auto-check", help="Build automatic audit and Agent review queue")
    auto.add_argument("--input", type=Path, required=True)
    auto.add_argument("--output", type=Path, required=True)
    auto.add_argument("--review-output", type=Path, required=True)
    auto.add_argument("--workers", type=int, required=True)
    auto.add_argument("--timeout", type=float, required=True)
    auto.add_argument("--per-host-rps", type=float, required=True)
    auto.add_argument("--previous-audit", type=Path)
    add_column_overrides(auto)

    final = subparsers.add_parser("finalize", help="Merge validated Agent results into an audit")
    final.add_argument("--auto-audit", type=Path, required=True)
    final.add_argument("--agent-results", type=Path, required=True)
    final.add_argument("--output", type=Path, required=True)
    final.add_argument("--updates-output", type=Path, required=True)

    validate = subparsers.add_parser("validate", help="Validate audit and accessibility update manifest")
    validate.add_argument("--input", type=Path, required=True)
    validate.add_argument("--audit", type=Path, required=True)
    validate.add_argument("--updates", type=Path, required=True)
    validate.add_argument("--output", type=Path, required=True)
    add_column_overrides(validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "check-one":
            check_one(args)
        elif args.command == "auto-check":
            run_auto_check(args)
        elif args.command == "finalize":
            finalize(args)
        elif args.command == "validate":
            validate_outputs(args)
        else:
            raise AuditError(f"Unknown command: {args.command}")
    except (AuditError, OSError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    output = getattr(args, "output", None)
    print(f"Success! Data written to: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
