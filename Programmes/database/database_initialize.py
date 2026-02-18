import sqlite3
from pathlib import Path
import os
import requests
import numpy as np
import pandas as pd
from tqdm import tqdm

# Build database from Excel so column names define the schema.
SCRIPT_DIR = Path(__file__).resolve().parent
EXCEL_PATH = SCRIPT_DIR / "data.xlsx"
DB_PATH = SCRIPT_DIR / "data.db"

if not EXCEL_PATH.exists():
    raise FileNotFoundError(f"Missing data source: {EXCEL_PATH}")

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

def get_semantic_scholar_citations(doi):
    paper_id = f"DOI:{doi}"
    url = f"https://api.semanticscholar.org/graph/v1/paper/{paper_id}"
    params = {"fields": "citationCount"}
    headers = {}
    api_key = os.getenv("S2_API_KEY")
    if api_key:
        headers["x-api-key"] = api_key
    response = requests.get(url, params=params, headers=headers, timeout=30)
    if response.status_code == 200:
        data = response.json()
        return data.get("citationCount", 0)
    return None

def parse_columns(columns):
    metadata = []
    for index, col in enumerate(columns):
        col_text = str(col)
        tags = []
        base_name = col_text.strip()
        if col_text.strip().startswith("<") and ">" in col_text:
            end = col_text.find(">")
            if end > 0:
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
            }
        )
    return metadata

def find_column_by_name(columns, target_name):
    target_lower = target_name.strip().lower()
    for col in columns:
        if str(col).strip().lower() == target_lower:
            return col
    return None

def check_url_accessible(url):
    if pd.isna(url) or not str(url).strip():
        return pd.NA
    url_text = str(url).strip()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        )
    }
    if not url_text.lower().startswith(("http://", "https://")):
        candidates = [f"https://{url_text}", f"http://{url_text}"]
    else:
        candidates = [url_text]
    for candidate in candidates:
        for _ in range(2):
            try:
                response = requests.head(
                    candidate, allow_redirects=True, timeout=20, headers=headers
                )
                if response.status_code < 400:
                    return True
                response = requests.get(
                    candidate,
                    allow_redirects=True,
                    timeout=10,
                    stream=True,
                    headers=headers,
                )
                if response.status_code < 400:
                    return True
            except requests.exceptions.SSLError:
                try:
                    response = requests.get(
                        candidate,
                        allow_redirects=True,
                        timeout=20,
                        stream=True,
                        headers=headers,
                        verify=False,
                    )
                    if response.status_code < 400:
                        return True
                except Exception:
                    continue
            except Exception:
                continue
    return False

def normalize_bool(value):
    if pd.isna(value):
        return pd.NA
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not pd.isna(value):
        return bool(int(value))
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y"}:
        return True
    if text in {"0", "false", "f", "no", "n"}:
        return False
    return pd.NA

print("Step 1/6: Load Excel data")
df_raw = pd.read_excel(str(EXCEL_PATH))

column_metadata = parse_columns(df_raw.columns)
base_names = [meta["column_name"] for meta in column_metadata]
duplicate_names = [
    name for name in set(base_names) if name and base_names.count(name) > 1
]
if duplicate_names:
    raise ValueError(f"Duplicate column names after prefix removal: {duplicate_names}")

rename_map = {
    meta["original_name"]: meta["column_name"] for meta in column_metadata
}
df_data = df_raw.rename(columns=rename_map)

