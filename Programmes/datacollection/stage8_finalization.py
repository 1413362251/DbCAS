"""Database-level finalization for Stage 8 curation results.

This module deliberately separates automatic URL triage from the browser review
performed by agents.  Automatic results decide which rows need review; they do
not, by themselves, turn a database into a live/dead final decision.
"""

from __future__ import annotations

import json
import hashlib
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import urlsplit, urlunsplit

import pandas as pd


PROGRAMMES_DIR = Path(__file__).resolve().parents[1]
if str(PROGRAMMES_DIR) not in sys.path:
    sys.path.insert(0, str(PROGRAMMES_DIR))


ID_COLUMN = "<main,t-word-id> id"
TITLE_COLUMN = "<main,t-word> title"
DATABASE_NAME_COLUMN = "<main,t-word> database_name"
DATABASE_URL_COLUMN = "<main,t-word-url> database_url"
DOI_COLUMN = "<sub,t-word-doi> doi"
PMID_COLUMN = "<sub,t-word-pmid> pmid"
YEAR_COLUMN = "<sub,t-numeric> year"
ORIGINAL_DATABASE_COLUMN = "<main,t-bool> original_48_database"
POLICY_VERSION_COLUMN = "<sub,t-word-tag> screening_policy_version"
ACCESSIBILITY_COLUMN = "<main,t-word-tag> accessibility"
DECISION_COLUMN = "<main,t-bool> db_type_confirmation"
EVIDENCE_URL_COLUMN = "<sub,t-word-url> evidence_url"
EVIDENCE_SOURCE_TYPE_COLUMN = "<sub,t-word-tag> evidence_source_type"
EVIDENCE_STATEMENT_COLUMN = "<sub,t-word> evidence_statement"
EVIDENCE_CHECKED_DATE_COLUMN = "<sub,t-word> evidence_checked_date"
QUALIFICATION_BASIS_COLUMN = "<main,t-word-tag> qualification_basis"
EXCLUSION_CODE_COLUMN = "<main,t-word-tag> exclusion_code"
MANUAL_REVIEW_COLUMN = "<main,t-bool> manual_review_needed"
FOCUS_COLUMN = "<main,t-word-tag> focus"
GENE_EXPRESSION_COLUMN = "<main,t-bool> gene_expression_available"
NEURAL_LINK_COLUMN = "<main,t-word-tag> neural_link"
NEURAL_METADATA_COLUMNS = (
    "<main,t-word-tag> tissue_or_brain_region",
    "<sub,t-word-tag> cell_type",
    "<sub,t-word-tag> disease_association",
    "<sub,t-word-tag> developmental_association",
)

FINALIZATION_POLICY_VERSION = "transcript_splicing_v2_stage8_finalize_v2"
FINAL_REVIEW_CHUNK_SIZE = 25

DUPLICATE_GROUP_COLUMN = "<audit,t-word-tag> duplicate_candidate_group"
DUPLICATE_MEMBERS_COLUMN = "<audit,t-word> duplicate_member_ids"
AUTOMATIC_STATUS_COLUMN = "<audit,t-word-tag> automatic_accessibility"
CANONICAL_ID_COLUMN = "<audit,t-word> canonical_id"
REPRESENTATIVE_ID_COLUMN = "<audit,t-word-id> representative_id"
AGENT_VISIT_STATUS_COLUMN = "<audit,t-word-tag> agent_visit_status"
AGENT_CHECKED_URL_COLUMN = "<audit,t-word-url> agent_checked_url"
AGENT_CLICK_PATH_COLUMN = "<audit,t-word> agent_click_path"
AGENT_REVIEW_STATEMENT_COLUMN = "<audit,t-word> agent_review_statement"

ACCESSIBILITY_AGENT_AUDIT_COLUMNS = [
    "agent_reviewed",
    "agent_final_database_name",
    "agent_final_database_url",
    "agent_final_accessibility",
    "agent_final_db_type_confirmation",
    "agent_final_evidence_url",
    "agent_final_evidence_source_type",
    "agent_final_canonical_id",
    "agent_final_representative_id",
    AGENT_VISIT_STATUS_COLUMN,
    AGENT_CHECKED_URL_COLUMN,
    AGENT_CLICK_PATH_COLUMN,
    AGENT_REVIEW_STATEMENT_COLUMN,
]

INCREMENTAL_GROUP_COLUMN = "<audit,t-word-tag> incremental_candidate_group"
INCREMENTAL_MEMBERS_COLUMN = "<audit,t-word> incremental_member_ids"
INCREMENTAL_ACTION_COLUMN = "<audit,t-word-tag> incremental_action"
INCREMENTAL_CANONICAL_COLUMN = "<audit,t-word> incremental_canonical_id"
INCREMENTAL_REPRESENTATIVE_COLUMN = "<audit,t-word-id> incremental_representative_id"
INCREMENTAL_STATEMENT_COLUMN = "<audit,t-word> incremental_decision_statement"

INCREMENTAL_REVIEW_COLUMNS = [
    ID_COLUMN,
    DATABASE_NAME_COLUMN,
    DATABASE_URL_COLUMN,
    TITLE_COLUMN,
    DOI_COLUMN,
    PMID_COLUMN,
    YEAR_COLUMN,
    ORIGINAL_DATABASE_COLUMN,
    INCREMENTAL_GROUP_COLUMN,
    INCREMENTAL_MEMBERS_COLUMN,
    INCREMENTAL_ACTION_COLUMN,
    INCREMENTAL_CANONICAL_COLUMN,
    INCREMENTAL_REPRESENTATIVE_COLUMN,
    INCREMENTAL_STATEMENT_COLUMN,
]

FINAL_REVIEW_AUDIT_COLUMNS = [
    DUPLICATE_GROUP_COLUMN,
    DUPLICATE_MEMBERS_COLUMN,
    AUTOMATIC_STATUS_COLUMN,
    CANONICAL_ID_COLUMN,
    REPRESENTATIVE_ID_COLUMN,
    AGENT_VISIT_STATUS_COLUMN,
    AGENT_CHECKED_URL_COLUMN,
    AGENT_CLICK_PATH_COLUMN,
    AGENT_REVIEW_STATEMENT_COLUMN,
]

FINAL_REVIEW_CHECKPOINT_COLUMNS = [
    "chunk_id",
    "input_path",
    "result_path",
    "status",
    "input_rows",
    "result_rows",
    "error",
    "completed_at",
    "finalization_policy_version",
]

