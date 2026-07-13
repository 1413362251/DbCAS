import sqlite3
from pathlib import Path
import os
import re
import sys
import time
from urllib.parse import urlsplit

import requests
import numpy as np
import pandas as pd
from tqdm import tqdm

PROGRAMMES_DIR = Path(__file__).resolve().parents[1]
if str(PROGRAMMES_DIR) not in sys.path:
    sys.path.insert(0, str(PROGRAMMES_DIR))

from scripts.check_url_accessibility import check_urls

# Build database from Excel so column names define the schema.
SCRIPT_DIR = Path(__file__).resolve().parent
EXCEL_PATH = SCRIPT_DIR / "data.xlsx"
DB_PATH = SCRIPT_DIR / "data.db"

TAG_MAIN = "main"
TAG_SUB = "sub"
TAG_NUMERIC = "t-numeric"
TAG_NUMERIC_CITE = "t-numeric-cite"
TAG_WORD = "t-word"
TAG_WORD_TAG = "t-word-tag"
TAG_WORD_URL = "t-word-url"
TAG_WORD_DOI = "t-word-doi"
TAG_BOOL = "t-bool"
TAG_BOOL_ACCESS = "t-bool-access"

DATA_TYPE_TAGS = {
    TAG_NUMERIC,
    TAG_NUMERIC_CITE,
    TAG_WORD,
    TAG_WORD_TAG,
    TAG_WORD_URL,
    TAG_WORD_DOI,
    TAG_BOOL,
    TAG_BOOL_ACCESS,
}

POSITION_TAGS = {TAG_MAIN, TAG_SUB}
MISSING_TEXT_VALUES = {"", "unknown", "n/a", "na", "none", "nan", "<na>", "-"}
SNAKE_CASE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
SEMANTIC_SCHOLAR_BATCH_SIZE = 500
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

EXPECTED_DISPLAY_SCHEMA = [
    ("database_name", TAG_MAIN, TAG_WORD),
    ("database_url", TAG_MAIN, TAG_WORD_URL),
    ("accessibility", TAG_MAIN, TAG_BOOL_ACCESS),
    ("year", TAG_MAIN, TAG_NUMERIC),
    ("citation", TAG_MAIN, TAG_NUMERIC_CITE),
    ("species", TAG_MAIN, TAG_WORD_TAG),
    ("tissue_or_brain_region", TAG_MAIN, TAG_WORD_TAG),
    ("sequencing_resolution", TAG_MAIN, TAG_WORD_TAG),
    ("read_technology", TAG_MAIN, TAG_WORD_TAG),
    ("classification_code", TAG_MAIN, TAG_WORD_TAG),
    ("title", TAG_SUB, TAG_WORD),
    ("doi", TAG_SUB, TAG_WORD_DOI),
    ("disease_association", TAG_SUB, TAG_WORD_TAG),
    ("developmental_association", TAG_SUB, TAG_WORD_TAG),
    ("cell_type", TAG_SUB, TAG_WORD_TAG),
    ("description", TAG_SUB, TAG_WORD),
]
EXPECTED_DISPLAY_BY_NAME = {
    name: (position_tag, data_type)
    for name, position_tag, data_type in EXPECTED_DISPLAY_SCHEMA
}


def is_blank(value):
    return pd.isna(value) or not str(value).strip()


def is_missing_text(value):
    if pd.isna(value):
        return True
    return str(value).strip().casefold() in MISSING_TEXT_VALUES


def normalize_doi_token(value):
    if is_missing_text(value):
        return ""
    token = str(value).strip()
    token = re.sub(r"^doi:\s*", "", token, flags=re.IGNORECASE)
    token = re.sub(
        r"^https?://(?:dx\.)?doi\.org/",
        "",
        token,
        flags=re.IGNORECASE,
    )
    return token.strip()


def split_doi_values(value):
    if is_missing_text(value):
        return []
    dois = []
    seen = set()
    for part in str(value).split(";"):
        doi = normalize_doi_token(part)
        key = doi.casefold()
        if doi and key not in seen:
            seen.add(key)
            dois.append(doi)
    return dois


