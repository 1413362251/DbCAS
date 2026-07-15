from pathlib import Path
import threading
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd
import requests
from tqdm.auto import tqdm

from pipeline_runtime import (
    PipelineState,
    RateLimiter,
    append_csv_row,
    clean_str,
    read_csv_rows,
    to_int,
)


EUROPE_PMC_SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
EUROPE_PMC_REFERENCES_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/MED/{pmid}/references"
EUROPE_PMC_ARTICLE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/article/{source}/{ref_id}"
EUROPE_PMC_MAX_PAGE_SIZE = 1000

TARGET_COLUMNS = [
    "title",
    "keywordList",
    "web",
    "pubYear",
    "journal",
    "citedByCount",
    "pmid",
    "pmcid",
    "source",
    "doi",
    "abstractText",
]

SEED_CHECKPOINT_COLUMNS = ["search_year"] + TARGET_COLUMNS
REFERENCE_COLUMNS = ["seed_pmid", "ref_source", "ref_id", "ref_title", "ref_doi", "ref_pubYear"]
DETAIL_COLUMNS = TARGET_COLUMNS
DETAIL_PREFETCH_MULTIPLIER = 4
DETAIL_BATCH_SIZE = 1


def _raise_for_status(response: Any) -> None:
    if hasattr(response, "raise_for_status"):
        response.raise_for_status()