IMMUTABLE_FINAL_REVIEW_COLUMNS = [
    ID_COLUMN,
    TITLE_COLUMN,
    DOI_COLUMN,
    PMID_COLUMN,
    YEAR_COLUMN,
    ORIGINAL_DATABASE_COLUMN,
    POLICY_VERSION_COLUMN,
    DUPLICATE_GROUP_COLUMN,
    DUPLICATE_MEMBERS_COLUMN,
    AUTOMATIC_STATUS_COLUMN,
]

GENERIC_DATABASE_NAMES = {
    "database",
    "databases",
    "resource",
    "resources",
    "repository",
    "portal",
    "website",
    "webserver",
}

OFFICIAL_EVIDENCE_TYPES = {"official_database", "official_documentation"}
FINAL_VISIT_STATUSES = {"entered_database", "confirmed_dead", "restricted", "manual_blocked"}
NEURAL_METADATA_PATTERN = re.compile(
    r"brain|neural|neuron|glia|cortex|hippocamp|cerebell|spinal|astrocy|microglia|oligodend|parkinson|alzheimer",
    flags=re.IGNORECASE,
)


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if value != value:
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def clean_id(value: Any) -> str:
    text = clean_text(value)
    if re.fullmatch(r"-?\d+(?:\.0+)?", text):
        return str(int(float(text)))
    return text


def has_explicit_neural_metadata(row: pd.Series) -> bool:
    metadata = ";".join(clean_text(row.get(column)) for column in NEURAL_METADATA_COLUMNS)
    metadata = re.sub(
        r"\b(?:renal|kidney)\s+cortex\b",
        "",
        metadata,
        flags=re.IGNORECASE,
    )
    return bool(NEURAL_METADATA_PATTERN.search(metadata))


def normalize_neural_link_consistency(df: pd.DataFrame) -> pd.DataFrame:
    """Promote contradictory none values when curated metadata is explicitly neural."""
    normalized = df.copy()
    for index, row in normalized.iterrows():
        if clean_text(row.get(NEURAL_LINK_COLUMN)).lower() == "none" and has_explicit_neural_metadata(row):
            normalized.at[index, NEURAL_LINK_COLUMN] = "partial"
    return normalized