def normalize_doi_value(value):
    dois = split_doi_values(value)
    return ";".join(dois) if dois else pd.NA


def normalize_tag_value(value):
    if pd.isna(value) or not str(value).strip():
        return pd.NA
    tags = []
    seen = set()
    for part in str(value).split(";"):
        tag = part.strip()
        key = tag.casefold()
        if tag and key not in seen:
            seen.add(key)
            tags.append(tag)
    return ";".join(tags) if tags else pd.NA


def normalize_url_value(value):
    if is_missing_text(value):
        return pd.NA
    return str(value).strip()


def get_max_citation_count(value, citation_fetcher):
    citation_counts = []
    for doi in split_doi_values(value):
        citation = citation_fetcher(doi)
        if citation is not None:
            citation_counts.append(int(citation))
    return max(citation_counts) if citation_counts else None


def accessibility_from_result(result):
    accessible = result.get("accessible")
    return False if accessible is None else bool(accessible)


def get_semantic_scholar_citations(doi, session=None, max_attempts=3):
    paper_id = f"DOI:{doi}"
    url = f"https://api.semanticscholar.org/graph/v1/paper/{paper_id}"
    params = {"fields": "citationCount"}
    headers = {}
    api_key = os.getenv("S2_API_KEY")
    if api_key:
        headers["x-api-key"] = api_key
    client = session or requests
    for attempt in range(max_attempts):
        try:
            response = client.get(url, params=params, headers=headers, timeout=30)
        except requests.RequestException:
            if attempt + 1 == max_attempts:
                return None
            time.sleep(2**attempt)
            continue
        if response.status_code == 200:
            try:
                citation_count = response.json().get("citationCount")
                return int(citation_count) if citation_count is not None else None
            except (TypeError, ValueError, KeyError):
                return None
        if response.status_code not in RETRYABLE_STATUS_CODES:
            return None
        if attempt + 1 < max_attempts:
            time.sleep(2**attempt)
    return None


def get_semantic_scholar_batch_citations(
    dois,
    session=None,
    max_attempts=3,
    batch_size=SEMANTIC_SCHOLAR_BATCH_SIZE,
):
    if batch_size <= 0 or batch_size > SEMANTIC_SCHOLAR_BATCH_SIZE:
        raise ValueError(
            f"batch_size must be between 1 and {SEMANTIC_SCHOLAR_BATCH_SIZE}"
        )

    unique_dois = []
    seen = set()
    for value in dois:
        doi = normalize_doi_token(value)
        key = doi.casefold()
        if doi and key not in seen:
            seen.add(key)
            unique_dois.append(doi)

    citations = {doi.casefold(): None for doi in unique_dois}
    if not unique_dois:
        return citations

    url = "https://api.semanticscholar.org/graph/v1/paper/batch"
    params = {"fields": "citationCount"}
    headers = {}
    api_key = os.getenv("S2_API_KEY")
    if api_key:
        headers["x-api-key"] = api_key
    client = session or requests

    for start in range(0, len(unique_dois), batch_size):
        batch = unique_dois[start : start + batch_size]
        payload = None
        for attempt in range(max_attempts):
            try:
                response = client.post(
                    url,
                    params=params,
                    headers=headers,
                    json={"ids": [f"DOI:{doi}" for doi in batch]},
                    timeout=30,
                )
            except requests.RequestException:
                if attempt + 1 < max_attempts:
                    time.sleep(2**attempt)
                continue

            if response.status_code == 200:
                try:
                    response_payload = response.json()
                    if (
                        isinstance(response_payload, list)
                        and len(response_payload) == len(batch)
                    ):
                        payload = response_payload
                except (TypeError, ValueError):
                    payload = None
                break
            if response.status_code not in RETRYABLE_STATUS_CODES:
                break
            if attempt + 1 < max_attempts:
                time.sleep(2**attempt)

        if payload is None:
            continue

        unresolved = []
        for doi, item in zip(batch, payload):
            citation_count = (
                item.get("citationCount") if isinstance(item, dict) else None
            )
            try:
                citations[doi.casefold()] = int(citation_count)
            except (TypeError, ValueError):
                unresolved.append(doi)

        for doi in unresolved:
            citations[doi.casefold()] = get_semantic_scholar_citations(
                doi, session=client
            )

    return citations


