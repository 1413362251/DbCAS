import argparse
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from paper_search import (
    EUROPE_PMC_MAX_PAGE_SIZE,
    collect_reference_details,
    collect_references_for_seeds,
    collect_seed_articles,
)
from paper_screening import (
    DEFAULT_AI_QUESTION,
    DEFAULT_STAGE2_EXPERT_PROMPT,
    DEFAULT_STAGE2_MAX_OUTPUT_TOKENS,
    DEFAULT_STAGE2_PROMPT_CACHE_KEY,
    DEFAULT_STAGE2_PROMPT_CACHE_RETENTION,
    DEFAULT_STAGE2_REASONING_EFFORT,
    DEFAULT_STAGE2_SERVICE_TIER,
    DEFAULT_STAGE1_SERVICE_TIER,
    SCREENING_POLICY_VERSION,
    ai_screen_dataframe,
    keyword_screen,
    mark_ai_skipped,
    prioritize_stage2_benchmark_rows,
    rebuild_web_from_doi,
    stage2_screen_dataframe,
    split_missing_abstract,
    yes_only,
)
from pipeline_runtime import (
    PipelineState,
    append_csv_row,
    configure_logging,
    read_csv_rows,
    setup_run_paths,
    write_config,
)
from stage8_finalization import (
    FINALIZATION_POLICY_VERSION,
    apply_incremental_duplicate_decisions,
    augment_evidence_accessibility_audit,
    build_duplicate_candidate_map,
    enrich_accessibility_audit,
    initialize_raw_snapshot,
    merge_final_review,
    prepare_incremental_duplicate_review,
    prepare_final_review_dataframe,
    run_accessibility_audit,
    scan_final_review_chunks,
    write_excel_with_hyperlinks,
    write_final_review_chunks,
    write_finalization_summary,
)


# =============================================================================
# User-editable parameters
# =============================================================================

SEARCH_QUERY = '("alternative splicing" OR splicing)'
SEARCH_YEARS = [2026, 2025, 2024, 2023, 2022, 2021, 2020, 2019, 2018, 2017, 2016, 2015]
RUN_ID = ""  # Leave blank to create runs/<YYYYMMDD_HHMMSS>.
RESUME = True

ENABLED_STAGES = [
    "seed_search",
    "references",
    "details",
    "keyword_screen",
    "ai_screen",
]

SEED_PAGE_SIZE = 300
SEED_LIMIT_PER_YEAR = None
SEED_SORT_BY = ""
SEED_DEDUPE_ORDER = "citations"
REFERENCE_RATE_LIMIT_PER_SEC = 10
REFERENCE_WORKERS = 1
DETAIL_RATE_LIMIT_PER_SEC = 9
DETAIL_WORKERS = 1
DETAIL_BATCH_SIZE = 1
REQUEST_TIMEOUT = 30

ENABLE_AI = True
OPENAI_MODEL = "gpt-5.4"
AI_QUESTION = DEFAULT_AI_QUESTION
STAGE1_SERVICE_TIER = DEFAULT_STAGE1_SERVICE_TIER
AI_WORKERS = 1
AI_RATE_LIMIT_PER_SEC = 500 / 60

STAGE2_SERVICE_TIER = DEFAULT_STAGE2_SERVICE_TIER
STAGE2_REASONING_EFFORT = DEFAULT_STAGE2_REASONING_EFFORT
STAGE2_PROMPT_CACHE_KEY = DEFAULT_STAGE2_PROMPT_CACHE_KEY
STAGE2_PROMPT_CACHE_RETENTION = DEFAULT_STAGE2_PROMPT_CACHE_RETENTION
STAGE2_MAX_OUTPUT_TOKENS = DEFAULT_STAGE2_MAX_OUTPUT_TOKENS
STAGE2_WORKERS = 32
STAGE2_RATE_LIMIT_PER_SEC = 500 / 60
STAGE2_BENCHMARK_PATH = str(
    Path(__file__).resolve().parents[1]
    / "archive"
    / "datacollection"
    / "legacy_outputs"
    / "Updated_List.xlsx"
)

MAX_ROWS = None  # Set an integer for quick test runs.


# =============================================================================
# Pipeline implementation
# =============================================================================

DATA_COLLECTION_DIR = Path(__file__).resolve().parent

OUTPUT_FILES = {
    "seed_search": "01_seed_articles.xlsx",
    "references": "02_reference_list_full.xlsx",
    "details": "03_reference_details_dedup.xlsx",
    "keyword_screen": "04_keyword_screened.xlsx",
    "missing_abstract_for_codex": "05_missing_abstract_for_codex.xlsx",
    "ai_screen": "05_ai_check.xlsx",
    "ai_yes_only": "05_ai_yes_only.xlsx",
    "stage1_pass_unclear_for_stage2": "05_stage1_pass_unclear_for_stage2.xlsx",
    "stage2_ai_check": "06_stage2_ai_check.xlsx",
    "stage2_ai_yes_only": "06_stage2_ai_yes_only.xlsx",
    "stage2_ai_summary": "06_stage2_ai_check.summary.json",
    "agent_input_all": "07_agent_input_all.xlsx",
    "agent_input_summary": "07_agent_input.summary.json",
    "agent_merged": "08_agent_merged.xlsx",
    "agent_merged_summary": "08_agent_merged.summary.json",
    "accessibility_audit": "08_accessibility_audit.xlsx",
    "duplicate_merge_audit": "08_duplicate_merge_audit.xlsx",
    "final_review_input_all": "08_final_review_input_all.xlsx",
}

CHECKPOINT_FILES = {
    "seed_search": "01_seed_articles.csv",
    "references": "02_reference_list_full.csv",
    "details": "03_reference_details.csv",
    "ai_screen": "05_ai_check.csv",
    "stage2_ai_check": "06_stage2_ai_check.csv",
    "agent_table_prep": "07_agent_input_rows.csv",
    "agent_web_curation": "08_agent_chunks.csv",
    "agent_final_review": "08_final_review_chunks.csv",
}

OUTPUT_DIRS = {
    "agent_input_chunks": "07_agent_input_chunks",
    "agent_result_chunks": "08_chunk",
    "final_review_input_chunks": "08_final_review_input_chunks",
    "final_review_result_chunks": "08_final_review_chunk",
}

POLICY_VERSION_COLUMN = "<sub,t-word-tag> screening_policy_version"
ORIGINAL_DATABASE_COLUMN = "<main,t-bool> original_48_database"
QUALIFICATION_BASIS_COLUMN = "<main,t-word-tag> qualification_basis"
EXCLUSION_CODE_COLUMN = "<main,t-word-tag> exclusion_code"
EVIDENCE_URL_COLUMN = "<sub,t-word-url> evidence_url"
EVIDENCE_SOURCE_TYPE_COLUMN = "<sub,t-word-tag> evidence_source_type"
EVIDENCE_STATEMENT_COLUMN = "<sub,t-word> evidence_statement"
EVIDENCE_CHECKED_DATE_COLUMN = "<sub,t-word> evidence_checked_date"
MANUAL_REVIEW_COLUMN = "<main,t-bool> manual_review_needed"

AGENT_INPUT_COLUMNS = [
    "<main,t-word-id> id",
    "<main,t-word> title",
    "<main,t-word> database_name",
    "<main,t-word-url> database_url",
    "<sub,t-word-doi> doi",
    "<sub,t-word-pmid> pmid",
    "<sub,t-numeric> year",
    ORIGINAL_DATABASE_COLUMN,
    POLICY_VERSION_COLUMN,
    "<main,t-word-tag> accessibility",
    "<main,t-bool> db_type_confirmation",
    "<sub,t-word> confirmation_reason",
    QUALIFICATION_BASIS_COLUMN,
    EXCLUSION_CODE_COLUMN,
    EVIDENCE_URL_COLUMN,
    EVIDENCE_SOURCE_TYPE_COLUMN,
    EVIDENCE_STATEMENT_COLUMN,
    EVIDENCE_CHECKED_DATE_COLUMN,
    MANUAL_REVIEW_COLUMN,
    "<main,t-word-tag> neural_link",
    "<main,t-word-tag> focus",
    "<main,t-bool> gene_expression_available",
    "<main,t-word-tag> species",
    "<sub,t-word-tag> disease_association",
    "<sub,t-word-tag> developmental_association",
    "<main,t-word-tag> tissue_or_brain_region",
    "<sub,t-word-tag> cell_type",
    "<main,t-word-tag> sequencing_resolution",
    "<main,t-word-tag> read_technology",
    "<sub,t-word> visualization_methods",
]

QUALIFICATION_BASIS_VALUES = {
    "splicing_event",
    "splice_site_or_junction",
    "splicing_regulation_or_sqtl",
    "transcript_or_isoform_model",
    "transcript_level_abundance",
}

EXCLUSION_CODE_VALUES = {
    "not_applicable",
    "gene_expression_only",
    "rna_not_transcript_splicing",
    "no_search_browse_query",
    "download_or_static_only",
    "software_or_upload_only",
    "paper_only",
    "insufficient_evidence",
}

EVIDENCE_SOURCE_TYPE_VALUES = {
    "official_database",
    "official_documentation",
    "publication",
    "web_archive",
}

EVIDENCE_STATEMENT_MIN_CHARS = 20
EVIDENCE_STATEMENT_MAX_CHARS = 300

STAGE8_FORBIDDEN_PLACEHOLDER_TERMS = {
    "multiple species",
    "various species",
    "many species",
    "several species",
    "diverse species",
    "multiple organisms",
    "various organisms",
    "multiple tissues",
    "various tissues",
    "many tissues",
    "several tissues",
    "multiple organs",
    "various organs",
    "multiple brain regions",
    "various brain regions",
    "multiple cell types",
    "various cell types",
    "many cell types",
    "several cell types",
    "multiple diseases",
    "various diseases",
    "multiple stages",
    "various stages",
    "multiple visualizations",
    "various visualizations",
    "various plots",
}

STAGE8_FORBIDDEN_PLACEHOLDER_COLUMNS = [
    "<main,t-word-tag> species",
    "<sub,t-word-tag> disease_association",
    "<sub,t-word-tag> developmental_association",
    "<main,t-word-tag> tissue_or_brain_region",
    "<sub,t-word-tag> cell_type",
    "<sub,t-word> visualization_methods",
]

STAGE8_AGENT_FILLED_COLUMNS = [
    "<main,t-word-tag> accessibility",
    "<main,t-bool> db_type_confirmation",
    "<sub,t-word> confirmation_reason",
    QUALIFICATION_BASIS_COLUMN,
    EXCLUSION_CODE_COLUMN,
    EVIDENCE_URL_COLUMN,
    EVIDENCE_SOURCE_TYPE_COLUMN,
    EVIDENCE_STATEMENT_COLUMN,
    EVIDENCE_CHECKED_DATE_COLUMN,
    MANUAL_REVIEW_COLUMN,
    "<main,t-word-tag> neural_link",
    "<main,t-word-tag> focus",
    "<main,t-bool> gene_expression_available",
    "<main,t-word-tag> species",
    "<sub,t-word-tag> disease_association",
    "<sub,t-word-tag> developmental_association",
    "<main,t-word-tag> tissue_or_brain_region",
    "<sub,t-word-tag> cell_type",
    "<main,t-word-tag> sequencing_resolution",
    "<main,t-word-tag> read_technology",
    "<sub,t-word> visualization_methods",
]