def normalize_database_name(value: Any) -> str:
    text = clean_text(value).lower().replace("&", " and ")
    if text in {"", "unknown", "none", "n/a", "na", "not available", "not applicable"}:
        return ""
    text = re.sub(r"(?:\b(?:database|db)\b\s*)+$", "", text).strip()
    text = re.sub(r"\b(?:version|release|v)\s*\d+(?:\.\d+)*\b", "", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def database_name_keys(value: Any) -> set[str]:
    """Return conservative exact/explicit-acronym keys for duplicate discovery."""
    text = clean_text(value)
    keys = {normalize_database_name(text)}
    for alias in re.findall(r"\(([^()]{2,40})\)", text):
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{1,15}", alias.strip()):
            keys.add(normalize_database_name(alias))
    leading = re.match(r"^([A-Z][A-Z0-9_-]{1,14})\s*[:\-]", text)
    if leading:
        keys.add(normalize_database_name(leading.group(1)))
    return {key for key in keys if key and key not in GENERIC_DATABASE_NAMES}


def normalize_database_url(value: Any) -> str:
    text = clean_text(value)
    if not text or text.lower() in {"unknown", "none", "n/a", "na", "not available", "not applicable"}:
        return ""
    candidate = text if "://" in text else f"https://{text}"
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return ""
    path = re.sub(r"/+", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    # Exact host+path equality is safe even on shared hosts.  Host equality is
    # intentionally insufficient for NCBI/EBI/UCSC/GitHub/archives.
    return urlunsplit(("", host, path, parts.query, ""))


class _UnionFind:
    def __init__(self, values: Iterable[str]):
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def build_duplicate_candidate_map(
    df: pd.DataFrame,
    accessibility_audit: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Return one mapping row per member of a yes-only duplicate candidate group."""
    yes = df[df[DECISION_COLUMN].map(lambda value: clean_text(value).lower()) == "yes"].copy()
    ids = [clean_id(value) for value in yes[ID_COLUMN].tolist()]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate input IDs are not allowed during finalization.")
    union = _UnionFind(ids)
    name_members: Dict[str, List[str]] = {}
    url_members: Dict[str, List[str]] = {}
    url_keys_by_id: Dict[str, set[str]] = {}
    row_by_id: Dict[str, pd.Series] = {}
    access_by_id = {
        clean_id(row.get(ID_COLUMN)): row
        for _, row in accessibility_audit.iterrows()
    } if accessibility_audit is not None else {}
    for (_, row), row_id in zip(yes.iterrows(), ids):
        row_by_id[row_id] = row
        for name_key in database_name_keys(row.get(DATABASE_NAME_COLUMN)):
            name_members.setdefault(name_key, []).append(row_id)
        url_values = [row.get(DATABASE_URL_COLUMN)]
        automatic_final_url = clean_text(access_by_id.get(row_id, {}).get("final_url"))
        automatic_status = clean_text(access_by_id.get(row_id, {}).get("status")).lower()
        if automatic_final_url and automatic_status in {"reachable", "restricted", "continue_required"}:
            url_values.append(automatic_final_url)
        for url_value in url_values:
            url_key = normalize_database_url(url_value)
            if url_key:
                url_members.setdefault(url_key, []).append(row_id)
                url_keys_by_id.setdefault(row_id, set()).add(url_key)
    for members in list(name_members.values()) + list(url_members.values()):
        if len(members) > 1:
            for member in members[1:]:
                union.union(members[0], member)

    groups: Dict[str, List[str]] = {}
    for row_id in ids:
        groups.setdefault(union.find(row_id), []).append(row_id)
    duplicate_groups = [sorted(members, key=_sort_id) for members in groups.values() if len(members) > 1]
    duplicate_groups.sort(key=lambda members: _sort_id(members[0]))

    rows: List[Dict[str, Any]] = []
    for group_number, members in enumerate(duplicate_groups, start=1):
        group_id = f"dup_{group_number:04d}"
        group_name_keys = [database_name_keys(row_by_id[row_id].get(DATABASE_NAME_COLUMN)) for row_id in members]
        basis = []
        if set.intersection(*group_name_keys) if group_name_keys else set():
            basis.append("name")
        group_url_keys = [url_keys_by_id.get(row_id, set()) for row_id in members]
        if set.intersection(*group_url_keys) if group_url_keys else set():
            basis.append("url")
        for row_id in members:
            rows.append(
                {
                    ID_COLUMN: row_id,
                    DUPLICATE_GROUP_COLUMN: group_id,
                    DUPLICATE_MEMBERS_COLUMN: ";".join(members),
                    "match_basis": ";".join(basis) or "transitive",
                    "candidate_count": len(members),
                }
            )
    return pd.DataFrame(
        rows,
        columns=[
            ID_COLUMN,
            DUPLICATE_GROUP_COLUMN,
            DUPLICATE_MEMBERS_COLUMN,
            "match_basis",
            "candidate_count",
        ],
    )


def prepare_incremental_duplicate_review(
    final_df: pd.DataFrame,
    canonical_audit: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Build an Agent decision table for yes duplicates created during review."""
    mapping = build_duplicate_candidate_map(final_df)
    candidate_sets: List[set[str]] = []
    if not mapping.empty:
        candidate_sets.extend(
            {
                clean_id(value)
                for value in clean_text(member_text).split(";")
                if clean_id(value)
            }
            for member_text in mapping[DUPLICATE_MEMBERS_COLUMN].drop_duplicates()
        )
    if canonical_audit is not None and not canonical_audit.empty:
        for canonical, group in canonical_audit.groupby("canonical_id", sort=False):
            if not clean_text(canonical):
                continue
            representatives = {
                clean_id(value)
                for value in group["representative_id"]
                if clean_id(value)
            }
            if len(representatives) > 1:
                candidate_sets.append(representatives)
    candidate_sets = [group for group in candidate_sets if len(group) > 1]
    if not candidate_sets:
        return pd.DataFrame(columns=INCREMENTAL_REVIEW_COLUMNS)

    merged_sets: List[set[str]] = []
    for candidate_set in candidate_sets:
        overlaps = [group for group in merged_sets if group & candidate_set]
        if not overlaps:
            merged_sets.append(set(candidate_set))
            continue
        combined = set(candidate_set)
        for group in overlaps:
            combined.update(group)
            merged_sets.remove(group)
        merged_sets.append(combined)

    source_by_id = {clean_id(row[ID_COLUMN]): row for _, row in final_df.iterrows()}
    rows: List[Dict[str, Any]] = []
    ordered_sets = sorted(merged_sets, key=lambda group: min((_sort_id(value) for value in group)))
    for group_index, member_set in enumerate(ordered_sets, start=1):
        members = sorted(member_set, key=_sort_id)
        member_text = ";".join(members)
        group_id = f"incremental_dup_{group_index:04d}"
        for row_id in members:
            source = source_by_id.get(row_id)
            if source is None:
                raise ValueError(f"Incremental duplicate candidate ID is missing from final data: {row_id}")
            rows.append(
                {
                    ID_COLUMN: row_id,
                    DATABASE_NAME_COLUMN: clean_text(source.get(DATABASE_NAME_COLUMN)),
                    DATABASE_URL_COLUMN: clean_text(source.get(DATABASE_URL_COLUMN)),
                    TITLE_COLUMN: clean_text(source.get(TITLE_COLUMN)),
                    DOI_COLUMN: clean_text(source.get(DOI_COLUMN)),
                    PMID_COLUMN: clean_text(source.get(PMID_COLUMN)),
                    YEAR_COLUMN: source.get(YEAR_COLUMN, ""),
                    ORIGINAL_DATABASE_COLUMN: clean_text(source.get(ORIGINAL_DATABASE_COLUMN)),
                    INCREMENTAL_GROUP_COLUMN: group_id,
                    INCREMENTAL_MEMBERS_COLUMN: member_text,
                    INCREMENTAL_ACTION_COLUMN: "",
                    INCREMENTAL_CANONICAL_COLUMN: "",
                    INCREMENTAL_REPRESENTATIVE_COLUMN: "",
                    INCREMENTAL_STATEMENT_COLUMN: "",
                }
            )
    return pd.DataFrame(rows, columns=INCREMENTAL_REVIEW_COLUMNS)


def validate_incremental_duplicate_result(input_df: pd.DataFrame, result_df: pd.DataFrame) -> Optional[str]:
    if list(input_df.columns) != INCREMENTAL_REVIEW_COLUMNS or list(result_df.columns) != INCREMENTAL_REVIEW_COLUMNS:
        return "incremental duplicate schema mismatch"
    if len(input_df) != len(result_df):
        return "incremental duplicate row count mismatch"
    for column in INCREMENTAL_REVIEW_COLUMNS[:10]:
        cleaner = clean_id if column in {ID_COLUMN, YEAR_COLUMN} else clean_text
        if [cleaner(value) for value in input_df[column]] != [cleaner(value) for value in result_df[column]]:
            return f"incremental Agent modified immutable column: {column}"
    errors: List[str] = []
    for group_id, group in result_df.groupby(INCREMENTAL_GROUP_COLUMN, sort=False):
        member_ids = {clean_id(value) for value in group[ID_COLUMN]}
        declared_sets = {
            frozenset(clean_id(value) for value in clean_text(text).split(";") if clean_id(value))
            for text in group[INCREMENTAL_MEMBERS_COLUMN]
        }
        actions = {clean_text(value).lower() for value in group[INCREMENTAL_ACTION_COLUMN]}
        canonicals = {clean_text(value) for value in group[INCREMENTAL_CANONICAL_COLUMN]}
        representatives = {clean_id(value) for value in group[INCREMENTAL_REPRESENTATIVE_COLUMN]}
        if declared_sets != {frozenset(member_ids)}:
            errors.append(f"{group_id}: inconsistent member IDs")
            continue
        if len(actions) != 1 or next(iter(actions), "") not in {"merge", "split"}:
            errors.append(f"{group_id}: action must be uniformly merge or split")
            continue
        action = next(iter(actions))
        if action == "merge":
            if len(canonicals) != 1 or not next(iter(canonicals), ""):
                errors.append(f"{group_id}: merge requires one canonical ID")
            if len(representatives) != 1 or next(iter(representatives), "") not in member_ids:
                errors.append(f"{group_id}: merge requires one member representative")
        else:
            if len(canonicals) != len(member_ids):
                errors.append(f"{group_id}: split requires one unique canonical ID per row")
            if any(clean_id(row[INCREMENTAL_REPRESENTATIVE_COLUMN]) != clean_id(row[ID_COLUMN]) for _, row in group.iterrows()):
                errors.append(f"{group_id}: split rows must represent themselves")
        if any(len(clean_text(value)) < 20 for value in group[INCREMENTAL_STATEMENT_COLUMN]):
            errors.append(f"{group_id}: decision statement too short")
    return "; ".join(errors[:8]) or None


def apply_incremental_duplicate_decisions(
    final_df: pd.DataFrame,
    decisions: pd.DataFrame,
    canonical_audit: Optional[pd.DataFrame] = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    review_input = prepare_incremental_duplicate_review(final_df, canonical_audit=canonical_audit)
    error = validate_incremental_duplicate_result(review_input, decisions)
    if error:
        raise ValueError(f"Invalid incremental duplicate decisions: {error}")
    output = final_df.copy()
    remove_ids: set[str] = set()
    audit_rows: List[Dict[str, Any]] = []
    split_signatures: set[frozenset[str]] = set()
    for _, group in decisions.groupby(INCREMENTAL_GROUP_COLUMN, sort=False):
        members = sorted({clean_id(value) for value in group[ID_COLUMN]}, key=_sort_id)
        action = clean_text(group[INCREMENTAL_ACTION_COLUMN].iloc[0]).lower()
        canonical = clean_text(group[INCREMENTAL_CANONICAL_COLUMN].iloc[0])
        representative_id = ""
        removed: List[str] = []
        if action == "split":
            split_signatures.add(frozenset(members))
        else:
            representative_id = clean_id(group[INCREMENTAL_REPRESENTATIVE_COLUMN].iloc[0])
            original48 = any(
                clean_text(output.loc[output[ID_COLUMN].map(clean_id) == member, ORIGINAL_DATABASE_COLUMN].iloc[0]).lower() == "yes"
                for member in members
            )
            if original48:
                output.loc[output[ID_COLUMN].map(clean_id) == representative_id, ORIGINAL_DATABASE_COLUMN] = "yes"
            removed = [member for member in members if member != representative_id]
            remove_ids.update(removed)
        audit_rows.append(
            {
                "canonical_id": canonical,
                "representative_id": representative_id,
                "member_ids": ";".join(members),
                "removed_ids": ";".join(removed),
                "member_count": len(members),
                "incremental_action": action,
                "decision_statement": clean_text(group[INCREMENTAL_STATEMENT_COLUMN].iloc[0]),
            }
        )
    output = output[~output[ID_COLUMN].map(clean_id).isin(remove_ids)].copy().reset_index(drop=True)
    remaining = build_duplicate_candidate_map(output)
    if not remaining.empty:
        signatures = {
            frozenset(clean_id(value) for value in clean_text(text).split(";") if clean_id(value))
            for text in remaining[DUPLICATE_MEMBERS_COLUMN]
        }
        if signatures - split_signatures:
            raise ValueError("New unresolved yes duplicates remain after incremental decisions.")
    return output, pd.DataFrame(audit_rows)


def validate_conditional_evidence(df: pd.DataFrame) -> Optional[str]:
    errors: List[str] = []
    for _, row in df.iterrows():
        row_id = clean_id(row.get(ID_COLUMN)) or "<blank>"
        accessibility = clean_text(row.get(ACCESSIBILITY_COLUMN)).lower()
        source_type = clean_text(row.get(EVIDENCE_SOURCE_TYPE_COLUMN)).lower()
        evidence_url = clean_text(row.get(EVIDENCE_URL_COLUMN))
        if not re.fullmatch(r"https?://\S+", evidence_url, flags=re.IGNORECASE):
            errors.append(f"id {row_id}: invalid evidence URL")
        if accessibility == "live" and source_type not in OFFICIAL_EVIDENCE_TYPES:
            errors.append(f"id {row_id}: live evidence must be official_database or official_documentation")
        elif accessibility == "dead" and source_type != "publication":
            errors.append(f"id {row_id}: dead evidence must be publication")
        elif accessibility not in {"live", "dead"}:
            errors.append(f"id {row_id}: invalid accessibility {accessibility or '<blank>'}")
    return "; ".join(errors[:8]) or None


def validate_final_decision_fields(df: pd.DataFrame) -> Optional[str]:
    errors: List[str] = []
    manual_count = 0
    for _, row in df.iterrows():
        row_id = clean_id(row.get(ID_COLUMN)) or "<blank>"
        decision = clean_text(row.get(DECISION_COLUMN)).lower()
        qualification = clean_text(row.get(QUALIFICATION_BASIS_COLUMN)).lower()
        exclusion = clean_text(row.get(EXCLUSION_CODE_COLUMN)).lower()
        manual = clean_text(row.get(MANUAL_REVIEW_COLUMN)).lower()
        focus = clean_text(row.get(FOCUS_COLUMN)).lower()
        expression = clean_text(row.get(GENE_EXPRESSION_COLUMN)).lower()
        statement = clean_text(row.get(EVIDENCE_STATEMENT_COLUMN))
        checked_date = clean_text(row.get(EVIDENCE_CHECKED_DATE_COLUMN))
        if decision == "yes":
            if not qualification or qualification == "not_applicable":
                errors.append(f"id {row_id}: yes requires qualification basis")
            if exclusion != "not_applicable" or manual != "no":
                errors.append(f"id {row_id}: yes requires exclusion=not_applicable and manual_review=no")
        elif decision == "no":
            if qualification != "not_applicable" or exclusion in {"", "not_applicable"}:
                errors.append(f"id {row_id}: no requires not_applicable qualification and an exclusion code")
            if focus != "unknown" or expression != "no":
                errors.append(f"id {row_id}: no requires focus=unknown and gene_expression_available=no")
        else:
            errors.append(f"id {row_id}: invalid db_type_confirmation")
        if manual == "yes":
            manual_count += 1
            if decision != "no" or exclusion != "insufficient_evidence":
                errors.append(f"id {row_id}: manual review is only allowed for no+insufficient_evidence")
        elif manual != "no":
            errors.append(f"id {row_id}: invalid manual_review_needed")
        if not 20 <= len(statement) <= 300:
            errors.append(f"id {row_id}: evidence statement length must be 20-300")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", checked_date):
            errors.append(f"id {row_id}: invalid evidence checked date")
        if clean_text(row.get(NEURAL_LINK_COLUMN)).lower() == "none" and has_explicit_neural_metadata(row):
            errors.append(f"id {row_id}: neural_link none conflicts with neural metadata")
    allowed_manual = max(1, (len(df) + 19) // 20) if len(df) else 0
    if manual_count > allowed_manual:
        errors.append(f"manual review limit exceeded: {manual_count}>{allowed_manual}")
    return "; ".join(errors[:8]) or None


def run_accessibility_audit(
    df: pd.DataFrame,
    workers: int = 32,
    timeout: float = 120,
    check_urls_func: Optional[Callable[..., List[Dict[str, Any]]]] = None,
) -> pd.DataFrame:
    if check_urls_func is None:
        from scripts.check_url_accessibility import check_urls as check_urls_func

    database_urls = [clean_text(value) for value in df[DATABASE_URL_COLUMN].tolist()]
    evidence_urls = [clean_text(value) for value in df[EVIDENCE_URL_COLUMN].tolist()]
    urls = database_urls + evidence_urls
    results = check_urls_func(urls, workers=workers, timeout=timeout)
    if len(results) != len(urls):
        raise ValueError(f"URL checker returned {len(results)} rows for {len(urls)} inputs.")
    database_results = results[: len(df)]
    evidence_results = results[len(df) :]
    audit_rows = []
    for (_, source), checked, evidence_checked in zip(df.iterrows(), database_results, evidence_results):
        row = {ID_COLUMN: clean_id(source.get(ID_COLUMN)), DATABASE_URL_COLUMN: clean_text(source.get(DATABASE_URL_COLUMN))}
        row.update(checked)
        redirect_chain = row.get("redirect_chain")
        if isinstance(redirect_chain, (list, tuple)):
            row["redirect_chain"] = " -> ".join(clean_text(value) for value in redirect_chain)
        for key, value in evidence_checked.items():
            if key in {"original_url", "status", "final_url", "http_status", "error_category", "redirect_chain", "tls_warning", "checked_url", "error_message", "elapsed_seconds", "checked_date", "accessible"}:
                if key == "redirect_chain" and isinstance(value, (list, tuple)):
                    value = " -> ".join(clean_text(item) for item in value)
                row[f"evidence_{key}"] = value
        audit_rows.append(row)
    return pd.DataFrame(audit_rows)


def augment_evidence_accessibility_audit(
    df: pd.DataFrame,
    existing_audit: pd.DataFrame,
    workers: int = 32,
    timeout: float = 120,
    check_urls_func: Optional[Callable[..., List[Dict[str, Any]]]] = None,
) -> pd.DataFrame:
    """Add evidence diagnostics to an older database-only audit without rescanning DB URLs."""
    if len(existing_audit) != len(df):
        raise ValueError("Existing accessibility audit row count does not match finalization input.")
    if check_urls_func is None:
        from Programmes.scripts.check_url_accessibility import check_urls as check_urls_func
    evidence_urls = [clean_text(value) for value in df[EVIDENCE_URL_COLUMN].tolist()]
    results = check_urls_func(evidence_urls, workers=workers, timeout=timeout)
    if len(results) != len(df):
        raise ValueError(f"URL checker returned {len(results)} evidence rows for {len(df)} inputs.")
    augmented = existing_audit.copy()
    for key in ("original_url", "status", "checked_url", "final_url", "http_status", "redirect_chain", "elapsed_seconds", "error_category", "error_message", "tls_warning", "checked_date", "accessible"):
        values = []
        for result in results:
            value = result.get(key, "")
            if key == "redirect_chain" and isinstance(value, (list, tuple)):
                value = " -> ".join(clean_text(item) for item in value)
            values.append(value)
        augmented[f"evidence_{key}"] = values
    return augmented


def prepare_final_review_dataframe(
    raw_df: pd.DataFrame,
    accessibility_audit: pd.DataFrame,
    agent_columns: List[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if list(raw_df.columns) != agent_columns:
        raise ValueError("Finalization input must use the standard 30-column Stage 8 schema.")
    duplicate_map = build_duplicate_candidate_map(raw_df, accessibility_audit)
    duplicate_by_id = {
        clean_id(row[ID_COLUMN]): row for _, row in duplicate_map.iterrows()
    }
    access_by_id = {
        clean_id(row[ID_COLUMN]): row for _, row in accessibility_audit.iterrows()
    }
    rows = []
    for _, row in raw_df.iterrows():
        row_id = clean_id(row.get(ID_COLUMN))
        access = access_by_id.get(row_id, {})
        auto_status = clean_text(access.get("status"))
        evidence_status = clean_text(access.get("evidence_status")) or "missing"
        final_url = clean_text(access.get("final_url"))
        current = clean_text(row.get(ACCESSIBILITY_COLUMN)).lower()
        current_source = clean_text(row.get(EVIDENCE_SOURCE_TYPE_COLUMN)).lower()
        auto_live = auto_status in {"reachable", "restricted", "continue_required"}
        conflict = bool(auto_status) and (
            (auto_live and current == "dead")
            or (auto_status in {"unreachable", "missing"} and current == "live")
        )
        evidence_mismatch = (
            (current == "live" and current_source not in OFFICIAL_EVIDENCE_TYPES)
            or (current == "dead" and current_source != "publication")
        )
        original_host = _url_host(row.get(DATABASE_URL_COLUMN))
        final_host = _url_host(final_url)
        cross_domain_redirect = bool(original_host and final_host and original_host != final_host)
        has_tls_warning = clean_text(access.get("tls_warning")).lower() in {"true", "1", "yes"}
        evidence_tls_warning = clean_text(access.get("evidence_tls_warning")).lower() in {"true", "1", "yes"}
        needs_review = (
            clean_text(row.get(DECISION_COLUMN)).lower() == "yes"
            or conflict
            or auto_status in {"restricted", "continue_required"}
            or evidence_mismatch
            or cross_domain_redirect
            # 401/403/429 and bot protection are diagnostic restrictions, not
            # proof that an evidence page is unavailable.  Canonical yes rows
            # are reviewed independently; other rows require review here only
            # for a true failure/missing page or a click-through interstitial.
            or evidence_status in {"continue_required", "unreachable", "missing"}
            or has_tls_warning
            or evidence_tls_warning
        )
        if not needs_review:
            continue
        output = {column: row.get(column, "") for column in agent_columns}
        duplicate = duplicate_by_id.get(row_id, {})
        output[DUPLICATE_GROUP_COLUMN] = clean_text(duplicate.get(DUPLICATE_GROUP_COLUMN))
        output[DUPLICATE_MEMBERS_COLUMN] = clean_text(duplicate.get(DUPLICATE_MEMBERS_COLUMN)) or row_id
        output[AUTOMATIC_STATUS_COLUMN] = auto_status or "missing"
        output[CANONICAL_ID_COLUMN] = ""
        output[REPRESENTATIVE_ID_COLUMN] = ""
        output[AGENT_VISIT_STATUS_COLUMN] = ""
        output[AGENT_CHECKED_URL_COLUMN] = final_url
        output[AGENT_CLICK_PATH_COLUMN] = ""
        output[AGENT_REVIEW_STATEMENT_COLUMN] = ""
        rows.append(output)
    return pd.DataFrame(rows, columns=agent_columns + FINAL_REVIEW_AUDIT_COLUMNS), duplicate_map


def write_final_review_chunks(review_df: pd.DataFrame, output_dir: Path, chunk_size: int = 25) -> List[Path]:
    if chunk_size <= 0:
        raise ValueError("Final review chunk size must be positive.")
    output_dir.mkdir(parents=True, exist_ok=True)
    units: List[pd.DataFrame] = []
    consumed_groups = set()
    for index, row in review_df.iterrows():
        group_id = clean_text(row.get(DUPLICATE_GROUP_COLUMN))
        if not group_id:
            units.append(review_df.loc[[index]])
            continue
        if group_id in consumed_groups:
            continue
        consumed_groups.add(group_id)
        units.append(review_df[review_df[DUPLICATE_GROUP_COLUMN].map(clean_text) == group_id])

    chunks: List[pd.DataFrame] = []
    pending: List[pd.DataFrame] = []
    pending_rows = 0
    for unit in units:
        if pending and pending_rows + len(unit) > chunk_size:
            chunks.append(pd.concat(pending, ignore_index=True))
            pending, pending_rows = [], 0
        pending.append(unit)
        pending_rows += len(unit)
        # A duplicate group is atomic even when it is itself larger than the
        # nominal chunk size.
        if pending_rows >= chunk_size:
            chunks.append(pd.concat(pending, ignore_index=True))
            pending, pending_rows = [], 0
    if pending:
        chunks.append(pd.concat(pending, ignore_index=True))

    paths = []
    for chunk_id, chunk in enumerate(chunks, start=1):
        path = output_dir / f"08_final_review_input_part_{chunk_id:03d}.xlsx"
        chunk.to_excel(path, index=False)
        paths.append(path)
    return paths


def final_review_result_path(input_path: Path, result_dir: Path) -> Path:
    match = re.search(r"part_(\d+)$", input_path.stem)
    if not match:
        raise ValueError(f"Cannot parse final review chunk ID: {input_path}")
    return result_dir / f"08_final_review_result_part_{match.group(1)}.xlsx"


def validate_final_review_result(input_path: Path, result_path: Path, agent_columns: List[str]) -> Dict[str, Any]:
    if not result_path.exists():
        return {"status": "pending", "input_rows": "", "result_rows": "", "error": "result file missing"}
    try:
        input_df = pd.read_excel(input_path, dtype=object).fillna("")
        result_df = pd.read_excel(result_path, dtype=object).fillna("")
    except Exception as exc:
        return {"status": "invalid", "input_rows": "", "result_rows": "", "error": f"read failed: {exc}"}
    input_rows, result_rows = len(input_df), len(result_df)
    expected_columns = agent_columns + FINAL_REVIEW_AUDIT_COLUMNS
    if list(input_df.columns) != expected_columns or list(result_df.columns) != expected_columns:
        return {"status": "invalid", "input_rows": input_rows, "result_rows": result_rows, "error": "final review schema mismatch"}
    if input_rows != result_rows:
        return {"status": "invalid", "input_rows": input_rows, "result_rows": result_rows, "error": "row count mismatch"}
    if [clean_id(v) for v in input_df[ID_COLUMN]] != [clean_id(v) for v in result_df[ID_COLUMN]]:
        return {"status": "invalid", "input_rows": input_rows, "result_rows": result_rows, "error": "id order mismatch"}
    for column in IMMUTABLE_FINAL_REVIEW_COLUMNS:
        cleaner = clean_id if column in {ID_COLUMN, YEAR_COLUMN} else clean_text
        if [cleaner(v) for v in input_df[column]] != [cleaner(v) for v in result_df[column]]:
            return {"status": "invalid", "input_rows": input_rows, "result_rows": result_rows, "error": f"agent modified immutable input column: {column}"}
    # database_name and database_url are deliberately mutable so the result can
    # point at the canonical database and its current direct entry point.
    evidence_error = validate_conditional_evidence(result_df)
    if evidence_error:
        return {"status": "invalid", "input_rows": input_rows, "result_rows": result_rows, "error": evidence_error}
    decision_error = validate_final_decision_fields(result_df)
    if decision_error:
        return {"status": "invalid", "input_rows": input_rows, "result_rows": result_rows, "error": decision_error}
    errors = []
    for _, row in result_df.iterrows():
        row_id = clean_id(row.get(ID_COLUMN))
        members = {clean_id(v) for v in clean_text(row.get(DUPLICATE_MEMBERS_COLUMN)).split(";") if clean_id(v)}
        canonical = clean_text(row.get(CANONICAL_ID_COLUMN))
        representative = clean_id(row.get(REPRESENTATIVE_ID_COLUMN))
        visit = clean_text(row.get(AGENT_VISIT_STATUS_COLUMN)).lower()
        checked_url = clean_text(row.get(AGENT_CHECKED_URL_COLUMN))
        click_path = clean_text(row.get(AGENT_CLICK_PATH_COLUMN))
        statement = clean_text(row.get(AGENT_REVIEW_STATEMENT_COLUMN))
        accessibility = clean_text(row.get(ACCESSIBILITY_COLUMN)).lower()
        decision = clean_text(row.get(DECISION_COLUMN)).lower()
        if not canonical or representative not in members:
            errors.append(f"id {row_id}: invalid canonical/representative assignment")
        if visit not in FINAL_VISIT_STATUSES:
            errors.append(f"id {row_id}: invalid agent visit status")
        if accessibility == "live" and not re.fullmatch(r"https?://\S+", checked_url, flags=re.IGNORECASE):
            errors.append(f"id {row_id}: live row lacks agent-checked URL")
        if clean_text(row.get(AUTOMATIC_STATUS_COLUMN)).lower() == "continue_required" and not click_path:
            errors.append(f"id {row_id}: continue-required row lacks click path")
        if decision == "yes" and accessibility == "live" and visit not in {"entered_database", "restricted"}:
            errors.append(f"id {row_id}: live yes database was not entered")
        if (
            decision == "yes"
            and accessibility == "live"
            and clean_text(row.get(AUTOMATIC_STATUS_COLUMN)).lower() == "continue_required"
            and visit != "entered_database"
        ):
            errors.append(f"id {row_id}: continue-required yes database was not entered")
        if decision == "yes" and accessibility == "dead" and visit != "confirmed_dead":
            errors.append(f"id {row_id}: dead yes database was not confirmed")
        if len(statement) < 20:
            errors.append(f"id {row_id}: agent review statement too short")
    return {
        "status": "invalid" if errors else "complete",
        "input_rows": input_rows,
        "result_rows": result_rows,
        "error": "; ".join(errors[:8]),
    }


def scan_final_review_chunks(
    input_dir: Path,
    result_dir: Path,
    checkpoint_path: Path,
    agent_columns: List[str],
) -> pd.DataFrame:
    if checkpoint_path.exists():
        previous = pd.read_csv(checkpoint_path, dtype=object).fillna("")
        versions = {
            clean_text(value)
            for value in previous.get("finalization_policy_version", pd.Series(dtype=object)).tolist()
            if clean_text(value)
        }
        if versions and versions != {FINALIZATION_POLICY_VERSION}:
            raise ValueError(
                "Stage 8 final review checkpoint policy mismatch: "
                f"expected {FINALIZATION_POLICY_VERSION}, found {', '.join(sorted(versions))}."
            )
    input_paths = sorted(input_dir.glob("08_final_review_input_part_*.xlsx"))
    result_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for input_path in input_paths:
        result_path = final_review_result_path(input_path, result_dir)
        validation = validate_final_review_result(input_path, result_path, agent_columns)
        match = re.search(r"part_(\d+)$", input_path.stem)
        chunk_id = match.group(1) if match else ""
        rows.append(
            {
                "chunk_id": chunk_id,
                "input_path": str(input_path),
                "result_path": str(result_path),
                "status": validation["status"],
                "input_rows": validation.get("input_rows", ""),
                "result_rows": validation.get("result_rows", ""),
                "error": validation.get("error", ""),
                "completed_at": datetime.now().isoformat(timespec="seconds") if validation["status"] == "complete" else "",
                "finalization_policy_version": FINALIZATION_POLICY_VERSION,
            }
        )
    manifest = pd.DataFrame(rows, columns=FINAL_REVIEW_CHECKPOINT_COLUMNS)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    manifest.to_csv(checkpoint_path, index=False, encoding="utf-8")
    return manifest


def merge_final_review(
    raw_df: pd.DataFrame,
    manifest: pd.DataFrame,
    agent_columns: List[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not manifest.empty and (manifest["status"] != "complete").any():
        raise ValueError("Cannot finalize while final review chunks are incomplete or invalid.")
    result_frames = [pd.read_excel(path, dtype=object).fillna("") for path in manifest.get("result_path", [])]
    reviewed = pd.concat(result_frames, ignore_index=True) if result_frames else pd.DataFrame(columns=agent_columns + FINAL_REVIEW_AUDIT_COLUMNS)
    reviewed_by_id = {clean_id(row[ID_COLUMN]): row for _, row in reviewed.iterrows()}

    updated_rows = []
    for _, source in raw_df.iterrows():
        row_id = clean_id(source.get(ID_COLUMN))
        reviewed_row = reviewed_by_id.get(row_id)
        if reviewed_row is None:
            updated_rows.append({column: source.get(column, "") for column in agent_columns})
        else:
            updated_rows.append({column: reviewed_row.get(column, "") for column in agent_columns})
    updated = pd.DataFrame(updated_rows, columns=agent_columns)

    yes_ids = {
        clean_id(row[ID_COLUMN])
        for _, row in updated.iterrows()
        if clean_text(row.get(DECISION_COLUMN)).lower() == "yes"
    }
    canonical_by_id = {
        clean_id(row[ID_COLUMN]): clean_text(row.get(CANONICAL_ID_COLUMN))
        for _, row in reviewed.iterrows()
    }
    representative_by_id = {
        clean_id(row[ID_COLUMN]): clean_id(row.get(REPRESENTATIVE_ID_COLUMN))
        for _, row in reviewed.iterrows()
    }
    allowed_members_by_id = {
        clean_id(row[ID_COLUMN]): {
            clean_id(value)
            for value in clean_text(row.get(DUPLICATE_MEMBERS_COLUMN)).split(";")
            if clean_id(value)
        }
        for _, row in reviewed.iterrows()
    }
    missing_review = sorted(row_id for row_id in yes_ids if not canonical_by_id.get(row_id))
    if missing_review:
        raise ValueError(f"Every yes database must be agent-reviewed; missing IDs: {', '.join(missing_review[:8])}")

    canonical_groups: Dict[tuple[str, str], List[str]] = {}
    for row_id in yes_ids:
        reviewed_row = reviewed_by_id[row_id]
        candidate_group = clean_text(reviewed_row.get(DUPLICATE_GROUP_COLUMN)) or f"row:{row_id}"
        key = (candidate_group, canonical_by_id[row_id])
        canonical_groups.setdefault(key, []).append(row_id)
    remove_ids = set()
    audit_rows = []
    for (_, canonical), members in sorted(canonical_groups.items()):
        members = sorted(members, key=_sort_id)
        member_set = set(members)
        if any(not member_set.issubset(allowed_members_by_id.get(row_id, set())) for row_id in members):
            raise ValueError(f"Canonical group {canonical} merges rows outside its duplicate candidate group.")
        representatives = {representative_by_id[row_id] for row_id in members}
        if len(representatives) != 1:
            raise ValueError(f"Canonical group {canonical} has inconsistent representative IDs.")
        representative_id = representatives.pop()
        if representative_id not in members:
            raise ValueError(f"Canonical group {canonical} representative is not a group member.")
        original48 = any(
            clean_text(updated.loc[updated[ID_COLUMN].map(clean_id) == member, ORIGINAL_DATABASE_COLUMN].iloc[0]).lower() == "yes"
            for member in members
        )
        representative_mask = updated[ID_COLUMN].map(clean_id) == representative_id
        if original48:
            updated.loc[representative_mask, ORIGINAL_DATABASE_COLUMN] = "yes"
        removed = [member for member in members if member != representative_id]
        remove_ids.update(removed)
        audit_rows.append(
            {
                "canonical_id": canonical,
                "representative_id": representative_id,
                "member_ids": ";".join(members),
                "removed_ids": ";".join(removed),
                "member_count": len(members),
                "original_48_database": "yes" if original48 else "no",
            }
        )
    final_df = updated[~updated[ID_COLUMN].map(clean_id).isin(remove_ids)].copy()
    final_df["_sort_id"] = final_df[ID_COLUMN].map(_sort_id)
    final_df = final_df.sort_values("_sort_id").drop(columns=["_sort_id"]).reset_index(drop=True)
    final_df = normalize_neural_link_consistency(final_df)
    evidence_error = validate_conditional_evidence(final_df)
    if evidence_error:
        raise ValueError(f"Final evidence validation failed: {evidence_error}")
    decision_error = validate_final_decision_fields(final_df)
    if decision_error:
        raise ValueError(f"Final decision validation failed: {decision_error}")
    return final_df[agent_columns], pd.DataFrame(audit_rows)


def enrich_accessibility_audit(
    accessibility_df: pd.DataFrame,
    manifest: pd.DataFrame,
) -> pd.DataFrame:
    """Attach browser-review conclusions to the one-row-per-input URL audit.

    The automatic checker remains unchanged and auditable.  Agent conclusions
    are added in separate columns so a reader can compare the HTTP triage with
    the final browser visit, click path, and evidence decision.
    """
    if not manifest.empty and (manifest["status"] != "complete").any():
        raise ValueError("Cannot enrich accessibility audit while final review chunks are incomplete.")
    result_frames = [
        pd.read_excel(path, dtype=object).fillna("")
        for path in manifest.get("result_path", [])
    ]
    reviewed = pd.concat(result_frames, ignore_index=True) if result_frames else pd.DataFrame()
    if not reviewed.empty:
        reviewed_ids = reviewed[ID_COLUMN].map(clean_id)
        duplicates = reviewed_ids[reviewed_ids.duplicated()].unique().tolist()
        if duplicates:
            raise ValueError(
                "Final review contains duplicate IDs while enriching accessibility audit: "
                + ", ".join(str(value) for value in duplicates[:8])
            )
        reviewed_by_id = {
            clean_id(row[ID_COLUMN]): row
            for _, row in reviewed.iterrows()
        }
    else:
        reviewed_by_id = {}

    enriched = accessibility_df.copy()
    for column in ACCESSIBILITY_AGENT_AUDIT_COLUMNS:
        enriched[column] = ""
    mappings = {
        "agent_final_database_name": DATABASE_NAME_COLUMN,
        "agent_final_database_url": DATABASE_URL_COLUMN,
        "agent_final_accessibility": ACCESSIBILITY_COLUMN,
        "agent_final_db_type_confirmation": DECISION_COLUMN,
        "agent_final_evidence_url": EVIDENCE_URL_COLUMN,
        "agent_final_evidence_source_type": EVIDENCE_SOURCE_TYPE_COLUMN,
        "agent_final_canonical_id": CANONICAL_ID_COLUMN,
        "agent_final_representative_id": REPRESENTATIVE_ID_COLUMN,
        AGENT_VISIT_STATUS_COLUMN: AGENT_VISIT_STATUS_COLUMN,
        AGENT_CHECKED_URL_COLUMN: AGENT_CHECKED_URL_COLUMN,
        AGENT_CLICK_PATH_COLUMN: AGENT_CLICK_PATH_COLUMN,
        AGENT_REVIEW_STATEMENT_COLUMN: AGENT_REVIEW_STATEMENT_COLUMN,
    }
    for index, audit_row in enriched.iterrows():
        reviewed_row = reviewed_by_id.get(clean_id(audit_row.get(ID_COLUMN)))
        if reviewed_row is None:
            enriched.at[index, "agent_reviewed"] = "no"
            continue
        enriched.at[index, "agent_reviewed"] = "yes"
        for target, source in mappings.items():
            enriched.at[index, target] = reviewed_row.get(source, "")
    return enriched


def write_excel_with_hyperlinks(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="xlsxwriter", engine_kwargs={"options": {"strings_to_urls": False}}) as writer:
        df.to_excel(writer, index=False)
        worksheet = writer.sheets["Sheet1"]
        for column_name in (
            DATABASE_URL_COLUMN,
            EVIDENCE_URL_COLUMN,
            "original_url",
            "checked_url",
            "final_url",
            "evidence_original_url",
            "evidence_checked_url",
            "evidence_final_url",
            "agent_final_database_url",
            "agent_final_evidence_url",
            AGENT_CHECKED_URL_COLUMN,
        ):
            if column_name not in df.columns:
                continue
            column_index = df.columns.get_loc(column_name)
            for row_index, value in enumerate(df[column_name].tolist(), start=1):
                url = clean_text(value)
                if re.fullmatch(r"https?://\S+", url, flags=re.IGNORECASE):
                    worksheet.write_url(row_index, column_index, url, string=url)


def _sort_id(value: Any) -> tuple[int, Any]:
    text = clean_id(value)
    return (0, int(text)) if text.isdigit() else (1, text)


def _url_host(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    candidate = text if "://" in text else f"https://{text}"
    try:
        host = (urlsplit(candidate).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def write_finalization_summary(
    path: Path,
    raw_df: pd.DataFrame,
    final_df: Optional[pd.DataFrame],
    manifest: pd.DataFrame,
    duplicate_map: pd.DataFrame,
    input_path: Path,
) -> Dict[str, Any]:
    status_counts = manifest["status"].value_counts().to_dict() if "status" in manifest else {}
    summary: Dict[str, Any] = {
        "finalization_policy_version": FINALIZATION_POLICY_VERSION,
        "input_path": str(input_path),
        "input_sha256": _sha256(input_path),
        "input_rows": len(raw_df),
        "duplicate_candidate_groups": int(duplicate_map[DUPLICATE_GROUP_COLUMN].nunique()) if not duplicate_map.empty else 0,
        "duplicate_candidate_rows": len(duplicate_map),
        "review_chunks": len(manifest),
        "review_status_counts": {str(key): int(value) for key, value in status_counts.items()},
        "merge_ready": bool(manifest.empty or not (manifest["status"] != "complete").any()),
        "final_rows": len(final_df) if final_df is not None else 0,
    }
    if final_df is not None:
        for column, key in (
            (ACCESSIBILITY_COLUMN, "accessibility"),
            (DECISION_COLUMN, "db_type"),
            ("<main,t-word-tag> neural_link", "neural"),
            (EVIDENCE_SOURCE_TYPE_COLUMN, "evidence_source_type"),
        ):
            summary[f"final_{key}_counts"] = {
                str(name): int(count)
                for name, count in final_df[column].astype(str).value_counts(dropna=False).sort_index().items()
            }
        candidate_mask = (
            final_df[ACCESSIBILITY_COLUMN].astype(str).str.strip().str.lower().eq("live")
            & final_df[DECISION_COLUMN].astype(str).str.strip().str.lower().eq("yes")
            & final_df["<main,t-word-tag> neural_link"].astype(str).str.strip().str.lower().isin({"primary", "partial"})
        )
        summary["final_candidate_rows"] = int(candidate_mask.sum())
        summary["original_48_rows"] = int(
            final_df[ORIGINAL_DATABASE_COLUMN].astype(str).str.strip().str.lower().eq("yes").sum()
        )
        summary["original_48_candidate_overlap"] = int(
            (
                candidate_mask
                & final_df[ORIGINAL_DATABASE_COLUMN].astype(str).str.strip().str.lower().eq("yes")
            ).sum()
        )
        summary["removed_duplicate_rows"] = int(len(raw_df) - len(final_df))
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def initialize_raw_snapshot(input_path: Path, raw_path: Path) -> None:
    if raw_path.exists():
        return
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(input_path, raw_path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()