def parse_columns(columns):
    metadata = []
    for index, col in enumerate(columns):
        col_text = str(col)
        tags = []
        base_name = col_text.strip()
        tag_position = "hidden"
        if col_text.strip().startswith("<") and ">" in col_text:
            end = col_text.find(">")
            if end > 0:
                tag_position = "prefix"
                tag_text = col_text[1:end]
                base_name = col_text[end + 1 :].strip()
                tags = [
                    tag.strip().lower()
                    for tag in tag_text.split(",")
                    if tag.strip()
                ]
        elif "<" in col_text and ">" in col_text:
            start = col_text.rfind("<")
            end = col_text.rfind(">")
            if start < end:
                tag_position = "suffix"
                base_name = col_text[:start].strip()
                tag_text = col_text[start + 1 : end]
                tags = [
                    tag.strip().lower()
                    for tag in tag_text.split(",")
                    if tag.strip()
                ]
        display_group = "hidden"
        if TAG_MAIN in tags:
            display_group = "main"
        elif TAG_SUB in tags:
            display_group = "expand"

        data_type = next((tag for tag in tags if tag in DATA_TYPE_TAGS), None)
        is_access = int(data_type == TAG_BOOL_ACCESS)
        is_citation = int(data_type == TAG_NUMERIC_CITE)

        metadata.append(
            {
                "original_name": col_text,
                "column_name": base_name,
                "display_group": display_group,
                "order_index": index,
                "is_citation": is_citation,
                "is_access": is_access,
                "data_type": data_type,
                "tags": tags,
                "tag_position": tag_position,
            }
        )
    return metadata

def find_column_by_name(columns, target_name):
    target_lower = target_name.strip().lower()
    for col in columns:
        if str(col).strip().lower() == target_lower:
            return col
    return None

def normalize_bool(value):
    if pd.isna(value):
        return pd.NA
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not pd.isna(value):
        if float(value) == 1:
            return True
        if float(value) == 0:
            return False
        return pd.NA
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y"}:
        return True
    if text in {"0", "false", "f", "no", "n"}:
        return False
    return pd.NA


def validate_schema(column_metadata):
    errors = []
    base_names = [meta["column_name"] for meta in column_metadata]
    duplicate_names = sorted(
        {name for name in base_names if name and base_names.count(name) > 1}
    )
    if duplicate_names:
        errors.append(
            f"duplicate column names after tag removal: {', '.join(duplicate_names)}"
        )

    actual_display_schema = []
    hidden_seen = False
    for meta in column_metadata:
        original_name = meta["original_name"]
        column_name = meta["column_name"]
        tags = meta["tags"]
        if not column_name:
            errors.append(f"empty column name in header {original_name!r}")
            continue
        if not SNAKE_CASE_PATTERN.fullmatch(column_name):
            errors.append(f"column name is not snake_case: {original_name!r}")

        if not tags:
            hidden_seen = True
            if "<" in original_name or ">" in original_name:
                errors.append(f"malformed tag syntax: {original_name!r}")
            if column_name == "original_48_database":
                errors.append("original_48_database must be removed")
            continue

        if hidden_seen:
            errors.append(f"display column appears after a hidden column: {original_name!r}")
        if meta["tag_position"] != "prefix":
            errors.append(f"tags must be a header prefix: {original_name!r}")

        position_tags = [tag for tag in tags if tag in POSITION_TAGS]
        data_type_tags = [tag for tag in tags if tag in DATA_TYPE_TAGS]
        unknown_tags = [
            tag for tag in tags if tag not in POSITION_TAGS and tag not in DATA_TYPE_TAGS
        ]
        if len(tags) != 2 or len(position_tags) != 1 or len(data_type_tags) != 1:
            errors.append(
                f"tagged header requires exactly one position and one data type: {original_name!r}"
            )
            continue
        if unknown_tags:
            errors.append(
                f"unknown tags in {original_name!r}: {', '.join(unknown_tags)}"
            )
            continue

        position_tag = position_tags[0]
        data_type = data_type_tags[0]
        actual_display_schema.append((column_name, position_tag, data_type))
        expected = EXPECTED_DISPLAY_BY_NAME.get(column_name)
        if expected is None:
            errors.append(
                f"unapproved display column must be hidden instead: {original_name!r}"
            )
        elif expected != (position_tag, data_type):
            errors.append(
                f"incorrect tags for {column_name}: expected <{expected[0]},{expected[1]}>"
            )

    if actual_display_schema != EXPECTED_DISPLAY_SCHEMA:
        actual_names = [name for name, _, _ in actual_display_schema]
        expected_names = [name for name, _, _ in EXPECTED_DISPLAY_SCHEMA]
        errors.append(
            "display columns are missing, extra, or out of order; "
            f"expected {expected_names}, got {actual_names}"
        )
    if "id" not in base_names:
        errors.append("required primary key column is missing: id")

    if errors:
        raise ValueError(_format_validation_errors(errors))