STAGE8_UNKNOWN_MISSING_VALUE_COLUMNS = [
    "<main,t-word-tag> focus",
    "<main,t-word-tag> species",
    "<sub,t-word-tag> disease_association",
    "<sub,t-word-tag> developmental_association",
    "<main,t-word-tag> tissue_or_brain_region",
    "<sub,t-word-tag> cell_type",
    "<sub,t-word> visualization_methods",
]

STAGE8_FORBIDDEN_MISSING_VALUES = {
    "none",
    "unclear",
    "n/a",
    "na",
    "not specified",
}

CONFIRMATION_REASON_MAX_CHARS = 80

URL_PATTERN = re.compile(r"\b(?:https?://|www\.)[^\s<>\"]+", flags=re.IGNORECASE)
URL_TRAILING_CHARS = ".,;:)]}>\"'"

STAGE8_CHECKPOINT_COLUMNS = [
    "chunk_id",
    "input_path",
    "result_path",
    "status",
    "agent_id",
    "assigned_at",
    "completed_at",
    "input_rows",
    "result_rows",
    "error",
]


def parse_years(value: Optional[str]) -> Optional[List[int]]:
    if not value:
        return None
    value = value.strip()
    if "-" in value and "," not in value:
        start_text, end_text = value.split("-", 1)
        start = int(start_text.strip())
        end = int(end_text.strip())
        step = -1 if start >= end else 1
        return list(range(start, end + step, step))
    return [int(part.strip()) for part in value.split(",") if part.strip()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the paper search and screening pipeline.")
    parser.add_argument("--run-id", help="Run folder name under datacollection/runs.")
    parser.add_argument("--resume", action="store_true", help="Resume an existing run.")
    parser.add_argument("--query", help="Base Europe PMC query, without the PUB_YEAR filter.")
    parser.add_argument("--years", help="Years as comma list or range, e.g. 2026,2025 or 2026-2015.")
    parser.add_argument("--no-ai", action="store_true", help="Skip OpenAI calls and mark AI_check as Skipped.")
    parser.add_argument("--stop-before-ai", action="store_true", help="Stop after keyword screening.")
    parser.add_argument("--stage2-only", action="store_true", help="Run only Stage2 expert AI check from Stage1 output.")
    parser.add_argument("--stage7-only", action="store_true", help="Run only Stage 07 agent input table preparation.")
    parser.add_argument("--stage7-input", help="Optional Stage 07 input Excel path. Defaults to run outputs/06_stage2_ai_yes_only.xlsx.")
    parser.add_argument(
        "--stage7-input-format",
        choices=["stage2", "curated"],
        default="stage2",
        help="Stage 07 input schema: Stage2 yes-only rows or an existing curated workbook.",
    )
    parser.add_argument("--stage7-forced-include", help="Optional Excel file of legacy/forced rows to append to Stage 07 input.")
    parser.add_argument("--stage7-chunk-size", type=int, default=25, help="Rows per Stage 07 agent chunk file. Default is 25.")
    parser.add_argument("--stage8-only", action="store_true", help="Run only Stage 08 agent result scan and merge support.")
    parser.add_argument("--stage8-agent-count", type=int, default=5, help="Maximum concurrent Codex sub-agents expected for Stage 08. Default is 5.")
    parser.add_argument("--stage8-input-dir", help="Optional Stage 08 input chunk directory. Defaults to run outputs/07_agent_input_chunks.")
    parser.add_argument("--stage8-merge-only", action="store_true", help="Only validate and merge existing Stage 08 chunk results.")
    parser.add_argument(
        "--stage8-finalize-only",
        action="store_true",
        help="Run database-level duplicate, evidence, and accessibility finalization from an existing Stage 08 workbook.",
    )
    parser.add_argument("--stage8-finalize-input", help="Existing standard 30-column Stage 08 workbook to finalize.")
    parser.add_argument("--stage8-url-workers", type=int, default=32, help="Concurrent URL checks during Stage 08 finalization. Default is 32.")
    parser.add_argument("--stage8-url-timeout", type=float, default=120, help="Total per-URL accessibility budget in seconds. Default is 120.")
    parser.add_argument(
        "--no-stage8-finalize-after-merge",
        action="store_true",
        help="Keep the legacy raw Stage 08 merge behavior instead of automatically starting finalization.",
    )
    parser.add_argument("--limit", type=int, help="Limit rows per stage for quick test runs.")
    parser.add_argument("--seed-limit-per-year", type=int, help="Limit seed articles collected for each search year.")
    parser.add_argument(
        "--seed-dedupe-order",
        choices=["citations", "relevance"],
        help="Seed duplicate retention order: highest citation count or Europe PMC relevance order.",
    )
    parser.add_argument("--reference-workers", type=int, help="Number of worker threads for seed reference fetching.")
    parser.add_argument("--detail-workers", type=int, help="Number of worker threads for reference detail fetches.")
    parser.add_argument("--detail-batch-size", type=int, help="Reference detail IDs per batch search request.")
    parser.add_argument("--ai-workers", type=int, help="Number of worker threads for OpenAI AI screening.")
    parser.add_argument(
        "--ai-rate-limit-per-sec",
        type=float,
        help="Global OpenAI request limit shared by all AI worker threads. Default is 500 requests/min.",
    )
    parser.add_argument(
        "--ai-max-new-rows",
        type=int,
        help="Limit new OpenAI AI screening requests for this invocation without completing the AI stage early.",
    )
    parser.add_argument(
        "--stage1-service-tier",
        help="OpenAI service_tier for future low-cost stage1 triage. Default is flex.",
    )
    parser.add_argument("--stage2-workers", type=int, help="Number of worker threads for Stage2 expert AI screening.")
    parser.add_argument(
        "--stage2-rate-limit-per-sec",
        type=float,
        help="Global Stage2 OpenAI request limit shared by all Stage2 worker threads. Default is 500 requests/min.",
    )
    parser.add_argument(
        "--stage2-max-new-rows",
        type=int,
        help="Limit new Stage2 OpenAI requests for this invocation without completing Stage2 early.",
    )
    parser.add_argument("--stage2-service-tier", help="OpenAI service_tier for Stage2. Default is flex.")
    parser.add_argument(
        "--stage2-reasoning-effort",
        help="OpenAI reasoning effort for Stage2 Responses API. Default is none.",
    )
    parser.add_argument("--stage2-prompt-cache-key", help="Prompt cache key for Stage2.")
    parser.add_argument(
        "--stage2-prompt-cache-retention",
        help="Prompt cache retention for Stage2. Default is 24h.",
    )
    parser.add_argument("--stage2-max-output-tokens", type=int, help="Stage2 max_output_tokens. Default is 800.")
    parser.add_argument("--stage2-benchmark-path", help="Benchmark Excel path to prioritize in Stage2.")
    parser.add_argument("--model", help="OpenAI model name.")
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> Dict[str, Any]:
    years = parse_years(args.years) or SEARCH_YEARS
    seed_limit_per_year = (
        args.seed_limit_per_year
        if getattr(args, "seed_limit_per_year", None) is not None
        else SEED_LIMIT_PER_YEAR
    )
    seed_page_size = SEED_PAGE_SIZE
    if seed_limit_per_year:
        seed_page_size = min(max(seed_page_size, seed_limit_per_year), EUROPE_PMC_MAX_PAGE_SIZE)
    enabled_stages = list(ENABLED_STAGES)
    if getattr(args, "stop_before_ai", False) and "ai_screen" in enabled_stages:
        enabled_stages.remove("ai_screen")
    stage2_only = bool(getattr(args, "stage2_only", False))
    stage7_only = bool(getattr(args, "stage7_only", False))
    stage8_only = bool(getattr(args, "stage8_only", False))
    stage8_finalize_only = bool(getattr(args, "stage8_finalize_only", False))
    if sum(bool(value) for value in (stage2_only, stage7_only, stage8_only, stage8_finalize_only)) > 1:
        raise ValueError("--stage2-only, --stage7-only, --stage8-only, and --stage8-finalize-only cannot be used together.")
    if stage2_only:
        enabled_stages = ["stage2_ai_check"]
    if stage7_only:
        enabled_stages = ["agent_table_prep"]
    if stage8_only:
        enabled_stages = ["agent_web_curation"]
    if stage8_finalize_only:
        enabled_stages = ["agent_final_review"]
    return {
        "search_query": args.query or SEARCH_QUERY,
        "search_years": years,
        "run_id": args.run_id if args.run_id is not None else RUN_ID,
        "resume": bool(args.resume or RESUME),
        "stage2_only": stage2_only,
        "stage7_only": stage7_only,
        "stage8_only": stage8_only,
        "stage8_finalize_only": stage8_finalize_only,
        "enabled_stages": enabled_stages,
        "seed_page_size": seed_page_size,
        "seed_limit_per_year": seed_limit_per_year,
        "seed_sort_by": SEED_SORT_BY,
        "seed_dedupe_order": getattr(args, "seed_dedupe_order", None) or SEED_DEDUPE_ORDER,
        "reference_rate_limit_per_sec": REFERENCE_RATE_LIMIT_PER_SEC,
        "reference_workers": getattr(args, "reference_workers", None) or REFERENCE_WORKERS,
        "detail_rate_limit_per_sec": DETAIL_RATE_LIMIT_PER_SEC,
        "detail_workers": getattr(args, "detail_workers", None) or DETAIL_WORKERS,
        "detail_batch_size": getattr(args, "detail_batch_size", None) or DETAIL_BATCH_SIZE,
        "request_timeout": REQUEST_TIMEOUT,
        "enable_ai": False if args.no_ai else ENABLE_AI,
        "openai_model": args.model or ("gpt-5.5" if stage2_only else OPENAI_MODEL),
        "ai_question": AI_QUESTION,
        "stage1_service_tier": getattr(args, "stage1_service_tier", None) or STAGE1_SERVICE_TIER,
        "ai_workers": getattr(args, "ai_workers", None) or AI_WORKERS,
        "ai_rate_limit_per_sec": (
            getattr(args, "ai_rate_limit_per_sec", None)
            if getattr(args, "ai_rate_limit_per_sec", None) is not None
            else AI_RATE_LIMIT_PER_SEC
        ),
        "ai_max_new_rows": getattr(args, "ai_max_new_rows", None),
        "stage2_workers": getattr(args, "stage2_workers", None) or STAGE2_WORKERS,
        "stage2_rate_limit_per_sec": (
            getattr(args, "stage2_rate_limit_per_sec", None)
            if getattr(args, "stage2_rate_limit_per_sec", None) is not None
            else STAGE2_RATE_LIMIT_PER_SEC
        ),
        "stage2_max_new_rows": getattr(args, "stage2_max_new_rows", None),
        "stage2_service_tier": getattr(args, "stage2_service_tier", None) or STAGE2_SERVICE_TIER,
        "stage2_reasoning_effort": getattr(args, "stage2_reasoning_effort", None)
        or STAGE2_REASONING_EFFORT,
        "stage2_prompt_cache_key": getattr(args, "stage2_prompt_cache_key", None)
        or STAGE2_PROMPT_CACHE_KEY,
        "stage2_prompt_cache_retention": getattr(args, "stage2_prompt_cache_retention", None)
        or STAGE2_PROMPT_CACHE_RETENTION,
        "stage2_max_output_tokens": getattr(args, "stage2_max_output_tokens", None)
        or STAGE2_MAX_OUTPUT_TOKENS,
        "stage2_benchmark_path": getattr(args, "stage2_benchmark_path", None)
        or STAGE2_BENCHMARK_PATH,
        "stage2_prompt": DEFAULT_STAGE2_EXPERT_PROMPT,
        "screening_policy_version": SCREENING_POLICY_VERSION,
        "stage7_input": getattr(args, "stage7_input", None),
        "stage7_input_format": getattr(args, "stage7_input_format", None) or "stage2",
        "stage7_forced_include": getattr(args, "stage7_forced_include", None),
        "stage7_chunk_size": getattr(args, "stage7_chunk_size", None) or 25,
        "stage7_max_new_rows": getattr(args, "stage7_max_new_rows", None),
        "stage8_agent_count": getattr(args, "stage8_agent_count", None) or 5,
        "stage8_input_dir": getattr(args, "stage8_input_dir", None),
        "stage8_merge_only": bool(getattr(args, "stage8_merge_only", False)),
        "stage8_finalize_input": getattr(args, "stage8_finalize_input", None),
        "stage8_url_workers": getattr(args, "stage8_url_workers", None) or 32,
        "stage8_url_timeout": getattr(args, "stage8_url_timeout", None) or 120,
        "stage8_finalization_policy_version": FINALIZATION_POLICY_VERSION,
        # Command-line Stage 8 runs use the new finalization by default.  Older
        # programmatic callers whose handcrafted Namespace lacks this new flag
        # keep their historical behavior.
        "stage8_finalize_after_merge": (
            not bool(getattr(args, "no_stage8_finalize_after_merge", False))
            if hasattr(args, "no_stage8_finalize_after_merge")
            else False
        ),
        "max_rows": args.limit if args.limit is not None else MAX_ROWS,
    }


def write_excel(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(
        path,
        engine="xlsxwriter",
        engine_kwargs={"options": {"strings_to_urls": False}},
    ) as writer:
        df.to_excel(writer, index=False)


def read_excel(path: Path) -> pd.DataFrame:
    return pd.read_excel(path)


def validate_resume_policy(config_path: Path, config: Dict[str, Any]) -> None:
    if not config.get("resume") or not config_path.exists():
        return
    if not any(config.get(key) for key in ("stage2_only", "stage7_only", "stage8_only", "stage8_finalize_only")):
        return
    existing = json.loads(config_path.read_text(encoding="utf-8"))
    existing_version = existing.get("screening_policy_version", "legacy/unversioned")
    current_version = config.get("screening_policy_version", SCREENING_POLICY_VERSION)
    if existing_version != current_version:
        raise ValueError(
            "Screening policy mismatch for resumed run: "
            f"expected {current_version}, found {existing_version}. Use a new run ID."
        )
    if config.get("stage8_finalize_only"):
        existing_finalization = existing.get("stage8_finalization_policy_version", "legacy/unversioned")
        current_finalization = config.get("stage8_finalization_policy_version")
        if existing_finalization != current_finalization:
            raise ValueError(
                "Stage 8 finalization policy mismatch for resumed run: "
                f"expected {current_finalization}, found {existing_finalization}. Use a new run ID."
            )


def stage_enabled(config: Dict[str, Any], stage: str) -> bool:
    return stage in set(config["enabled_stages"])


def maybe_load_completed_stage(
    state: PipelineState,
    stage: str,
    output_path: Path,
    resume: bool,
    logger: Any,
) -> Optional[pd.DataFrame]:
    if resume and state.stage_completed(stage) and output_path.exists():
        logger.info("Skipping completed stage: %s", stage)
        return read_excel(output_path)
    return None


def require_stage(stage: str, output_path: Path) -> pd.DataFrame:
    if not output_path.exists():
        raise FileNotFoundError(f"Stage '{stage}' is disabled and output is missing: {output_path}")
    return read_excel(output_path)


def complete_stage(
    state: PipelineState,
    stage: str,
    output_key: str,
    output_path: Path,
    df: pd.DataFrame,
) -> None:
    state.set_output_path(output_key, output_path)
    state.set_count(f"{stage}_rows", len(df))
    state.mark_stage_complete(stage)


def numeric_sum(df: pd.DataFrame, column: str) -> int:
    if column not in df.columns:
        return 0
    return int(pd.to_numeric(df[column], errors="coerce").fillna(0).sum())


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return str(value).strip() == ""


def _clean_text(value: Any) -> str:
    if _is_blank(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none"} else text


def _clean_stage8_text(value: Any) -> str:
    if _is_blank(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def _clean_resource_name(value: Any) -> str:
    text = _clean_text(value)
    return "" if text.lower() == "none" else text


def _clean_doi(value: Any) -> str:
    text = _clean_text(value)
    if text.lower().startswith("https://doi.org/"):
        text = text[len("https://doi.org/") :]
    elif text.lower().startswith("http://doi.org/"):
        text = text[len("http://doi.org/") :]
    return text


def _clean_int_identifier(value: Any) -> str:
    if _is_blank(value):
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        text = str(value).strip()
        return text[:-2] if text.endswith(".0") and text[:-2].isdigit() else text
    if number.is_integer():
        return str(int(number))
    return str(value).strip()


def _clean_year(value: Any) -> Any:
    text = _clean_int_identifier(value)
    return int(text) if text.isdigit() else ""


def _normalize_url(raw_url: str) -> str:
    url = raw_url.strip().rstrip(URL_TRAILING_CHARS)
    if url.lower().startswith("www."):
        url = f"https://{url}"
    return url


def _is_publication_url(url: str) -> bool:
    lower = url.lower()
    if "doi.org/" in lower or "europepmc.org/" in lower:
        return True
    if "pubmed.ncbi.nlm.nih.gov/" in lower:
        return True
    if "ncbi.nlm.nih.gov/pubmed" in lower:
        return True
    return False


def extract_database_url(*texts: Any) -> str:
    seen = set()
    for value in texts:
        text = _clean_text(value)
        if not text:
            continue
        for match in URL_PATTERN.findall(text):
            url = _normalize_url(match)
            if not url or url in seen:
                continue
            seen.add(url)
            if _is_publication_url(url):
                continue
            return url
    return ""


def _identity_value(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    normalized = re.sub(r"\s+", " ", text).strip()
    return "" if normalized.lower() in {"-", "na", "n/a", "null", "unknown"} else normalized


def _normalize_doi_identity(value: Any) -> str:
    text = _identity_value(value)
    if not text:
        return ""
    text = re.sub(r"^https?://(dx\.)?doi\.org/", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^doi:\s*", "", text, flags=re.IGNORECASE)
    return text.strip().rstrip(".").lower()


def _normalize_text_identity(value: Any) -> str:
    text = _identity_value(value)
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip().lower()


def _first_identity_value(*values: Any) -> Any:
    for value in values:
        if _identity_value(value):
            return value
    return ""


def _candidate_identity_key(row: Dict[str, Any], include_resource_name: bool = False) -> str:
    doi = _normalize_doi_identity(_first_identity_value(row.get("doi"), row.get("ref_doi")))
    if doi:
        return f"doi:{doi}"
    pmid = _clean_int_identifier(_first_identity_value(row.get("pmid")))
    if _identity_value(pmid):
        return f"pmid:{pmid.lower()}"
    pmcid = _normalize_text_identity(row.get("pmcid"))
    if pmcid:
        return f"pmcid:{pmcid}"
    title = _normalize_text_identity(_first_identity_value(row.get("title"), row.get("ref_title")))
    if title:
        return f"title:{title}"
    if include_resource_name:
        resource_name = _normalize_text_identity(
            _first_identity_value(row.get("resource_name"), row.get("database_name"))
        )
        if resource_name:
            return f"resource:{resource_name}"
    return ""


def build_keyword_candidate_pool(seed_df: pd.DataFrame, detail_df: pd.DataFrame) -> pd.DataFrame:
    detail_work = detail_df.copy()
    seed_work = seed_df.copy()
    detail_work["candidate_source"] = "reference_detail"
    seed_work["candidate_source"] = "seed_article"

    all_columns = list(detail_work.columns)
    for column in seed_work.columns:
        if column not in all_columns:
            all_columns.append(column)
    for frame in (detail_work, seed_work):
        for column in all_columns:
            if column not in frame.columns:
                frame[column] = ""

    rows: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in pd.concat([detail_work[all_columns], seed_work[all_columns]], ignore_index=True).iterrows():
        record = row.to_dict()
        key = _candidate_identity_key(record)
        if not key:
            key = f"row:{index}"
        if key in seen:
            continue
        seen.add(key)
        rows.append(record)
    return pd.DataFrame(rows, columns=all_columns).reset_index(drop=True)


def append_stage7_forced_includes(stage2_yes_df: pd.DataFrame, forced_include_df: pd.DataFrame) -> pd.DataFrame:
    if forced_include_df.empty:
        return stage2_yes_df.copy()

    base = stage2_yes_df.copy()
    forced = forced_include_df.copy()
    if "resource_name" not in forced.columns and "database_name" in forced.columns:
        forced["resource_name"] = forced["database_name"]

    required_columns = [
        "title",
        "resource_name",
        "doi",
        "pmid",
        "pmcid",
        "pubYear",
        "web",
        "abstractText",
        "AI_reason",
        "AI_check",
        "candidate_source",
        "force_reason",
    ]
    all_columns = list(base.columns)
    for column in list(forced.columns) + required_columns:
        if column not in all_columns:
            all_columns.append(column)
    for frame in (base, forced):
        for column in all_columns:
            if column not in frame.columns:
                frame[column] = ""

    seen = {
        key
        for key in (
            _candidate_identity_key(row, include_resource_name=True)
            for row in base[all_columns].to_dict("records")
        )
        if key
    }
    appended: List[Dict[str, Any]] = []
    for row in forced[all_columns].to_dict("records"):
        title = _clean_text(row.get("title"))
        resource_name = _clean_resource_name(row.get("resource_name"))
        if not title and resource_name:
            row["title"] = resource_name
        if not resource_name and title:
            row["resource_name"] = title
        if not _clean_text(row.get("abstractText")):
            row["abstractText"] = _clean_text(
                " ".join(
                    part
                    for part in (
                        "Legacy forced include.",
                        _clean_text(_first_identity_value(row.get("resource_name"), row.get("title"))),
                        _clean_text(row.get("doi")),
                        _clean_text(row.get("force_reason")),
                    )
                    if part
                )
            )
        row["AI_check"] = "Yes"
        if not _clean_text(row.get("AI_reason")):
            row["AI_reason"] = _clean_text(row.get("force_reason")) or "legacy forced include"
        row["candidate_source"] = "forced_include"

        key = _candidate_identity_key(row, include_resource_name=True)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        appended.append({column: row.get(column, "") for column in all_columns})

    if not appended:
        return base[all_columns].reset_index(drop=True)
    return pd.concat([base[all_columns], pd.DataFrame(appended, columns=all_columns)], ignore_index=True)


def _empty_agent_input_record() -> Dict[str, Any]:
    record = {column: "" for column in AGENT_INPUT_COLUMNS}
    record[ORIGINAL_DATABASE_COLUMN] = "no"
    record[POLICY_VERSION_COLUMN] = SCREENING_POLICY_VERSION
    return record


def build_agent_input_record(row: Dict[str, Any], row_id: int) -> Dict[str, Any]:
    record = _empty_agent_input_record()
    record.update(
        {
            "<main,t-word-id> id": row_id,
            "<main,t-word> title": _clean_text(row.get("title")),
            "<main,t-word> database_name": _clean_resource_name(row.get("resource_name")),
            "<main,t-word-url> database_url": extract_database_url(
                row.get("abstractText"),
                row.get("title"),
                row.get("AI_reason"),
            ),
            "<sub,t-word-doi> doi": _clean_doi(row.get("doi")),
            "<sub,t-word-pmid> pmid": _clean_int_identifier(row.get("pmid")),
            "<sub,t-numeric> year": _clean_year(row.get("pubYear")),
        }
    )
    return record


def build_curated_agent_input_record(row: Dict[str, Any]) -> Dict[str, Any]:
    record = _empty_agent_input_record()
    original_value = _clean_stage8_text(row.get(ORIGINAL_DATABASE_COLUMN)).lower()
    record.update(
        {
            "<main,t-word-id> id": _clean_year(row.get("<main,t-word-id> id")),
            "<main,t-word> title": _clean_stage8_text(row.get("<main,t-word> title")),
            "<main,t-word> database_name": _clean_stage8_text(
                row.get("<main,t-word> database_name")
            ),
            "<main,t-word-url> database_url": _clean_stage8_text(
                row.get("<main,t-word-url> database_url")
            ),
            "<sub,t-word-doi> doi": _clean_stage8_text(row.get("<sub,t-word-doi> doi")),
            "<sub,t-word-pmid> pmid": _clean_stage8_text(row.get("<sub,t-word-pmid> pmid")),
            "<sub,t-numeric> year": _clean_year(row.get("<sub,t-numeric> year")),
            ORIGINAL_DATABASE_COLUMN: original_value if original_value in {"yes", "no"} else "no",
        }
    )
    return record


def _sort_agent_input_rows(rows: List[Dict[str, Any]], target_ids: List[str]) -> pd.DataFrame:
    by_id = {str(row.get("<main,t-word-id> id", "")).strip(): row for row in rows}
    ordered = [by_id[row_id] for row_id in target_ids if row_id in by_id]
    result = pd.DataFrame(ordered, columns=AGENT_INPUT_COLUMNS)
    if result.empty:
        return pd.DataFrame(columns=AGENT_INPUT_COLUMNS)
    result = result.where(pd.notna(result), "")
    for column in ("<main,t-word-id> id", "<sub,t-numeric> year"):
        result[column] = result[column].apply(_clean_year)
    return result


def prepare_agent_input_dataframe(
    stage2_yes_df: pd.DataFrame,
    checkpoint_path: Path,
    state: PipelineState,
    max_rows: Optional[int] = None,
    max_new_rows: Optional[int] = None,
    input_format: str = "stage2",
) -> pd.DataFrame:
    if input_format not in {"stage2", "curated"}:
        raise ValueError(f"Unsupported Stage 07 input format: {input_format}")
    work = stage2_yes_df.head(max_rows).copy() if max_rows else stage2_yes_df.copy()
    rows = read_csv_rows(checkpoint_path)
    if rows:
        versions = {
            _clean_stage8_text(row.get(POLICY_VERSION_COLUMN))
            for row in rows
        }
        if versions != {SCREENING_POLICY_VERSION}:
            found = ", ".join(sorted(value for value in versions if value)) or "legacy/unversioned"
            raise ValueError(
                "Stage 07 checkpoint screening policy mismatch: "
                f"expected {SCREENING_POLICY_VERSION}, found {found}. Use a new run ID."
            )

    if input_format == "curated":
        id_column = "<main,t-word-id> id"
        if id_column not in work.columns:
            raise ValueError(f"Curated Stage 07 input is missing required column: {id_column}")
        target_ids = [_clean_int_identifier(value) for value in work[id_column].tolist()]
        if any(not row_id for row_id in target_ids):
            raise ValueError("Curated Stage 07 input contains a blank id")
        if len(set(target_ids)) != len(target_ids):
            raise ValueError("Curated Stage 07 input contains duplicate ids")
        input_rows = list(zip(target_ids, work.to_dict("records")))
    else:
        target_ids = [str(index) for index in range(1, len(work) + 1)]
        input_rows = [
            (str(row_id), row)
            for row_id, row in enumerate(work.to_dict("records"), start=1)
        ]
    checkpoint_completed = {
        str(row.get("<main,t-word-id> id", "")).strip()
        for row in rows
        if str(row.get("<main,t-word-id> id", "")).strip()
    }
    if checkpoint_completed:
        state.mark_keys_complete("agent_prep_rows", checkpoint_completed)
    completed = state.completed_keys("agent_prep_rows") | checkpoint_completed

    pending = [
        (row_id, row)
        for row_id, row in input_rows
        if str(row_id) not in completed
    ]
    if max_new_rows is not None:
        pending = pending[: max(0, int(max_new_rows))]

    for row_id, row in pending:
        record = (
            build_curated_agent_input_record(row)
            if input_format == "curated"
            else build_agent_input_record(row, int(row_id))
        )
        append_csv_row(checkpoint_path, record, AGENT_INPUT_COLUMNS)
        rows.append({field: record.get(field, "") for field in AGENT_INPUT_COLUMNS})
        state.mark_key_complete("agent_prep_rows", str(row_id))

    return _sort_agent_input_rows(rows, target_ids)


def write_agent_input_chunks(df: pd.DataFrame, chunks_dir: Path, chunk_size: int) -> List[Path]:
    if chunk_size <= 0:
        raise ValueError("stage7 chunk size must be greater than zero.")
    chunks_dir.mkdir(parents=True, exist_ok=True)
    for stale_path in chunks_dir.glob("07_agent_input_part_*.xlsx"):
        stale_path.unlink()
    chunk_paths: List[Path] = []
    for start in range(0, len(df), chunk_size):
        chunk_number = len(chunk_paths) + 1
        chunk_path = chunks_dir / f"07_agent_input_part_{chunk_number:03d}.xlsx"
        write_excel(df.iloc[start : start + chunk_size].copy(), chunk_path)
        chunk_paths.append(chunk_path)
    return chunk_paths


def write_agent_input_summary(
    summary_path: Path,
    row_count: int,
    input_path: Path,
    all_output_path: Path,
    chunks_dir: Path,
    chunk_paths: List[Path],
    chunk_size: int,
    prompt_copy_path: Optional[Path],
    input_format: str,
) -> Dict[str, Any]:
    summary = {
        "rows": int(row_count),
        "chunk_size": int(chunk_size),
        "chunks": len(chunk_paths),
        "input_path": str(input_path),
        "output_path": str(all_output_path),
        "chunks_dir": str(chunks_dir),
        "chunk_files": [str(path) for path in chunk_paths],
        "prompt_path": str(prompt_copy_path) if prompt_copy_path else "",
        "input_format": input_format,
        "screening_policy_version": SCREENING_POLICY_VERSION,
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _stage8_chunk_id(input_path: Path) -> str:
    match = re.search(r"07_agent_input_part_(\d+)\.xlsx$", input_path.name)
    if not match:
        raise ValueError(f"Invalid Stage 07 chunk filename: {input_path.name}")
    return match.group(1)


def discover_stage8_input_chunks(input_dir: Path) -> List[Path]:
    if not input_dir.exists():
        raise FileNotFoundError(f"Stage 08 input directory is missing: {input_dir}")
    return sorted(input_dir.glob("07_agent_input_part_*.xlsx"))


def stage8_result_path_for_chunk(result_dir: Path, chunk_id: str) -> Path:
    return result_dir / f"08_result_part_{int(chunk_id):03d}.xlsx"


def _read_stage8_checkpoint(checkpoint_path: Path) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for row in read_csv_rows(checkpoint_path):
        chunk_id = _clean_text(row.get("chunk_id"))
        if chunk_id:
            latest[chunk_id] = row
    return latest


def _read_agent_excel(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path, dtype=object)
    return df.where(pd.notna(df), "")


def _agent_ids(df: pd.DataFrame) -> List[str]:
    return [_clean_int_identifier(value) for value in df["<main,t-word-id> id"].tolist()]


def _validate_allowed_values(df: pd.DataFrame, column: str, allowed: set) -> Optional[str]:
    invalid = []
    for value in df[column].tolist():
        normalized = _clean_stage8_text(value).lower()
        if normalized not in allowed:
            invalid.append(_clean_stage8_text(value) or "<blank>")
    if invalid:
        examples = "; ".join(invalid[:5])
        return f"{column} has invalid values: {examples}"
    return None


def _validate_required_values(df: pd.DataFrame, columns: List[str]) -> Optional[str]:
    invalid = []
    for column in columns:
        for value in df[column].tolist():
            if not _clean_stage8_text(value):
                invalid.append(column)
                break
    if invalid:
        examples = "; ".join(invalid[:5])
        return f"required agent-filled columns are blank: {examples}"
    return None


def _validate_confirmation_reason(df: pd.DataFrame) -> Optional[str]:
    invalid = []
    column = "<sub,t-word> confirmation_reason"
    for value in df[column].tolist():
        cleaned = _clean_stage8_text(value)
        if not cleaned:
            invalid.append("<blank>")
        elif len(cleaned) > CONFIRMATION_REASON_MAX_CHARS:
            invalid.append(cleaned)
    if invalid:
        examples = "; ".join(invalid[:5])
        return (
            f"{column} must be non-empty and no longer than "
            f"{CONFIRMATION_REASON_MAX_CHARS} characters: {examples}"
        )
    return None


def _validate_forbidden_values(df: pd.DataFrame, column: str, forbidden: set) -> Optional[str]:
    invalid = []
    for value in df[column].tolist():
        normalized = _clean_stage8_text(value).lower()
        parts = [part.strip() for part in normalized.split(";")]
        if normalized in forbidden or any(part in forbidden for part in parts):
            invalid.append(_clean_stage8_text(value) or "<blank>")
    if invalid:
        examples = "; ".join(invalid[:5])
        return f"{column} has forbidden boolean-like values: {examples}"
    return None


def _validate_forbidden_missing_values(df: pd.DataFrame) -> Optional[str]:
    invalid = []
    for column in STAGE8_UNKNOWN_MISSING_VALUE_COLUMNS:
        for value in df[column].tolist():
            cleaned = _clean_stage8_text(value)
            normalized = cleaned.lower()
            parts = [part.strip() for part in normalized.split(";")]
            if normalized in STAGE8_FORBIDDEN_MISSING_VALUES or any(
                part in STAGE8_FORBIDDEN_MISSING_VALUES for part in parts
            ):
                invalid.append(f"{column}={cleaned or '<blank>'}")
    if invalid:
        examples = "; ".join(invalid[:5])
        return f"missing values must use unknown, not blank/none/legacy terms: {examples}"
    return None


def _validate_forbidden_placeholder_terms(df: pd.DataFrame) -> Optional[str]:
    invalid = []
    for column in STAGE8_FORBIDDEN_PLACEHOLDER_COLUMNS:
        for value in df[column].tolist():
            cleaned = _clean_stage8_text(value)
            normalized = cleaned.lower()
            if not normalized:
                continue
            parts = [part.strip() for part in normalized.split(";")]
            if "unclear" in parts:
                invalid.append(f"{column}={cleaned}")
                continue
            if any(term in normalized for term in STAGE8_FORBIDDEN_PLACEHOLDER_TERMS):
                invalid.append(f"{column}={cleaned}")
    if invalid:
        examples = "; ".join(invalid[:5])
        return f"result contains forbidden vague or legacy placeholder values: {examples}"
    return None


def _validate_immutable_agent_columns(input_df: pd.DataFrame, result_df: pd.DataFrame) -> Optional[str]:
    immutable_columns = AGENT_INPUT_COLUMNS[:9]
    for column in immutable_columns:
        if column in {"<main,t-word-id> id", "<sub,t-numeric> year"}:
            input_values = [_clean_int_identifier(value) for value in input_df[column].tolist()]
            result_values = [_clean_int_identifier(value) for value in result_df[column].tolist()]
        else:
            input_values = [_clean_stage8_text(value) for value in input_df[column].tolist()]
            result_values = [_clean_stage8_text(value) for value in result_df[column].tolist()]
        if input_values != result_values:
            return f"agent modified immutable input column: {column}"
    return None


def _validate_qualification_basis(df: pd.DataFrame) -> Optional[str]:
    invalid = []
    allowed = QUALIFICATION_BASIS_VALUES | {"not_applicable"}
    for value in df[QUALIFICATION_BASIS_COLUMN].tolist():
        cleaned = _clean_stage8_text(value).lower()
        parts = [part.strip() for part in cleaned.split(";") if part.strip()]
        if not parts or len(parts) != len(set(parts)) or any(part not in allowed for part in parts):
            invalid.append(cleaned or "<blank>")
            continue
        if "not_applicable" in parts and parts != ["not_applicable"]:
            invalid.append(cleaned)
    if invalid:
        return f"{QUALIFICATION_BASIS_COLUMN} has invalid values: {'; '.join(invalid[:5])}"
    return None


def _validate_evidence_fields(df: pd.DataFrame) -> Optional[str]:
    invalid = []
    for _, row in df.iterrows():
        evidence_url = _clean_stage8_text(row.get(EVIDENCE_URL_COLUMN))
        statement = _clean_stage8_text(row.get(EVIDENCE_STATEMENT_COLUMN))
        checked_date = _clean_stage8_text(row.get(EVIDENCE_CHECKED_DATE_COLUMN))
        if not re.fullmatch(r"https?://\S+", evidence_url, flags=re.IGNORECASE):
            invalid.append(f"invalid evidence URL: {evidence_url or '<blank>'}")
        if not EVIDENCE_STATEMENT_MIN_CHARS <= len(statement) <= EVIDENCE_STATEMENT_MAX_CHARS:
            invalid.append(f"invalid evidence statement length: {len(statement)}")
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", checked_date):
                raise ValueError
            datetime.strptime(checked_date, "%Y-%m-%d")
        except ValueError:
            invalid.append(f"invalid evidence date: {checked_date or '<blank>'}")
    if invalid:
        return "; ".join(invalid[:5])
    return None


def _validate_decision_consistency(df: pd.DataFrame) -> Optional[str]:
    invalid = []
    direct_splicing = {
        "splicing_event",
        "splice_site_or_junction",
        "splicing_regulation_or_sqtl",
    }
    for _, row in df.iterrows():
        decision = _clean_stage8_text(row.get("<main,t-bool> db_type_confirmation")).lower()
        basis = {
            part.strip()
            for part in _clean_stage8_text(row.get(QUALIFICATION_BASIS_COLUMN)).lower().split(";")
            if part.strip()
        }
        exclusion = _clean_stage8_text(row.get(EXCLUSION_CODE_COLUMN)).lower()
        focus = _clean_stage8_text(row.get("<main,t-word-tag> focus")).lower()
        gene_expression = _clean_stage8_text(
            row.get("<main,t-bool> gene_expression_available")
        ).lower()
        manual_review = _clean_stage8_text(row.get(MANUAL_REVIEW_COLUMN)).lower()
        row_id = _clean_int_identifier(row.get("<main,t-word-id> id")) or "<blank>"

        if decision == "yes":
            expected_focus = "as_focused" if basis & direct_splicing else "transcriptomics_general"
            if (
                basis == {"not_applicable"}
                or exclusion != "not_applicable"
                or focus != expected_focus
                or manual_review != "no"
            ):
                invalid.append(f"id {row_id} has inconsistent yes decision")
        elif decision == "no":
            if (
                basis != {"not_applicable"}
                or exclusion == "not_applicable"
                or focus != "unknown"
                or gene_expression != "no"
                or (manual_review == "yes" and exclusion != "insufficient_evidence")
            ):
                invalid.append(f"id {row_id} has inconsistent no decision")
    if invalid:
        return "; ".join(invalid[:5])
    return None


def _validate_manual_review_limit(df: pd.DataFrame) -> Optional[str]:
    review_count = sum(
        _clean_stage8_text(value).lower() == "yes"
        for value in df[MANUAL_REVIEW_COLUMN].tolist()
    )
    allowed = int(len(df) * 0.05)
    if review_count > allowed:
        return f"manual review limit exceeded: {review_count}/{len(df)} rows, maximum {allowed}"
    return None


def validate_stage8_result_chunk(input_path: Path, result_path: Path) -> Dict[str, Any]:
    if not result_path.exists():
        return {
            "status": "pending",
            "input_rows": "",
            "result_rows": "",
            "error": "result file missing",
        }
    try:
        input_df = _read_agent_excel(input_path)
        result_df = _read_agent_excel(result_path)
    except Exception as exc:
        return {
            "status": "invalid",
            "input_rows": "",
            "result_rows": "",
            "error": f"read failed: {exc}",
        }

    input_rows = len(input_df)
    result_rows = len(result_df)
    if list(input_df.columns) != AGENT_INPUT_COLUMNS:
        return {
            "status": "invalid",
            "input_rows": input_rows,
            "result_rows": result_rows,
            "error": "input schema does not match Stage 07 schema",
        }
    if list(result_df.columns) != AGENT_INPUT_COLUMNS:
        return {
            "status": "invalid",
            "input_rows": input_rows,
            "result_rows": result_rows,
            "error": "result schema does not match Stage 07 schema",
        }
    if input_rows != result_rows:
        return {
            "status": "invalid",
            "input_rows": input_rows,
            "result_rows": result_rows,
            "error": f"row count mismatch: input={input_rows}, result={result_rows}",
        }
    if _agent_ids(input_df) != _agent_ids(result_df):
        return {
            "status": "invalid",
            "input_rows": input_rows,
            "result_rows": result_rows,
            "error": "id order mismatch",
        }

    immutable_error = _validate_immutable_agent_columns(input_df, result_df)
    if immutable_error:
        return {
            "status": "invalid",
            "input_rows": input_rows,
            "result_rows": result_rows,
            "error": immutable_error,
        }

    for error in (
        _validate_required_values(result_df, STAGE8_AGENT_FILLED_COLUMNS),
        _validate_confirmation_reason(result_df),
    ):
        if error:
            return {
                "status": "invalid",
                "input_rows": input_rows,
                "result_rows": result_rows,
                "error": error,
            }

    for column, allowed in (
        (ORIGINAL_DATABASE_COLUMN, {"yes", "no"}),
        (POLICY_VERSION_COLUMN, {SCREENING_POLICY_VERSION}),
        ("<main,t-word-tag> accessibility", {"live", "dead"}),
        ("<main,t-bool> db_type_confirmation", {"yes", "no"}),
        (EXCLUSION_CODE_COLUMN, EXCLUSION_CODE_VALUES),
        (EVIDENCE_SOURCE_TYPE_COLUMN, EVIDENCE_SOURCE_TYPE_VALUES),
        (MANUAL_REVIEW_COLUMN, {"yes", "no"}),
        ("<main,t-word-tag> neural_link", {"primary", "partial", "none"}),
        ("<main,t-word-tag> focus", {"as_focused", "transcriptomics_general", "unknown"}),
        ("<main,t-bool> gene_expression_available", {"yes", "no"}),
        ("<main,t-word-tag> sequencing_resolution", {"bulk", "single_cell", "bulk;single_cell"}),
        ("<main,t-word-tag> read_technology", {"short", "long", "long;short"}),
    ):
        error = _validate_allowed_values(result_df, column, allowed)
        if error:
            return {
                "status": "invalid",
                "input_rows": input_rows,
                "result_rows": result_rows,
                "error": error,
            }

    for column in (
        "<sub,t-word-tag> disease_association",
        "<sub,t-word-tag> developmental_association",
    ):
        error = _validate_forbidden_values(
            df=result_df,
            column=column,
            forbidden={"yes", "no", "true", "false", "unclear"},
        )
        if error:
            return {
                "status": "invalid",
                "input_rows": input_rows,
                "result_rows": result_rows,
                "error": error,
            }

    error = _validate_forbidden_missing_values(result_df)
    if error:
        return {
            "status": "invalid",
            "input_rows": input_rows,
            "result_rows": result_rows,
            "error": error,
        }

    error = _validate_forbidden_placeholder_terms(result_df)
    if error:
        return {
            "status": "invalid",
            "input_rows": input_rows,
            "result_rows": result_rows,
            "error": error,
        }

    for error in (
        _validate_qualification_basis(result_df),
        _validate_evidence_fields(result_df),
        _validate_decision_consistency(result_df),
        _validate_manual_review_limit(result_df),
    ):
        if error:
            return {
                "status": "invalid",
                "input_rows": input_rows,
                "result_rows": result_rows,
                "error": error,
            }

    return {
        "status": "complete",
        "input_rows": input_rows,
        "result_rows": result_rows,
        "error": "",
    }


def scan_stage8_chunks(
    input_dir: Path,
    result_dir: Path,
    checkpoint_path: Path,
    state: PipelineState,
) -> pd.DataFrame:
    input_paths = discover_stage8_input_chunks(input_dir)
    if not input_paths:
        raise FileNotFoundError(f"No Stage 07 chunk files found in: {input_dir}")
    result_dir.mkdir(parents=True, exist_ok=True)
    previous = _read_stage8_checkpoint(checkpoint_path)
    rows: List[Dict[str, Any]] = []

    for input_path in input_paths:
        chunk_id = _stage8_chunk_id(input_path)
        result_path = stage8_result_path_for_chunk(result_dir, chunk_id)
        validation = validate_stage8_result_chunk(input_path, result_path)
        prior = previous.get(chunk_id, {})
        status = validation["status"]
        completed_at = _clean_text(prior.get("completed_at"))
        if status == "complete":
            if not completed_at:
                completed_at = _now_iso()
            state.mark_key_complete("agent_curation_chunks", chunk_id)

        rows.append(
            {
                "chunk_id": chunk_id,
                "input_path": str(input_path),
                "result_path": str(result_path),
                "status": status,
                "agent_id": _clean_text(prior.get("agent_id")),
                "assigned_at": _clean_text(prior.get("assigned_at")),
                "completed_at": completed_at if status == "complete" else "",
                "input_rows": validation.get("input_rows", ""),
                "result_rows": validation.get("result_rows", ""),
                "error": validation.get("error", ""),
            }
        )

    manifest_df = pd.DataFrame(rows, columns=STAGE8_CHECKPOINT_COLUMNS)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_df.to_csv(checkpoint_path, index=False, encoding="utf-8")
    return manifest_df


def merge_stage8_results(manifest_df: pd.DataFrame, output_path: Path) -> pd.DataFrame:
    if manifest_df.empty:
        raise ValueError("No Stage 08 chunks to merge.")
    incomplete = manifest_df[manifest_df["status"] != "complete"]
    if not incomplete.empty:
        raise ValueError(f"Cannot merge: {len(incomplete)} Stage 08 chunks are incomplete or invalid.")

    frames = []
    for result_path in manifest_df["result_path"].tolist():
        frame = _read_agent_excel(Path(result_path))
        frames.append(frame)
    merged = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=AGENT_INPUT_COLUMNS)
    ids = [_clean_int_identifier(value) for value in merged["<main,t-word-id> id"].tolist()]
    if len(ids) != len(set(ids)):
        raise ValueError("Cannot merge: duplicate ids found in Stage 08 results.")
    review_error = _validate_manual_review_limit(merged)
    if review_error:
        raise ValueError(f"Cannot merge: {review_error}")
    merged["_sort_id"] = [int(value) if str(value).isdigit() else 0 for value in ids]
    merged = merged.sort_values("_sort_id").drop(columns=["_sort_id"]).reset_index(drop=True)
    write_excel(merged, output_path)
    return merged


def write_stage8_summary(
    summary_path: Path,
    manifest_df: pd.DataFrame,
    input_dir: Path,
    result_dir: Path,
    merged_output_path: Path,
    agent_count: int,
    merged_rows: Optional[int] = None,
) -> Dict[str, Any]:
    status_counts = {
        str(key): int(value)
        for key, value in manifest_df["status"].value_counts(dropna=False).sort_index().items()
    } if "status" in manifest_df.columns else {}
    pending_df = manifest_df[manifest_df["status"] != "complete"] if "status" in manifest_df.columns else manifest_df
    summary = {
        "chunks": int(len(manifest_df)),
        "screening_policy_version": SCREENING_POLICY_VERSION,
        "status_counts": status_counts,
        "agent_count": int(agent_count),
        "input_dir": str(input_dir),
        "result_dir": str(result_dir),
        "merged_output_path": str(merged_output_path),
        "merged_rows": int(merged_rows) if merged_rows is not None else 0,
        "merge_ready": bool(not manifest_df.empty and pending_df.empty),
        "pending_chunks": pending_df["chunk_id"].astype(str).tolist() if "chunk_id" in pending_df.columns else [],
        "pending_assignments": [
            {
                "chunk_id": str(row["chunk_id"]),
                "input_path": str(row["input_path"]),
                "result_path": str(row["result_path"]),
                "status": str(row["status"]),
                "error": str(row.get("error", "")),
            }
            for _, row in pending_df.iterrows()
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def write_stage2_summary(df: pd.DataFrame, output_path: Path, config: Dict[str, Any]) -> Dict[str, Any]:
    ai_check = df["AI_check"].astype(str).str.strip().str.lower() if "AI_check" in df.columns else pd.Series([])
    class_counts = {}
    if "AI_class" in df.columns:
        class_counts = {
            str(key): int(value)
            for key, value in df["AI_class"].astype(str).value_counts(dropna=False).sort_index().items()
        }
    summary = {
        "rows": int(len(df)),
        "screening_policy_version": config["screening_policy_version"],
        "yes_rows": int((ai_check == "yes").sum()) if not ai_check.empty else 0,
        "no_rows": int((ai_check == "no").sum()) if not ai_check.empty else 0,
        "class_counts": class_counts,
        "model": config["openai_model"],
        "service_tier": config["stage2_service_tier"],
        "reasoning_effort": config["stage2_reasoning_effort"],
        "prompt_cache_key": config["stage2_prompt_cache_key"],
        "prompt_cache_retention": config["stage2_prompt_cache_retention"],
        "stage2_input_tokens": numeric_sum(df, "stage2_input_tokens"),
        "stage2_cached_tokens": numeric_sum(df, "stage2_cached_tokens"),
        "stage2_output_tokens": numeric_sum(df, "stage2_output_tokens"),
        "stage2_reasoning_tokens": numeric_sum(df, "stage2_reasoning_tokens"),
        "stage2_total_tokens": numeric_sum(df, "stage2_total_tokens"),
    }
    output_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def run_stage7_only(paths: Any, state: PipelineState, config: Dict[str, Any], logger: Any) -> Path:
    stage7_input = (
        Path(config["stage7_input"])
        if config.get("stage7_input")
        else paths.outputs_dir / OUTPUT_FILES["stage2_ai_yes_only"]
    )
    agent_output = paths.outputs_dir / OUTPUT_FILES["agent_input_all"]
    chunks_dir = paths.outputs_dir / OUTPUT_DIRS["agent_input_chunks"]
    summary_output = paths.outputs_dir / OUTPUT_FILES["agent_input_summary"]

    agent_df = maybe_load_completed_stage(
        state,
        "agent_table_prep",
        agent_output,
        config["resume"],
        logger,
    )
    if agent_df is None:
        logger.info("Stage 07: prepare agent curation input tables")
        state.set_current_stage("agent_table_prep")
        input_df = require_stage("stage2_ai_yes_only", stage7_input)
        input_format = config.get("stage7_input_format", "stage2")
        forced_include_path = config.get("stage7_forced_include")
        if input_format == "curated" and forced_include_path:
            raise ValueError("--stage7-forced-include cannot be used with curated Stage 07 input")
        if forced_include_path:
            forced_include_df = read_excel(Path(forced_include_path))
            before_rows = len(input_df)
            input_df = append_stage7_forced_includes(input_df, forced_include_df)
            forced_added = len(input_df) - before_rows
            state.set_count("agent_table_prep_forced_include_rows", forced_added)
            logger.info(
                "Stage 07 forced include rows added: %s from %s",
                forced_added,
                forced_include_path,
            )
        target_rows = len(input_df.head(config["max_rows"])) if config["max_rows"] else len(input_df)
        state.set_count("agent_table_prep_target_rows", target_rows)
        logger.info("Stage 07 input rows: %s", target_rows)

        agent_df = prepare_agent_input_dataframe(
            input_df,
            paths.checkpoints_dir / CHECKPOINT_FILES["agent_table_prep"],
            state,
            max_rows=config["max_rows"],
            max_new_rows=config.get("stage7_max_new_rows"),
            input_format=input_format,
        )
        state.set_count("agent_table_prep_rows", len(agent_df))
        if len(agent_df) < target_rows:
            logger.info(
                "Stage 07 partial: %s/%s rows complete. Resume the same run to continue.",
                len(agent_df),
                target_rows,
            )
            logger.info("Pipeline paused before final Stage 07 outputs: %s", paths.run_dir)
            return paths.run_dir

        write_excel(agent_df, agent_output)
        chunk_paths = write_agent_input_chunks(agent_df, chunks_dir, int(config["stage7_chunk_size"]))

        prompt_copy_path = None
        prompt_source_path = DATA_COLLECTION_DIR / "transcript_database_curation_prompt.md"
        if prompt_source_path.exists():
            prompt_copy_path = chunks_dir / prompt_source_path.name
            shutil.copyfile(prompt_source_path, prompt_copy_path)

        summary = write_agent_input_summary(
            summary_output,
            len(agent_df),
            stage7_input,
            agent_output,
            chunks_dir,
            chunk_paths,
            int(config["stage7_chunk_size"]),
            prompt_copy_path,
            input_format,
        )
        state.set_output_path("agent_input_chunks", chunks_dir)
        state.set_output_path("agent_input_summary", summary_output)
        state.set_count("agent_input_chunks", int(summary["chunks"]))
        complete_stage(state, "agent_table_prep", "agent_input_all", agent_output, agent_df)
        logger.info("Stage 07 agent input rows: %s", len(agent_df))
        logger.info("Stage 07 chunk files: %s", len(chunk_paths))

    logger.info("Stage 07 complete: %s", paths.run_dir)
    return paths.run_dir


def run_stage8_only(paths: Any, state: PipelineState, config: Dict[str, Any], logger: Any) -> Path:
    input_dir = (
        Path(config["stage8_input_dir"])
        if config.get("stage8_input_dir")
        else paths.outputs_dir / OUTPUT_DIRS["agent_input_chunks"]
    )
    result_dir = paths.outputs_dir / OUTPUT_DIRS["agent_result_chunks"]
    finalization_enabled = bool(config.get("stage8_finalize_after_merge"))
    merged_output = (
        paths.run_dir / "intermediate" / "08_raw_merged.xlsx"
        if finalization_enabled
        else paths.outputs_dir / OUTPUT_FILES["agent_merged"]
    )
    summary_output = paths.outputs_dir / OUTPUT_FILES["agent_merged_summary"]
    checkpoint_path = paths.checkpoints_dir / CHECKPOINT_FILES["agent_web_curation"]

    merged_df = maybe_load_completed_stage(
        state,
        "agent_web_curation",
        merged_output,
        config["resume"],
        logger,
    )
    if merged_df is not None:
        if finalization_enabled:
            finalization_config = dict(config)
            finalization_config["stage8_finalize_input"] = str(merged_output)
            finalization_config["resume"] = True
            return run_stage8_finalization_only(paths, state, finalization_config, logger)
        logger.info("Stage 08 complete: %s", paths.run_dir)
        return paths.run_dir

    logger.info("Stage 08: scan agent curation chunks and merge completed results")
    state.set_current_stage("agent_web_curation")
    manifest_df = scan_stage8_chunks(input_dir, result_dir, checkpoint_path, state)
    state.set_count("agent_web_curation_chunks", len(manifest_df))
    status_counts = manifest_df["status"].value_counts().to_dict()
    for status, count in sorted(status_counts.items()):
        state.set_count(f"agent_web_curation_{status}_chunks", int(count))
    logger.info("Stage 08 chunks: %s", len(manifest_df))
    logger.info("Stage 08 status counts: %s", status_counts)

    incomplete = manifest_df[manifest_df["status"] != "complete"]
    if not incomplete.empty:
        write_stage8_summary(
            summary_output,
            manifest_df,
            input_dir,
            result_dir,
            merged_output,
            int(config["stage8_agent_count"]),
        )
        logger.info(
            "Stage 08 partial: %s/%s chunks complete. Use Codex sub-agents for pending chunks, then resume.",
            int((manifest_df["status"] == "complete").sum()),
            len(manifest_df),
        )
        logger.info("Stage 08 result directory: %s", result_dir)
        return paths.run_dir

    merged_df = merge_stage8_results(manifest_df, merged_output)
    summary = write_stage8_summary(
        summary_output,
        manifest_df,
        input_dir,
        result_dir,
        merged_output,
        int(config["stage8_agent_count"]),
        merged_rows=len(merged_df),
    )
    state.set_output_path("agent_result_chunks", result_dir)
    state.set_output_path("agent_merged_summary", summary_output)
    state.set_count("agent_web_curation_rows", len(merged_df))
    state.set_count("agent_web_curation_complete_chunks", int(summary["status_counts"].get("complete", 0)))
    complete_stage(state, "agent_web_curation", "agent_merged", merged_output, merged_df)
    logger.info("Stage 08 merged rows: %s", len(merged_df))
    if finalization_enabled:
        finalization_config = dict(config)
        finalization_config["stage8_finalize_input"] = str(merged_output)
        finalization_config["resume"] = True
        return run_stage8_finalization_only(paths, state, finalization_config, logger)
    logger.info("Stage 08 complete: %s", paths.run_dir)
    return paths.run_dir


def run_stage8_finalization_only(paths: Any, state: PipelineState, config: Dict[str, Any], logger: Any) -> Path:
    input_value = config.get("stage8_finalize_input")
    if not input_value:
        raise ValueError("--stage8-finalize-input is required with --stage8-finalize-only")
    input_path = Path(input_value).resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Stage 8 finalization input not found: {input_path}")

    intermediate_dir = paths.run_dir / "intermediate"
    raw_path = intermediate_dir / "08_raw_merged.xlsx"
    accessibility_path = paths.outputs_dir / OUTPUT_FILES["accessibility_audit"]
    duplicate_audit_path = paths.outputs_dir / OUTPUT_FILES["duplicate_merge_audit"]
    review_all_path = paths.outputs_dir / OUTPUT_FILES["final_review_input_all"]
    review_input_dir = paths.outputs_dir / OUTPUT_DIRS["final_review_input_chunks"]
    review_result_dir = paths.outputs_dir / OUTPUT_DIRS["final_review_result_chunks"]
    incremental_input_path = paths.outputs_dir / "08_incremental_duplicate_input.xlsx"
    incremental_result_path = paths.outputs_dir / "08_incremental_duplicate_result.xlsx"
    checkpoint_path = paths.checkpoints_dir / CHECKPOINT_FILES["agent_final_review"]
    final_path = paths.outputs_dir / OUTPUT_FILES["agent_merged"]
    summary_path = paths.outputs_dir / OUTPUT_FILES["agent_merged_summary"]

    completed = maybe_load_completed_stage(
        state,
        "agent_final_review",
        final_path,
        config["resume"],
        logger,
    )
    if completed is not None:
        logger.info("Stage 08 finalization complete: %s", paths.run_dir)
        return paths.run_dir

    logger.info("Stage 08 finalization: accessibility, evidence, and duplicate review")
    state.set_current_stage("agent_final_review")
    initialize_raw_snapshot(input_path, raw_path)
    raw_df = _read_agent_excel(raw_path)
    if list(raw_df.columns) != AGENT_INPUT_COLUMNS:
        raise ValueError("Stage 8 finalization input must have the standard 30-column schema.")

    audit_was_augmented = False
    if accessibility_path.exists():
        accessibility_df = pd.read_excel(accessibility_path, dtype=object).fillna("")
        if "evidence_status" not in accessibility_df.columns:
            existing_results = list(review_result_dir.glob("08_final_review_result_part_*.xlsx"))
            if existing_results:
                raise ValueError(
                    "Existing final-review results were created from a database-only accessibility audit. "
                    "Use a new run ID so evidence URL checks can rebuild the review set safely."
                )
            logger.info("Stage 08 finalization: supplement legacy audit with evidence URL checks")
            accessibility_df = augment_evidence_accessibility_audit(
                raw_df,
                accessibility_df,
                workers=int(config["stage8_url_workers"]),
                timeout=float(config["stage8_url_timeout"]),
            )
            write_excel_with_hyperlinks(accessibility_df, accessibility_path)
            audit_was_augmented = True
    else:
        accessibility_df = run_accessibility_audit(
            raw_df,
            workers=int(config["stage8_url_workers"]),
            timeout=float(config["stage8_url_timeout"]),
        )
        write_excel_with_hyperlinks(accessibility_df, accessibility_path)
    if len(accessibility_df) != len(raw_df):
        raise ValueError("Accessibility audit must contain exactly one row per finalization input row.")

    review_df, duplicate_candidates = prepare_final_review_dataframe(
        raw_df,
        accessibility_df,
        AGENT_INPUT_COLUMNS,
    )
    if audit_was_augmented:
        review_all_path.unlink(missing_ok=True)
        checkpoint_path.unlink(missing_ok=True)
        for stale_input in review_input_dir.glob("08_final_review_input_part_*.xlsx"):
            stale_input.unlink()
    if not review_all_path.exists():
        write_excel_with_hyperlinks(review_df, review_all_path)
    if not list(review_input_dir.glob("08_final_review_input_part_*.xlsx")):
        write_final_review_chunks(review_df, review_input_dir)
    if not duplicate_audit_path.exists():
        write_excel_with_hyperlinks(duplicate_candidates, duplicate_audit_path)

    manifest = scan_final_review_chunks(
        review_input_dir,
        review_result_dir,
        checkpoint_path,
        AGENT_INPUT_COLUMNS,
    )
    state.set_count("agent_final_review_rows", len(review_df))
    state.set_count("agent_final_review_chunks", len(manifest))
    state.set_count("agent_final_review_complete_chunks", int((manifest["status"] == "complete").sum()) if not manifest.empty else 0)
    incomplete = manifest[manifest["status"] != "complete"] if not manifest.empty else manifest
    if not incomplete.empty:
        write_finalization_summary(
            summary_path,
            raw_df,
            None,
            manifest,
            duplicate_candidates,
            input_path,
        )
        logger.info(
            "Stage 08 finalization partial: %s/%s review chunks complete",
            int((manifest["status"] == "complete").sum()),
            len(manifest),
        )
        logger.info("Final review result directory: %s", review_result_dir)
        return paths.run_dir

    accessibility_df = enrich_accessibility_audit(accessibility_df, manifest)
    write_excel_with_hyperlinks(accessibility_df, accessibility_path)

    final_df, duplicate_decisions = merge_final_review(raw_df, manifest, AGENT_INPUT_COLUMNS)
    incremental_input_df = prepare_incremental_duplicate_review(
        final_df,
        canonical_audit=duplicate_decisions,
    )
    if not incremental_input_df.empty:
        if not incremental_input_path.exists():
            write_excel_with_hyperlinks(incremental_input_df, incremental_input_path)
        if not incremental_result_path.exists():
            write_finalization_summary(
                summary_path,
                raw_df,
                None,
                manifest,
                duplicate_candidates,
                input_path,
            )
            summary_data = json.loads(summary_path.read_text(encoding="utf-8"))
            summary_data["incremental_duplicate_review_pending"] = True
            summary_data["incremental_duplicate_candidate_groups"] = int(
                incremental_input_df["<audit,t-word-tag> incremental_candidate_group"].nunique()
            )
            summary_path.write_text(json.dumps(summary_data, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info(
                "Stage 08 finalization pending incremental duplicate review: %s groups",
                summary_data["incremental_duplicate_candidate_groups"],
            )
            logger.info("Incremental duplicate input: %s", incremental_input_path)
            return paths.run_dir
        incremental_result_df = pd.read_excel(incremental_result_path, dtype=object).fillna("")
        final_df, incremental_audit = apply_incremental_duplicate_decisions(
            final_df,
            incremental_result_df,
            canonical_audit=duplicate_decisions,
        )
        if not incremental_audit.empty:
            duplicate_decisions = pd.concat(
                [duplicate_decisions, incremental_audit],
                ignore_index=True,
                sort=False,
            )
    temp_path = final_path.with_suffix(".tmp.xlsx")
    write_excel_with_hyperlinks(final_df, temp_path)
    temp_path.replace(final_path)
    write_excel_with_hyperlinks(duplicate_decisions, duplicate_audit_path)
    write_finalization_summary(
        summary_path,
        raw_df,
        final_df,
        manifest,
        duplicate_candidates,
        input_path,
    )
    state.set_output_path("accessibility_audit", accessibility_path)
    state.set_output_path("duplicate_merge_audit", duplicate_audit_path)
    state.set_output_path("agent_merged_summary", summary_path)
    state.set_count("agent_final_review_final_rows", len(final_df))
    complete_stage(state, "agent_final_review", "agent_merged", final_path, final_df)
    logger.info("Stage 08 finalized rows: %s", len(final_df))
    return paths.run_dir


def run_stage2_only(paths: Any, state: PipelineState, config: Dict[str, Any], logger: Any) -> Path:
    stage2_input = paths.outputs_dir / OUTPUT_FILES["stage1_pass_unclear_for_stage2"]
    stage2_output = paths.outputs_dir / OUTPUT_FILES["stage2_ai_check"]
    stage2_yes_output = paths.outputs_dir / OUTPUT_FILES["stage2_ai_yes_only"]
    stage2_summary_output = paths.outputs_dir / OUTPUT_FILES["stage2_ai_summary"]

    stage2_df = maybe_load_completed_stage(
        state,
        "stage2_ai_check",
        stage2_output,
        config["resume"],
        logger,
    )
    if stage2_df is None:
        logger.info("Stage2 expert AI check from Stage1 pass/unclear")
        state.set_current_stage("stage2_ai_check")
        input_df = require_stage("stage1_pass_unclear_for_stage2", stage2_input)
        prioritized_df = prioritize_stage2_benchmark_rows(
            input_df,
            Path(config["stage2_benchmark_path"]) if config.get("stage2_benchmark_path") else None,
        )
        benchmark_rows = (
            int(prioritized_df["stage2_benchmark_priority"].sum())
            if "stage2_benchmark_priority" in prioritized_df.columns
            else 0
        )
        state.set_count("stage2_input_rows", len(prioritized_df))
        state.set_count("stage2_benchmark_priority_rows", benchmark_rows)
        logger.info("Stage2 input papers: %s", len(prioritized_df))
        logger.info("Stage2 benchmark-priority papers: %s", benchmark_rows)

        if not config["enable_ai"]:
            stage2_df = mark_ai_skipped(prioritized_df, max_rows=config["max_rows"])
        else:
            stage2_df = stage2_screen_dataframe(
                prioritized_df,
                paths.checkpoints_dir / CHECKPOINT_FILES["stage2_ai_check"],
                state,
                model=config["openai_model"],
                prompt=config["stage2_prompt"],
                max_rows=config["max_rows"],
                max_new_rows=config.get("stage2_max_new_rows"),
                max_workers=config["stage2_workers"],
                rate_limit_per_sec=config["stage2_rate_limit_per_sec"],
                service_tier=config["stage2_service_tier"],
                reasoning_effort=config["stage2_reasoning_effort"],
                prompt_cache_key=config["stage2_prompt_cache_key"],
                prompt_cache_retention=config["stage2_prompt_cache_retention"],
                max_output_tokens=config["stage2_max_output_tokens"],
                logger=logger,
            )
        target_rows = len(prioritized_df.head(config["max_rows"])) if config["max_rows"] else len(prioritized_df)
        state.set_count("stage2_ai_check_rows", len(stage2_df))
        state.set_count("stage2_ai_check_target_rows", target_rows)
        if len(stage2_df) < target_rows:
            logger.info(
                "Stage2 partial: %s/%s rows complete. Resume the same run to continue.",
                len(stage2_df),
                target_rows,
            )
            logger.info("Pipeline paused before final Stage2 outputs: %s", paths.run_dir)
            return paths.run_dir

        stage2_df = rebuild_web_from_doi(stage2_df)
        write_excel(stage2_df, stage2_output)
        yes_df = yes_only(stage2_df)
        write_excel(yes_df, stage2_yes_output)
        summary = write_stage2_summary(stage2_df, stage2_summary_output, config)
        state.set_output_path("stage2_ai_yes_only", stage2_yes_output)
        state.set_output_path("stage2_ai_summary", stage2_summary_output)
        state.set_count("stage2_ai_yes_rows", len(yes_df))
        state.set_count("stage2_cached_tokens", int(summary["stage2_cached_tokens"]))
        complete_stage(state, "stage2_ai_check", "stage2_ai_check", stage2_output, stage2_df)
        logger.info("Stage2-screened papers: %s", len(stage2_df))
        logger.info("Stage2 Yes papers: %s", len(yes_df))

    logger.info("Stage2 complete: %s", paths.run_dir)
    return paths.run_dir


def run_pipeline(config: Dict[str, Any]) -> Path:
    paths = setup_run_paths(DATA_COLLECTION_DIR, config["run_id"], resume=config["resume"])
    logger = configure_logging(paths.log_path)
    validate_resume_policy(paths.config_path, config)
    write_config(paths.config_path, config)
    state = PipelineState.load_or_create(paths.state_path, paths.run_id)

    logger.info("Run directory: %s", paths.run_dir)
    logger.info("Search query: %s", config["search_query"])
    logger.info("Years: %s", ", ".join(str(year) for year in config["search_years"]))

    if config.get("stage2_only"):
        return run_stage2_only(paths, state, config, logger)
    if config.get("stage7_only"):
        return run_stage7_only(paths, state, config, logger)
    if config.get("stage8_only"):
        return run_stage8_only(paths, state, config, logger)
    if config.get("stage8_finalize_only"):
        return run_stage8_finalization_only(paths, state, config, logger)

    seed_output = paths.outputs_dir / OUTPUT_FILES["seed_search"]
    if stage_enabled(config, "seed_search"):
        seed_df = maybe_load_completed_stage(state, "seed_search", seed_output, config["resume"], logger)
        if seed_df is None:
            logger.info("Stage 1/5: seed search")
            state.set_current_stage("seed_search")
            seed_df = collect_seed_articles(
                config["search_query"],
                config["search_years"],
                paths.checkpoints_dir / CHECKPOINT_FILES["seed_search"],
                state,
                page_size=config["seed_page_size"],
                sort_by=config["seed_sort_by"],
                timeout=config["request_timeout"],
                max_rows=config["max_rows"],
                max_rows_per_year=config["seed_limit_per_year"],
                dedupe_order=config["seed_dedupe_order"],
                logger=logger,
            )
            write_excel(seed_df, seed_output)
            complete_stage(state, "seed_search", "seed_articles", seed_output, seed_df)
            logger.info("Seed articles: %s", len(seed_df))
    else:
        seed_df = require_stage("seed_search", seed_output)

    reference_output = paths.outputs_dir / OUTPUT_FILES["references"]
    if stage_enabled(config, "references"):
        reference_df = maybe_load_completed_stage(state, "references", reference_output, config["resume"], logger)
        if reference_df is None:
            logger.info("Stage 2/5: reference collection")
            state.set_current_stage("references")
            reference_df = collect_references_for_seeds(
                seed_df,
                paths.checkpoints_dir / CHECKPOINT_FILES["references"],
                state,
                rate_limit_per_sec=config["reference_rate_limit_per_sec"],
                timeout=config["request_timeout"],
                max_rows=config["max_rows"],
                max_workers=config["reference_workers"],
                logger=logger,
            )
            write_excel(reference_df, reference_output)
            complete_stage(state, "references", "reference_list", reference_output, reference_df)
            logger.info("References: %s", len(reference_df))
    else:
        reference_df = require_stage("references", reference_output)

    detail_output = paths.outputs_dir / OUTPUT_FILES["details"]
    if stage_enabled(config, "details"):
        detail_df = maybe_load_completed_stage(state, "details", detail_output, config["resume"], logger)
        if detail_df is None:
            logger.info("Stage 3/5: reference details")
            state.set_current_stage("details")
            detail_df = collect_reference_details(
                reference_df,
                paths.checkpoints_dir / CHECKPOINT_FILES["details"],
                state,
                rate_limit_per_sec=config["detail_rate_limit_per_sec"],
                timeout=config["request_timeout"],
                max_rows=config["max_rows"],
                max_workers=config["detail_workers"],
                batch_size=config["detail_batch_size"],
                logger=logger,
            )
            write_excel(detail_df, detail_output)
            complete_stage(state, "details", "reference_details", detail_output, detail_df)
            logger.info("Reference details after dedup: %s", len(detail_df))
    else:
        detail_df = require_stage("details", detail_output)

    keyword_output = paths.outputs_dir / OUTPUT_FILES["keyword_screen"]
    if stage_enabled(config, "keyword_screen"):
        keyword_df = maybe_load_completed_stage(state, "keyword_screen", keyword_output, config["resume"], logger)
        if keyword_df is None:
            logger.info("Stage 4/5: keyword screening")
            state.set_current_stage("keyword_screen")
            candidate_df = build_keyword_candidate_pool(seed_df, detail_df)
            state.set_count("keyword_candidate_pool_rows", len(candidate_df))
            state.set_count(
                "keyword_candidate_pool_seed_article_rows",
                int((candidate_df["candidate_source"] == "seed_article").sum()),
            )
            keyword_df = keyword_screen(candidate_df)
            if config["max_rows"]:
                keyword_df = keyword_df.head(config["max_rows"]).reset_index(drop=True)
            write_excel(keyword_df, keyword_output)
            complete_stage(state, "keyword_screen", "keyword_screened", keyword_output, keyword_df)
            logger.info("Keyword-screened papers: %s", len(keyword_df))
    else:
        keyword_df = require_stage("keyword_screen", keyword_output)

    ai_output = paths.outputs_dir / OUTPUT_FILES["ai_screen"]
    yes_output = paths.outputs_dir / OUTPUT_FILES["ai_yes_only"]
    missing_abstract_output = paths.outputs_dir / OUTPUT_FILES["missing_abstract_for_codex"]
    if stage_enabled(config, "ai_screen"):
        ai_df = maybe_load_completed_stage(state, "ai_screen", ai_output, config["resume"], logger)
        if ai_df is None:
            logger.info("Stage 5/5: AI screening")
            state.set_current_stage("ai_screen")
            if config["enable_ai"]:
                ai_input_df, missing_abstract_df = split_missing_abstract(keyword_df)
                if not missing_abstract_df.empty:
                    write_excel(missing_abstract_df, missing_abstract_output)
                    state.set_output_path("missing_abstract_for_codex", missing_abstract_output)
                    state.set_count("missing_abstract_for_codex_rows", len(missing_abstract_df))
                    logger.info(
                        "Missing-abstract papers for Codex agent: %s",
                        len(missing_abstract_df),
                    )
                ai_df = ai_screen_dataframe(
                    ai_input_df,
                    paths.checkpoints_dir / CHECKPOINT_FILES["ai_screen"],
                    state,
                    model=config["openai_model"],
                    question=config["ai_question"],
                    max_rows=config["max_rows"],
                    max_new_rows=config.get("ai_max_new_rows"),
                    max_workers=config["ai_workers"],
                    rate_limit_per_sec=config["ai_rate_limit_per_sec"],
                    logger=logger,
                )
                ai_target_rows = len(ai_input_df.head(config["max_rows"])) if config["max_rows"] else len(ai_input_df)
                state.set_count("ai_screen_rows", len(ai_df))
                state.set_count("ai_screen_target_rows", ai_target_rows)
                if len(ai_df) < ai_target_rows:
                    logger.info(
                        "AI screening partial: %s/%s rows complete. Resume the same run to continue.",
                        len(ai_df),
                        ai_target_rows,
                    )
                    logger.info("Pipeline paused before final AI outputs: %s", paths.run_dir)
                    return paths.run_dir
            else:
                ai_df = mark_ai_skipped(keyword_df, max_rows=config["max_rows"])
            ai_df = rebuild_web_from_doi(ai_df)
            write_excel(ai_df, ai_output)
            yes_df = yes_only(ai_df)
            write_excel(yes_df, yes_output)
            state.set_output_path("ai_yes_only", yes_output)
            state.set_count("ai_yes_rows", len(yes_df))
            complete_stage(state, "ai_screen", "ai_check", ai_output, ai_df)
            logger.info("AI-screened papers: %s", len(ai_df))
            logger.info("AI Yes papers: %s", len(yes_df))
    else:
        if ai_output.exists():
            ai_df = require_stage("ai_screen", ai_output)
            yes_df = yes_only(ai_df)
            write_excel(yes_df, yes_output)
        else:
            logger.info("AI screening disabled; stopping after keyword screening")

    logger.info("Pipeline complete: %s", paths.run_dir)
    return paths.run_dir


def main() -> None:
    args = parse_args()
    config = build_config(args)
    run_pipeline(config)


if __name__ == "__main__":
    main()