total_rows = len(df_data)
doi_meta = next(
    (meta for meta in column_metadata if meta.get("data_type") == TAG_WORD_DOI), None
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
doi_count = (
    int(df_data[doi_column].notna().sum()) if doi_column else 0
)
success_count = 0
failed_count = 0
url_reachable_count = 0
url_unreachable_count = 0
url_missing_count = 0

print("Step 2/6: Update citation counts")
if citation_column and doi_column:
    df_data[citation_column] = np.nan
    df_raw[citation_meta["original_name"]] = np.nan
    for index, doi in tqdm(
        df_data[doi_column].items(),
        total=doi_count if doi_count else None,
        desc="Updating citations",
    ):
        if pd.isna(doi) or not str(doi).strip():
            df_data.at[index, citation_column] = np.nan
            df_raw.at[index, citation_meta["original_name"]] = np.nan
            failed_count += 1
            continue
        try:
            citation = get_semantic_scholar_citations(str(doi).strip())
            df_data.at[index, citation_column] = (
                citation if citation is not None else np.nan
            )
            df_raw.at[index, citation_meta["original_name"]] = (
                citation if citation is not None else np.nan
            )
            if citation is not None:
                success_count += 1
            else:
                failed_count += 1
        except Exception:
            df_data.at[index, citation_column] = np.nan
            df_raw.at[index, citation_meta["original_name"]] = np.nan
            failed_count += 1

print("Step 3/6: Check URL accessibility")
url_meta = next(
    (meta for meta in column_metadata if meta.get("data_type") == TAG_WORD_URL), None
)
url_column = url_meta["column_name"] if url_meta else None
if url_column and access_column:
    df_data[access_column] = pd.Series([pd.NA] * len(df_data), dtype="boolean")
    df_raw[access_meta["original_name"]] = pd.Series([pd.NA] * len(df_raw), dtype="boolean")
    for index, url in tqdm(
        df_data[url_column].items(),
        total=df_data[url_column].notna().sum(),
        desc="Checking URLs",
    ):
        status = check_url_accessible(url)
        df_data.at[index, access_column] = status
        df_raw.at[index, access_meta["original_name"]] = status
        if pd.isna(status):
            url_missing_count += 1
        elif status:
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
for col in tag_columns:
    if col not in df_data.columns:
        continue
    for value in df_data[col].dropna():
        for tag in str(value).split(";"):
            tag_text = tag.strip()
            if tag_text:
                tag_values.add(tag_text)
print(f"Tags detected: {len(tag_values)}")

print("Step 3.5/6: Normalize column types")
numeric_columns = [
    meta["column_name"]
    for meta in column_metadata
    if meta.get("data_type") in {TAG_NUMERIC, TAG_NUMERIC_CITE}
]
bool_columns = [
    meta["column_name"]
    for meta in column_metadata
    if meta.get("data_type") in {TAG_BOOL, TAG_BOOL_ACCESS}
]
for col in numeric_columns:
    if col in df_data.columns:
        df_data[col] = pd.to_numeric(df_data[col], errors="coerce")
    if col in df_raw.columns:
        df_raw[col] = pd.to_numeric(df_raw[col], errors="coerce")
for col in bool_columns:
    if col in df_data.columns:
        df_data[col] = df_data[col].map(normalize_bool).astype("boolean")
    if col in df_raw.columns:
        df_raw[col] = df_raw[col].map(normalize_bool).astype("boolean")

for idx, meta in enumerate(column_metadata):
    meta["order_index"] = idx

print("Step 4/6: Update Excel citation values")
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
df_data.to_sql("database_info", conn, if_exists="replace", index=False)
display_df.to_sql("display_columns", conn, if_exists="replace", index=False)
conn.close()

print("Step 6/6: Generate report")
report_lines = [
    "Database Initialization Report",
    "-" * 32,
    f"Source Excel   : {EXCEL_PATH}",
    f"Database path  : {DB_PATH}",
    "Table          : database_info",
    f"Rows imported  : {total_rows}",
    f"DOI entries    : {doi_count}",
    f"Citations      : {success_count} success, {failed_count} failed",
    f"URLs           : {url_reachable_count} reachable, {url_unreachable_count} unreachable, {url_missing_count} missing",
    "Columns        :",
]
report_lines.extend([f"  - {col}" for col in df_data.columns])
print("\n".join(report_lines))