def _row_context(row_number, row):
    row_id = row.get("id")
    id_text = "missing" if is_blank(row_id) else str(row_id).strip()
    return f"Excel row {row_number}, id={id_text}"


def validate_data(column_metadata, df_data):
    errors = []
    if "id" in df_data.columns:
        seen_ids = {}
        for position, (_, row) in enumerate(df_data.iterrows(), start=2):
            row_id = row.get("id")
            if is_blank(row_id):
                errors.append(f"{_row_context(position, row)}: id is blank")
                continue
            id_key = str(row_id).strip()
            if id_key in seen_ids:
                errors.append(
                    f"{_row_context(position, row)}: duplicate id; first seen at Excel row {seen_ids[id_key]}"
                )
            else:
                seen_ids[id_key] = position

    for position, (_, row) in enumerate(df_data.iterrows(), start=2):
        context = _row_context(position, row)
        for meta in column_metadata:
            column_name = meta["column_name"]
            if column_name not in df_data.columns:
                continue
            value = row.get(column_name)
            data_type = meta["data_type"]
            if data_type == TAG_WORD_URL and not is_blank(value):
                try:
                    parsed = urlsplit(str(value).strip())
                    valid_url = parsed.scheme.lower() in {"http", "https"} and bool(
                        parsed.hostname
                    )
                except ValueError:
                    valid_url = False
                if not valid_url:
                    errors.append(f"{context}: {column_name} is not a valid HTTP(S) URL")
            elif data_type == TAG_WORD_DOI:
                for doi in split_doi_values(value):
                    if not DOI_PATTERN.fullmatch(doi):
                        errors.append(f"{context}: invalid DOI {doi!r}")
            elif data_type in {TAG_NUMERIC, TAG_NUMERIC_CITE} and not is_blank(value):
                if pd.isna(pd.to_numeric(value, errors="coerce")):
                    errors.append(f"{context}: {column_name} is not numeric")
            elif data_type in {TAG_BOOL, TAG_BOOL_ACCESS} and not is_blank(value):
                if pd.isna(normalize_bool(value)):
                    errors.append(f"{context}: {column_name} is not a recognized boolean")
            elif data_type == TAG_WORD_TAG and not is_blank(value):
                value_text = str(value)
                if any(separator in value_text for separator in (",", "；", "|")):
                    errors.append(
                        f"{context}: {column_name} uses a forbidden tag separator"
                    )

    if errors:
        raise ValueError(_format_validation_errors(errors))


def _format_validation_errors(errors, limit=50):
    shown = errors[:limit]
    lines = ["Workbook validation failed:"]
    lines.extend(f"- {error}" for error in shown)
    if len(errors) > limit:
        lines.append(f"- ... {len(errors) - limit} more error(s)")
    return "\n".join(lines)