def search_epmc_page(
    query: str,
    page_size: int = 100,
    sort_by: str = "",
    cursor_mark: Optional[str] = None,
    session: Optional[requests.Session] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    http = session or requests.Session()
    params = {
        "query": query,
        "format": "json",
        "pageSize": max(1, min(int(page_size or 1), EUROPE_PMC_MAX_PAGE_SIZE)),
        "synonym": "FALSE",
        "resultType": "core",
        "sort": sort_by,
    }
    if cursor_mark is not None:
        params["cursorMark"] = cursor_mark
    response = http.get(EUROPE_PMC_SEARCH_URL, params=params, timeout=timeout)
    _raise_for_status(response)
    return response.json()


def search_epmc(
    query: str,
    page_size: int = 100,
    sort_by: str = "",
    session: Optional[requests.Session] = None,
    timeout: int = 30,
) -> List[Dict[str, Any]]:
    data = search_epmc_page(
        query,
        page_size=page_size,
        sort_by=sort_by,
        session=session,
        timeout=timeout,
    )
    return data.get("resultList", {}).get("result", []) or []


def search_epmc_paginated(
    query: str,
    page_size: int = 100,
    sort_by: str = "",
    max_results: Optional[int] = None,
    session: Optional[requests.Session] = None,
    timeout: int = 30,
) -> List[Dict[str, Any]]:
    http = session or requests.Session()
    articles: List[Dict[str, Any]] = []
    cursor_mark = "*"
    seen_cursors: set[str] = set()

    while True:
        if max_results is not None and len(articles) >= max_results:
            break
        data = search_epmc_page(
            query,
            page_size=page_size,
            sort_by=sort_by,
            cursor_mark=cursor_mark,
            session=http,
            timeout=timeout,
        )
        page_articles = data.get("resultList", {}).get("result", []) or []
        if not page_articles:
            break
        if max_results is not None:
            remaining = max(0, max_results - len(articles))
            page_articles = page_articles[:remaining]
        articles.extend(page_articles)

        next_cursor = clean_str(data.get("nextCursorMark", ""))
        if not next_cursor or next_cursor == cursor_mark or next_cursor in seen_cursors:
            break
        seen_cursors.add(cursor_mark)
        cursor_mark = next_cursor
    return articles


def safe_journal_title(article: Dict[str, Any]) -> str:
    journal_info = article.get("journalInfo", {})
    if isinstance(journal_info, dict):
        journal = journal_info.get("journal", {})
        if isinstance(journal, dict):
            return clean_str(journal.get("title", ""))
    return ""


def article_to_record(article: Dict[str, Any]) -> Dict[str, Any]:
    doi = clean_str(article.get("doi", ""))
    return {
        "title": clean_str(article.get("title", "")),
        "keywordList": article.get("keywordList", ""),
        "web": f"https://doi.org/{doi}" if doi else "",
        "pubYear": clean_str(article.get("pubYear", "")),
        "journal": safe_journal_title(article),
        "citedByCount": to_int(article.get("citedByCount", 0)),
        "pmid": clean_str(article.get("pmid", "")),
        "pmcid": clean_str(article.get("pmcid", "")),
        "source": clean_str(article.get("source", "")),
        "doi": doi,
        "abstractText": clean_str(article.get("abstractText", "")),
    }


def dedupe_seed_articles(
    rows: Iterable[Dict[str, Any]],
    max_rows: Optional[int] = None,
    dedupe_order: str = "citations",
) -> pd.DataFrame:
    df = pd.DataFrame(list(rows))
    if df.empty:
        return pd.DataFrame(columns=TARGET_COLUMNS)
    for column in TARGET_COLUMNS:
        if column not in df.columns:
            df[column] = ""
    df = df[TARGET_COLUMNS].copy()
    df["citedByCount"] = pd.to_numeric(df["citedByCount"], errors="coerce").fillna(0).astype(int)
    df["_norm_title"] = df["title"].fillna("").astype(str).str.strip().str.lower()
    if dedupe_order == "citations":
        df = df.sort_values(by="citedByCount", ascending=False)
    elif dedupe_order != "relevance":
        raise ValueError("dedupe_order must be either 'citations' or 'relevance'")
    df = df.drop_duplicates(subset=["_norm_title"]).drop(columns=["_norm_title"])
    if max_rows:
        df = df.head(max_rows)
    return df.reset_index(drop=True)


def build_year_query(base_query: str, year: int) -> str:
    return f"{base_query} AND PUB_YEAR:[{year - 1} TO {year}]"


def collect_seed_articles(
    base_query: str,
    years: List[int],
    checkpoint_path: Path,
    state: PipelineState,
    page_size: int = 300,
    sort_by: str = "",
    timeout: int = 30,
    max_rows: Optional[int] = None,
    max_rows_per_year: Optional[int] = None,
    dedupe_order: str = "citations",
    session: Optional[requests.Session] = None,
    logger: Any = None,
) -> pd.DataFrame:
    rows = read_csv_rows(checkpoint_path)
    completed_years = state.completed_keys("seed_years")
    http = session or requests.Session()

    for year in tqdm(years, desc="Seed search", unit="year"):
        if max_rows and len(rows) >= max_rows:
            break
        key = str(year)
        if key in completed_years:
            continue
        try:
            remaining = None if not max_rows else max_rows - len(rows)
            if remaining is not None and remaining <= 0:
                break
            year_limit = max_rows_per_year
            if remaining is not None:
                year_limit = min(year_limit, remaining) if year_limit else remaining
            if year_limit:
                effective_page_size = min(max(1, int(page_size or 1)), int(year_limit), EUROPE_PMC_MAX_PAGE_SIZE)
                articles = search_epmc_paginated(
                    build_year_query(base_query, year),
                    page_size=effective_page_size,
                    sort_by=sort_by,
                    max_results=year_limit,
                    session=http,
                    timeout=timeout,
                )
            else:
                articles = search_epmc(
                    build_year_query(base_query, year),
                    page_size=page_size,
                    sort_by=sort_by,
                    session=http,
                    timeout=timeout,
                )
            year_records = [{"search_year": year, **article_to_record(article)} for article in articles]
            for record in year_records:
                append_csv_row(checkpoint_path, record, SEED_CHECKPOINT_COLUMNS)
                rows.append(record)
            state.mark_key_complete("seed_years", key)
        except Exception as exc:
            if logger:
                logger.exception("Seed search failed for year %s", year)
            state.record_failure("seed_years", key, str(exc))
    return dedupe_seed_articles(rows, max_rows=max_rows, dedupe_order=dedupe_order)


def fetch_complete_references_for_pmid(
    pmid: str,
    session: Optional[requests.Session] = None,
    rate_limiter: Optional[RateLimiter] = None,
    timeout: int = 30,
) -> List[Dict[str, Any]]:
    http = session or requests.Session()
    all_refs: List[Dict[str, Any]] = []
    page = 1
    while True:
        if rate_limiter:
            rate_limiter.wait()
        url = EUROPE_PMC_REFERENCES_URL.format(pmid=pmid)
        response = http.get(url, params={"format": "json", "pageSize": 1000, "page": page}, timeout=timeout)
        _raise_for_status(response)
        refs = response.json().get("referenceList", {}).get("reference", []) or []
        if not refs:
            break
        all_refs.extend(refs)
        page += 1
    return all_refs


def reference_record_from_ref(pmid: str, ref: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "seed_pmid": pmid,
        "ref_source": clean_str(ref.get("source", "")),
        "ref_id": clean_str(ref.get("id", "")),
        "ref_title": clean_str(ref.get("title", "")),
        "ref_doi": clean_str(ref.get("doi", "")),
        "ref_pubYear": clean_str(ref.get("pubYear", "")),
    }


def _fetch_references_for_pmid(
    pmid: str,
    limiter: RateLimiter,
    timeout: int,
    thread_local: threading.local,
) -> Dict[str, Any]:
    refs = fetch_complete_references_for_pmid(
        pmid,
        session=_thread_session(thread_local),
        rate_limiter=limiter,
        timeout=timeout,
    )
    return {"pmid": pmid, "refs": refs}


def _seed_pmids(seed_df: pd.DataFrame, max_rows: Optional[int] = None) -> List[str]:
    if "pmid" not in seed_df.columns:
        raise ValueError("Seed data must contain a 'pmid' column")
    pmids = (
        seed_df["pmid"]
        .dropna()
        .astype(str)
        .str.extract(r"(\d+)")[0]
        .dropna()
        .drop_duplicates()
        .tolist()
    )
    return pmids[:max_rows] if max_rows else pmids


def collect_references_for_seeds(
    seed_df: pd.DataFrame,
    checkpoint_path: Path,
    state: PipelineState,
    rate_limit_per_sec: float = 10,
    timeout: int = 30,
    max_rows: Optional[int] = None,
    max_workers: int = 1,
    session: Optional[requests.Session] = None,
    logger: Any = None,
) -> pd.DataFrame:
    rows = read_csv_rows(checkpoint_path)
    completed_pmids = state.completed_keys("reference_pmids")
    limiter = RateLimiter(rate_limit_per_sec)
    pmids = _seed_pmids(seed_df, max_rows=max_rows)
    targets = [pmid for pmid in pmids if pmid not in completed_pmids]
    worker_count = max(1, int(max_workers or 1))

    def store_refs(pmid: str, refs: List[Dict[str, Any]]) -> None:
        for ref in refs:
            record = reference_record_from_ref(pmid, ref)
            append_csv_row(checkpoint_path, record, REFERENCE_COLUMNS)
            rows.append(record)
        state.mark_key_complete("reference_pmids", pmid)
        completed_pmids.add(pmid)

    if worker_count == 1:
        http = session or requests.Session()
        for pmid in tqdm(targets, desc="Reference collection", unit="pmid"):
            try:
                refs = fetch_complete_references_for_pmid(
                    pmid,
                    session=http,
                    rate_limiter=limiter,
                    timeout=timeout,
                )
                store_refs(pmid, refs)
            except Exception as exc:
                if logger:
                    logger.exception("Reference collection failed for PMID %s", pmid)
                state.record_failure("reference_pmids", pmid, str(exc))
        return pd.DataFrame(rows, columns=REFERENCE_COLUMNS)

    if session is not None:
        raise ValueError("collect_references_for_seeds does not support a shared session with worker threads")

    thread_local = threading.local()
    target_iter = iter(targets)
    futures: Dict[Future, str] = {}
    max_in_flight = max(worker_count, worker_count * 4)

    def submit_next(executor: ThreadPoolExecutor) -> bool:
        try:
            pmid = next(target_iter)
        except StopIteration:
            return False
        future = executor.submit(_fetch_references_for_pmid, pmid, limiter, timeout, thread_local)
        futures[future] = pmid
        return True

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        for _ in range(min(max_in_flight, len(targets))):
            submit_next(executor)
        with tqdm(total=len(targets), desc="Reference collection", unit="pmid") as pbar:
            while futures:
                done, _ = wait(futures.keys(), return_when=FIRST_COMPLETED)
                for future in done:
                    pmid = futures.pop(future)
                    try:
                        result = future.result()
                        store_refs(result["pmid"], result["refs"])
                    except Exception as exc:
                        if logger:
                            logger.exception("Reference collection failed for PMID %s", pmid)
                        state.record_failure("reference_pmids", pmid, str(exc))
                    pbar.update(1)
                    submit_next(executor)

    return pd.DataFrame(rows, columns=REFERENCE_COLUMNS)


def fetch_article_detail(
    source: str,
    ref_id: str,
    session: Optional[requests.Session] = None,
    rate_limiter: Optional[RateLimiter] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    if rate_limiter:
        rate_limiter.wait()
    http = session or requests.Session()
    url = EUROPE_PMC_ARTICLE_URL.format(source=source, ref_id=ref_id)
    response = http.get(url, params={"format": "json", "resultType": "core"}, timeout=timeout)
    _raise_for_status(response)
    return response.json().get("result", {}) or {}


def fetch_article_details_batch(
    source: str,
    ref_ids: List[str],
    session: Optional[requests.Session] = None,
    rate_limiter: Optional[RateLimiter] = None,
    timeout: int = 30,
) -> List[Dict[str, Any]]:
    ids = [clean_str(ref_id) for ref_id in ref_ids if clean_str(ref_id)]
    if not ids:
        return []
    if rate_limiter:
        rate_limiter.wait()
    http = session or requests.Session()
    query = f"SRC:{clean_str(source)} AND (" + " OR ".join(f"EXT_ID:{ref_id}" for ref_id in ids) + ")"
    response = http.get(
        EUROPE_PMC_SEARCH_URL,
        params={
            "query": query,
            "format": "json",
            "pageSize": len(ids),
            "synonym": "FALSE",
            "resultType": "core",
        },
        timeout=timeout,
    )
    _raise_for_status(response)
    return response.json().get("resultList", {}).get("result", []) or []


def _thread_session(thread_local: threading.local) -> requests.Session:
    session = getattr(thread_local, "session", None)
    if session is None:
        session = requests.Session()
        thread_local.session = session
    return session


def detail_record_from_article(article: Dict[str, Any], fallback_source: str = "") -> Dict[str, Any]:
    record = article_to_record(article)
    record["source"] = clean_str(article.get("source", fallback_source))
    return record


def detail_key(source: str, ref_id: str) -> str:
    return f"{clean_str(source)}/{clean_str(ref_id)}"


def detail_key_from_article(article: Dict[str, Any], fallback_source: str = "") -> str:
    source = clean_str(article.get("source", fallback_source))
    ref_id = clean_str(article.get("id", ""))
    if not ref_id and source == "MED":
        ref_id = clean_str(article.get("pmid", ""))
    if not ref_id and source == "PMC":
        ref_id = clean_str(article.get("pmcid", ""))
    return detail_key(source, ref_id) if source and ref_id else ""


def completed_detail_keys_from_rows(rows: Iterable[Dict[str, Any]]) -> set[str]:
    completed: set[str] = set()
    for row in rows:
        source = clean_str(row.get("source", ""))
        ref_id = ""
        if source == "MED":
            ref_id = clean_str(row.get("pmid", ""))
        elif source == "PMC":
            ref_id = clean_str(row.get("pmcid", ""))
        if source and ref_id:
            completed.add(detail_key(source, ref_id))
    return completed


def reference_targets(reference_df: pd.DataFrame, max_rows: Optional[int] = None) -> pd.DataFrame:
    if reference_df.empty:
        return pd.DataFrame(columns=["ref_source", "ref_id"])
    work = reference_df.copy()
    for column in ("ref_source", "ref_id"):
        if column not in work.columns:
            work[column] = ""
        work[column] = work[column].apply(clean_str)
    work = work[(work["ref_source"] != "") & (work["ref_id"] != "")]
    work = work.drop_duplicates(subset=["ref_source", "ref_id"])
    if max_rows:
        work = work.head(max_rows)
    return work[["ref_source", "ref_id"]].reset_index(drop=True)


def dedupe_reference_details(rows: Iterable[Dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(list(rows), columns=DETAIL_COLUMNS)
    if df.empty:
        return pd.DataFrame(columns=DETAIL_COLUMNS)
    for column in DETAIL_COLUMNS:
        if column not in df.columns:
            df[column] = ""
    df = df[DETAIL_COLUMNS].copy()
    df["citedByCount"] = pd.to_numeric(df["citedByCount"], errors="coerce").fillna(0).astype(int)
    df["doi"] = df["doi"].fillna("").astype(str).str.strip().str.lower()
    df["pmid"] = df["pmid"].fillna("").astype(str).str.strip()
    df["pmcid"] = df["pmcid"].fillna("").astype(str).str.strip()
    df["_norm_title"] = df["title"].fillna("").astype(str).str.strip().str.lower()
    df["_dedup_key"] = df.apply(
        lambda row: (
            f"doi:{row['doi']}"
            if row["doi"]
            else f"pmid:{row['pmid']}"
            if row["pmid"]
            else f"pmcid:{row['pmcid']}"
            if row["pmcid"]
            else f"title:{row['_norm_title']}"
        ),
        axis=1,
    )
    df = (
        df.sort_values(by="citedByCount", ascending=False)
        .drop_duplicates(subset=["_dedup_key"])
        .drop(columns=["_norm_title", "_dedup_key"])
    )
    return df.reset_index(drop=True)


def detail_targets_to_fetch(targets: pd.DataFrame, completed_refs: Iterable[str]) -> List[Dict[str, str]]:
    completed = set(completed_refs)
    pending: List[Dict[str, str]] = []
    for row in targets.itertuples(index=False):
        source = clean_str(getattr(row, "ref_source", ""))
        ref_id = clean_str(getattr(row, "ref_id", ""))
        key = detail_key(source, ref_id)
        if key in completed:
            continue
        pending.append({"source": source, "ref_id": ref_id, "key": key})
    return pending


def detail_target_batches(targets: List[Dict[str, str]], batch_size: int) -> List[List[Dict[str, str]]]:
    size = max(1, int(batch_size or 1))
    batches: List[List[Dict[str, str]]] = []
    current: List[Dict[str, str]] = []
    current_source = ""
    for target in targets:
        source = target["source"]
        if current and (source != current_source or len(current) >= size):
            batches.append(current)
            current = []
        current.append(target)
        current_source = source
    if current:
        batches.append(current)
    return batches


def _fetch_detail_target(
    target: Dict[str, str],
    limiter: RateLimiter,
    timeout: int,
    thread_local: threading.local,
) -> Dict[str, Any]:
    article = fetch_article_detail(
        target["source"],
        target["ref_id"],
        session=_thread_session(thread_local),
        rate_limiter=limiter,
        timeout=timeout,
    )
    return {
        "key": target["key"],
        "record": detail_record_from_article(article, fallback_source=target["source"]),
    }


def _fetch_detail_batch(
    targets: List[Dict[str, str]],
    limiter: RateLimiter,
    timeout: int,
    thread_local: threading.local,
) -> Dict[str, Any]:
    http = _thread_session(thread_local)
    successes: List[Dict[str, Any]] = []
    failures: List[Dict[str, str]] = []
    if not targets:
        return {"processed": 0, "successes": successes, "failures": failures}

    if len(targets) == 1:
        target = targets[0]
        try:
            result = _fetch_detail_target(target, limiter, timeout, thread_local)
            successes.append({"key": result["key"], "record": result["record"]})
        except Exception as exc:
            failures.append({"key": target["key"], "error": str(exc)})
        return {"processed": 1, "successes": successes, "failures": failures}

    source = targets[0]["source"]
    target_by_key = {target["key"]: target for target in targets}
    found_articles: Dict[str, Dict[str, Any]] = {}
    batch_error = ""
    try:
        articles = fetch_article_details_batch(
            source,
            [target["ref_id"] for target in targets],
            session=http,
            rate_limiter=limiter,
            timeout=timeout,
        )
        for article in articles:
            key = detail_key_from_article(article, fallback_source=source)
            if key in target_by_key and key not in found_articles:
                found_articles[key] = article
    except Exception as exc:
        batch_error = str(exc)

    for target in targets:
        article = found_articles.get(target["key"])
        if article is not None:
            successes.append(
                {
                    "key": target["key"],
                    "record": detail_record_from_article(article, fallback_source=target["source"]),
                }
            )
            continue
        try:
            fallback_article = fetch_article_detail(
                target["source"],
                target["ref_id"],
                session=http,
                rate_limiter=limiter,
                timeout=timeout,
            )
            successes.append(
                {
                    "key": target["key"],
                    "record": detail_record_from_article(fallback_article, fallback_source=target["source"]),
                }
            )
        except Exception as exc:
            message = str(exc)
            if batch_error:
                message = f"batch failed: {batch_error}; fallback failed: {message}"
            failures.append({"key": target["key"], "error": message})

    return {"processed": len(targets), "successes": successes, "failures": failures}


def _collect_reference_details_serial(
    pending_targets: List[Dict[str, str]],
    rows: List[Dict[str, Any]],
    checkpoint_path: Path,
    state: PipelineState,
    limiter: RateLimiter,
    timeout: int,
    session: Optional[requests.Session],
    logger: Any = None,
) -> None:
    http = session or requests.Session()
    for target in tqdm(pending_targets, total=len(pending_targets), desc="Reference details", unit="ref"):
        try:
            article = fetch_article_detail(
                target["source"],
                target["ref_id"],
                session=http,
                rate_limiter=limiter,
                timeout=timeout,
            )
            record = detail_record_from_article(article, fallback_source=target["source"])
            append_csv_row(checkpoint_path, record, DETAIL_COLUMNS)
            rows.append(record)
            state.mark_key_complete("detail_refs", target["key"])
        except Exception as exc:
            if logger:
                logger.exception("Reference detail fetch failed for %s", target["key"])
            state.record_failure("detail_refs", target["key"], str(exc))


def _collect_reference_details_threaded(
    pending_targets: List[Dict[str, str]],
    rows: List[Dict[str, Any]],
    checkpoint_path: Path,
    state: PipelineState,
    limiter: RateLimiter,
    timeout: int,
    max_workers: int,
    batch_size: int = DETAIL_BATCH_SIZE,
    logger: Any = None,
) -> None:
    thread_local = threading.local()
    batches = detail_target_batches(pending_targets, batch_size)
    batch_iter = iter(batches)
    futures: Dict[Future[Dict[str, Any]], List[Dict[str, str]]] = {}
    max_outstanding = max_workers * DETAIL_PREFETCH_MULTIPLIER

    def submit_next(executor: ThreadPoolExecutor) -> bool:
        try:
            batch = next(batch_iter)
        except StopIteration:
            return False
        future = executor.submit(_fetch_detail_batch, batch, limiter, timeout, thread_local)
        futures[future] = batch
        return True

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for _ in range(min(max_outstanding, len(batches))):
            submit_next(executor)

        with tqdm(total=len(pending_targets), desc="Reference details", unit="ref") as progress:
            while futures:
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    batch = futures.pop(future)
                    try:
                        result = future.result()
                        completed_keys = []
                        for success in result["successes"]:
                            record = success["record"]
                            append_csv_row(checkpoint_path, record, DETAIL_COLUMNS)
                            rows.append(record)
                            completed_keys.append(success["key"])
                        state.mark_keys_complete("detail_refs", completed_keys)
                        for failure in result["failures"]:
                            if logger:
                                logger.error("Reference detail fetch failed for %s: %s", failure["key"], failure["error"])
                            state.record_failure("detail_refs", failure["key"], failure["error"])
                        processed = result["processed"]
                    except Exception as exc:
                        processed = len(batch)
                        for target in batch:
                            if logger:
                                logger.exception("Reference detail fetch failed for %s", target["key"])
                            state.record_failure("detail_refs", target["key"], str(exc))
                    progress.update(processed)
                    submit_next(executor)


def collect_reference_details(
    reference_df: pd.DataFrame,
    checkpoint_path: Path,
    state: PipelineState,
    rate_limit_per_sec: float = 9,
    timeout: int = 30,
    max_rows: Optional[int] = None,
    max_workers: int = 1,
    batch_size: int = DETAIL_BATCH_SIZE,
    session: Optional[requests.Session] = None,
    logger: Any = None,
) -> pd.DataFrame:
    rows = read_csv_rows(checkpoint_path)
    completed_refs = state.completed_keys("detail_refs") | completed_detail_keys_from_rows(rows)
    limiter = RateLimiter(rate_limit_per_sec)
    targets = reference_targets(reference_df, max_rows=max_rows)
    pending_targets = detail_targets_to_fetch(targets, completed_refs)

    worker_count = max(1, int(max_workers or 1))
    resolved_batch_size = max(1, int(batch_size or 1))
    if worker_count == 1 and resolved_batch_size == 1:
        _collect_reference_details_serial(
            pending_targets,
            rows,
            checkpoint_path,
            state,
            limiter,
            timeout,
            session=session,
            logger=logger,
        )
    else:
        _collect_reference_details_threaded(
            pending_targets,
            rows,
            checkpoint_path,
            state,
            limiter,
            timeout,
            max_workers=worker_count,
            batch_size=resolved_batch_size,
            logger=logger,
        )

    return dedupe_reference_details(rows)