def normalize_loader_values(df_raw, df_data, column_metadata):
    changed_tag_cells = 0
    changed_doi_cells = 0
    changed_url_cells = 0

    def comparable(value):
        return "" if pd.isna(value) else str(value)

    for meta in column_metadata:
        column_name = meta["column_name"]
        original_name = meta["original_name"]
        data_type = meta["data_type"]
        if data_type == TAG_WORD_TAG:
            before = df_data[column_name].copy()
            after = before.map(normalize_tag_value)
            changed_tag_cells += sum(
                comparable(old) != comparable(new)
                for old, new in zip(before.tolist(), after.tolist())
            )
        elif data_type == TAG_WORD_DOI:
            before = df_data[column_name].copy()
            after = before.map(normalize_doi_value)
            changed_doi_cells += sum(
                comparable(old) != comparable(new)
                for old, new in zip(before.tolist(), after.tolist())
            )
        elif data_type == TAG_WORD_URL:
            before = df_data[column_name].copy()
            after = before.map(normalize_url_value)
            changed_url_cells += sum(
                comparable(old) != comparable(new)
                for old, new in zip(before.tolist(), after.tolist())
            )
        else:
            continue
        df_data[column_name] = after
        df_raw[original_name] = after

    return {
        "tag_cells": changed_tag_cells,
        "doi_cells": changed_doi_cells,
        "url_cells": changed_url_cells,
    }

def main():
    if not EXCEL_PATH.exists():
        raise FileNotFoundError(f"Missing data source: {EXCEL_PATH}")

    print("Step 1/6: Load, normalize, and validate Excel data")
    df_raw = pd.read_excel(str(EXCEL_PATH))
    column_metadata = parse_columns(df_raw.columns)
    validate_schema(column_metadata)

    rename_map = {
        meta["original_name"]: meta["column_name"] for meta in column_metadata
    }
    df_data = df_raw.rename(columns=rename_map)
    normalized_counts = normalize_loader_values(df_raw, df_data, column_metadata)
    validate_data(column_metadata, df_data)

    total_rows = len(df_data)
    doi_meta = next(
        (
            meta
            for meta in column_metadata
            if meta.get("data_type") == TAG_WORD_DOI
        ),
        None,
    )
    doi_column = doi_meta["column_name"] if doi_meta else None
    citation_meta = next(
        (meta for meta in column_metadata if meta["is_citation"] == 1), None
    )
    citation_column = citation_meta["column_name"] if citation_meta else None
    access_meta = next(
        (meta for meta in column_metadata if meta["is_access"] == 1), None
    )
    access_column = access_meta["column_name"] if access_meta else None
    url_meta = next(
        (
            meta
            for meta in column_metadata
            if meta.get("data_type") == TAG_WORD_URL
        ),
        None,
    )
    url_column = url_meta["column_name"] if url_meta else None

    doi_count = int(df_data[doi_column].notna().sum()) if doi_column else 0
    multi_doi_count = (
        sum(len(split_doi_values(value)) > 1 for value in df_data[doi_column])
        if doi_column
        else 0
    )
    citation_success_count = 0
    citation_failed_count = 0
    citation_missing_count = total_rows - doi_count
    url_reachable_count = 0
    url_unreachable_count = 0
    url_missing_count = 0

    print("Step 2/6: Update citation counts")
    if citation_column and doi_column:
        df_data[citation_column] = np.nan
        df_raw[citation_meta["original_name"]] = np.nan
        unique_dois = []
        seen_dois = set()
        for value in df_data[doi_column].dropna():
            for doi in split_doi_values(value):
                cache_key = doi.casefold()
                if cache_key not in seen_dois:
                    seen_dois.add(cache_key)
                    unique_dois.append(doi)

        with requests.Session() as session:
            citation_cache = get_semantic_scholar_batch_citations(
                unique_dois, session=session
            )

            for index, doi in tqdm(
                df_data[doi_column].items(),
                total=total_rows,
                desc="Applying citations",
            ):
                if is_blank(doi):
                    continue
                citation = get_max_citation_count(
                    doi,
                    lambda value: citation_cache.get(value.casefold()),
                )
                value = citation if citation is not None else np.nan
                df_data.at[index, citation_column] = value
                df_raw.at[index, citation_meta["original_name"]] = value
                if citation is None:
                    citation_failed_count += 1
                else:
                    citation_success_count += 1

    print("Step 3/6: Check URL accessibility")
    if url_column and access_column:
        df_data[access_column] = pd.Series(
            [False] * len(df_data), dtype="boolean", index=df_data.index
        )
        df_raw[access_meta["original_name"]] = pd.Series(
            [False] * len(df_raw), dtype="boolean", index=df_raw.index
        )
        url_items = list(df_data[url_column].items())
        url_results = check_urls([url for _, url in url_items])
        for (index, url), result in tqdm(
            zip(url_items, url_results),
            total=len(url_items),
            desc="Checking URLs",
        ):
            accessible = accessibility_from_result(result)
            df_data.at[index, access_column] = accessible
            df_raw.at[index, access_meta["original_name"]] = accessible
            if is_blank(url) or result.get("status") == "missing":
                url_missing_count += 1
            elif accessible:
                url_reachable_count += 1
            else:
                url_unreachable_count += 1

    print("Step 3.25/6: Count tag values")
    tag_columns = [
        meta["column_name"]
        for meta in column_metadata
        if meta.get("data_type") == TAG_WORD_TAG
    ]
    tag_values = set()
    for column_name in tag_columns:
        for value in df_data[column_name].dropna():
            tag_values.update(split for split in str(value).split(";") if split)
    print(f"Tags detected: {len(tag_values)}")

    print("Step 3.5/6: Normalize column types")
    for meta in column_metadata:
        column_name = meta["column_name"]
        original_name = meta["original_name"]
        data_type = meta.get("data_type")
        if data_type in {TAG_NUMERIC, TAG_NUMERIC_CITE}:
            df_data[column_name] = pd.to_numeric(
                df_data[column_name], errors="coerce"
            )
            df_raw[original_name] = pd.to_numeric(
                df_raw[original_name], errors="coerce"
            )
        elif data_type in {TAG_BOOL, TAG_BOOL_ACCESS}:
            df_data[column_name] = (
                df_data[column_name].map(normalize_bool).astype("boolean")
            )
            df_raw[original_name] = (
                df_raw[original_name].map(normalize_bool).astype("boolean")
            )

    for index, meta in enumerate(column_metadata):
        meta["order_index"] = index

    print("Step 4/6: Update Excel citation and normalized values")
    df_raw.to_excel(str(EXCEL_PATH), index=False)

    print("Step 5/6: Write to SQLite database")
    display_records = [
        {
            "column_name": meta["column_name"],
            "display_name": meta["column_name"],
            "display_group": meta["display_group"],
            "order_index": meta["order_index"],
            "is_citation": meta["is_citation"],
            "is_access": meta["is_access"],
            "data_type": meta["data_type"],
        }
        for meta in column_metadata
    ]
    display_df = pd.DataFrame(display_records)

    conn = sqlite3.connect(str(DB_PATH))
    try:
        df_data.to_sql("database_info", conn, if_exists="replace", index=False)
        display_df.to_sql("display_columns", conn, if_exists="replace", index=False)
        conn.commit()
    finally:
        conn.close()

    print("Step 6/6: Generate report")
    report_lines = [
        "Database Initialization Report",
        "-" * 32,
        f"Source Excel   : {EXCEL_PATH}",
        f"Database path  : {DB_PATH}",
        "Table          : database_info",
        f"Rows imported  : {total_rows}",
        f"DOI rows       : {doi_count} ({multi_doi_count} multi-DOI, {citation_missing_count} missing)",
        f"Citations      : {citation_success_count} success, {citation_failed_count} failed, {citation_missing_count} missing",
        f"URLs           : {url_reachable_count} reachable, {url_unreachable_count} unreachable, {url_missing_count} missing (stored inaccessible)",
        f"Normalized     : {normalized_counts['tag_cells']} tag cells, {normalized_counts['doi_cells']} DOI cells, {normalized_counts['url_cells']} URL cells",
        "Columns        :",
    ]
    report_lines.extend([f"  - {column}" for column in df_data.columns])
    print("\n".join(report_lines))


if __name__ == "__main__":
    main()
