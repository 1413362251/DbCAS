import argparse
import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd


MODULE_DIR = Path(__file__).resolve().parents[1]
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

from paper_search import (  # noqa: E402
    article_to_record,
    collect_reference_details,
    collect_references_for_seeds,
    collect_seed_articles,
    dedupe_reference_details,
    dedupe_seed_articles,
)
from paper_screening import (  # noqa: E402
    DEFAULT_AI_QUESTION,
    DEFAULT_STAGE1_SERVICE_TIER,
    DEFAULT_STAGE2_MAX_OUTPUT_TOKENS,
    DEFAULT_STAGE2_PROMPT_CACHE_KEY,
    DEFAULT_STAGE2_PROMPT_CACHE_RETENTION,
    DEFAULT_STAGE2_REASONING_EFFORT,
    DEFAULT_STAGE2_SERVICE_TIER,
    SCREENING_POLICY_VERSION,
    ask_yes_no,
    ask_stage1_with_usage,
    ask_stage2_with_usage,
    ai_screen_dataframe,
    keyword_screen,
    parse_ai_json_response,
    parse_stage2_json_response,
    prioritize_stage2_benchmark_rows,
    stage2_screen_dataframe,
    split_missing_abstract,
    yes_only,
)
from pipeline_runtime import PipelineState, append_csv_row  # noqa: E402
from searchscreening_pipeline import (  # noqa: E402
    AGENT_INPUT_COLUMNS,
    EVIDENCE_CHECKED_DATE_COLUMN,
    EVIDENCE_SOURCE_TYPE_COLUMN,
    EVIDENCE_STATEMENT_COLUMN,
    EVIDENCE_URL_COLUMN,
    EXCLUSION_CODE_COLUMN,
    MANUAL_REVIEW_COLUMN,
    ORIGINAL_DATABASE_COLUMN,
    POLICY_VERSION_COLUMN,
    QUALIFICATION_BASIS_COLUMN,
    append_stage7_forced_includes,
    build_config,
    build_keyword_candidate_pool,
    extract_database_url,
    merge_stage8_results,
    prepare_agent_input_dataframe,
    run_pipeline,
    scan_stage8_chunks,
    validate_stage8_result_chunk,
    write_agent_input_chunks,
)


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("HTTP should not be called for completed items")


class FakeCompletionMessage:
    def __init__(self, content):
        self.content = content


class FakeCompletionChoice:
    def __init__(self, content):
        self.message = FakeCompletionMessage(content)


class FakeCompletion:
    def __init__(self, content):
        self.choices = [FakeCompletionChoice(content)]
        self.model = "fake-model"
        self.service_tier = ""


class FakeCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeCompletion("Yes")


class FakeUsage:
    def __init__(self, payload):
        self.payload = payload

    def model_dump(self):
        return self.payload


class FakeOpenAIResponse:
    def __init__(self, output_text):
        self.output_text = output_text
        self.model = "fake-stage2-model"
        self.service_tier = "flex"
        self.usage = FakeUsage(
            {
                "input_tokens": 3000,
                "input_tokens_details": {"cached_tokens": 2816},
                "output_tokens": 60,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 3060,
            }
        )


class FakeResponses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeOpenAIResponse(
            json.dumps(
                {
                    "AI_class": 1,
                    "AI_check": "Yes",
                    "AI_reason": "AS database with URL",
                    "resource_name": "ExampleDB",
                    "resource_type": "database",
                    "url_mentioned": "Yes",
                    "target_scope_hint": "direct_splicing",
                }
            )
        )


class FakeChat:
    def __init__(self):
        self.completions = FakeCompletions()


class FakeOpenAIClient:
    def __init__(self):
        self.chat = FakeChat()
        self.responses = FakeResponses()


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakePagingSession:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        params = kwargs.get("params", {})
        cursor = params.get("cursorMark", "*")
        return FakeResponse(self.pages[cursor])


class PipelineMethodTests(unittest.TestCase):
    def make_state(self, directory: Path) -> PipelineState:
        return PipelineState.load_or_create(directory / "state.json", "test_run")

    def make_agent_rows(self, start_id: int, count: int) -> pd.DataFrame:
        rows = []
        for row_id in range(start_id, start_id + count):
            row = {column: "" for column in AGENT_INPUT_COLUMNS}
            row["<main,t-word-id> id"] = row_id
            row["<main,t-word> title"] = f"Database {row_id}"
            row["<main,t-word> database_name"] = f"DB{row_id}"
            row["<main,t-word-url> database_url"] = f"https://example.org/db{row_id}"
            row["<sub,t-word-doi> doi"] = f"10.1/{row_id}"
            row["<sub,t-word-pmid> pmid"] = str(1000 + row_id)
            row["<sub,t-numeric> year"] = 2025
            row[ORIGINAL_DATABASE_COLUMN] = "no"
            row[POLICY_VERSION_COLUMN] = SCREENING_POLICY_VERSION
            row["<main,t-word-tag> accessibility"] = "live"
            row["<main,t-bool> db_type_confirmation"] = "yes"
            row["<sub,t-word> confirmation_reason"] = "searchable isoform records"
            row[QUALIFICATION_BASIS_COLUMN] = "transcript_or_isoform_model"
            row[EXCLUSION_CODE_COLUMN] = "not_applicable"
            row[EVIDENCE_URL_COLUMN] = f"https://example.org/db{row_id}/records"
            row[EVIDENCE_SOURCE_TYPE_COLUMN] = "official_database"
            row[EVIDENCE_STATEMENT_COLUMN] = "The database provides searchable transcript isoform records."
            row[EVIDENCE_CHECKED_DATE_COLUMN] = "2026-07-10"
            row[MANUAL_REVIEW_COLUMN] = "no"
            row["<main,t-word-tag> neural_link"] = "none"
            row["<main,t-word-tag> focus"] = "transcriptomics_general"
            row["<main,t-bool> gene_expression_available"] = "yes"
            row["<main,t-word-tag> species"] = "Human"
            row["<sub,t-word-tag> disease_association"] = "unknown"
            row["<sub,t-word-tag> developmental_association"] = "unknown"
            row["<main,t-word-tag> tissue_or_brain_region"] = "unknown"
            row["<sub,t-word-tag> cell_type"] = "unknown"
            row["<main,t-word-tag> sequencing_resolution"] = "bulk"
            row["<main,t-word-tag> read_technology"] = "short"
            row["<sub,t-word> visualization_methods"] = "unknown"
            rows.append(row)
        return pd.DataFrame(rows, columns=AGENT_INPUT_COLUMNS)

    def test_article_normalization_and_seed_dedupe(self):
        article = {
            "title": "Example database.",
            "doi": "10.1/example",
            "pubYear": "2025",
            "journalInfo": {"journal": {"title": "Nucleic Acids Research"}},
            "citedByCount": "12",
            "pmid": "123",
            "pmcid": "PMC123",
            "source": "MED",
            "abstractText": "A useful database.",
        }
        record = article_to_record(article)
        self.assertEqual(record["web"], "https://doi.org/10.1/example")
        self.assertEqual(record["journal"], "Nucleic Acids Research")
        self.assertEqual(record["citedByCount"], 12)

        df = dedupe_seed_articles(
            [
                {**record, "title": "Example database.", "citedByCount": 12},
                {**record, "title": " example database. ", "citedByCount": 99},
            ]
        )
        self.assertEqual(len(df), 1)
        self.assertEqual(int(df.iloc[0]["citedByCount"]), 99)

        relevance_df = dedupe_seed_articles(
            [
                {**record, "title": "Example database.", "citedByCount": 12},
                {**record, "title": " example database. ", "citedByCount": 99},
            ],
            dedupe_order="relevance",
        )
        self.assertEqual(len(relevance_df), 1)
        self.assertEqual(int(relevance_df.iloc[0]["citedByCount"]), 12)

    def test_reference_resume_skips_completed_pmids(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state = self.make_state(tmp_path)
            state.mark_key_complete("reference_pmids", "123")
            session = FakeSession()
            seed_df = pd.DataFrame([{"pmid": "123"}])

            result = collect_references_for_seeds(
                seed_df,
                tmp_path / "references.csv",
                state,
                session=session,
            )

            self.assertTrue(result.empty)
            self.assertEqual(session.calls, [])

    def test_reference_collection_can_fetch_with_worker_threads(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state = self.make_state(tmp_path)
            seed_df = pd.DataFrame([{"pmid": "123"}, {"pmid": "456"}])

            def fake_fetch(pmid, **kwargs):
                return [
                    {
                        "source": "MED",
                        "id": f"{pmid}1",
                        "title": f"Reference {pmid}",
                        "doi": f"10.1/{pmid}",
                        "pubYear": "2025",
                    }
                ]

            with patch("paper_search.fetch_complete_references_for_pmid", side_effect=fake_fetch):
                result = collect_references_for_seeds(
                    seed_df,
                    tmp_path / "references.csv",
                    state,
                    rate_limit_per_sec=0,
                    max_workers=2,
                )

            self.assertEqual(len(result), 2)
            self.assertEqual(state.completed_keys("reference_pmids"), {"123", "456"})
            self.assertTrue((tmp_path / "references.csv").exists())

    def test_seed_collection_paginates_when_per_year_limit_exceeds_page_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state = self.make_state(tmp_path)
            pages = {
                "*": {
                    "resultList": {
                        "result": [
                            {"title": "First splicing paper", "doi": "10.1/1", "pubYear": "2026"},
                            {"title": "Second splicing paper", "doi": "10.1/2", "pubYear": "2026"},
                        ]
                    },
                    "nextCursorMark": "cursor-2",
                },
                "cursor-2": {
                    "resultList": {
                        "result": [
                            {"title": "Third splicing paper", "doi": "10.1/3", "pubYear": "2026"},
                            {"title": "Fourth splicing paper", "doi": "10.1/4", "pubYear": "2026"},
                        ]
                    },
                    "nextCursorMark": "cursor-3",
                },
            }
            session = FakePagingSession(pages)

            result = collect_seed_articles(
                "splicing",
                [2026],
                tmp_path / "seed.csv",
                state,
                page_size=2,
                max_rows_per_year=3,
                session=session,
            )

            self.assertEqual(len(result), 3)
            self.assertEqual(len(session.calls), 2)
            self.assertEqual(session.calls[0][1]["params"]["cursorMark"], "*")
            self.assertEqual(session.calls[1][1]["params"]["cursorMark"], "cursor-2")
            self.assertEqual(state.completed_keys("seed_years"), {"2026"})
            self.assertEqual(len(pd.read_csv(tmp_path / "seed.csv")), 3)

    def test_reference_detail_dedupe_priority(self):
        df = dedupe_reference_details(
            [
                {"title": "A", "doi": "10.1/a", "pmid": "1", "pmcid": "", "citedByCount": 1},
                {"title": "A better", "doi": "10.1/a", "pmid": "2", "pmcid": "", "citedByCount": 20},
                {"title": "Same title", "doi": "", "pmid": "", "pmcid": "", "citedByCount": 3},
                {"title": " same title ", "doi": "", "pmid": "", "pmcid": "", "citedByCount": 2},
            ]
        )

        self.assertEqual(len(df), 2)
        self.assertIn("A better", set(df["title"]))
        self.assertIn("Same title", set(df["title"]))

    def test_keyword_screening_matches_database_terms(self):
        df = pd.DataFrame(
            [
                {"title": "A splicing atlas", "abstractText": "A portal for analysis."},
                {"title": "Signal pathway", "abstractText": "No resource is provided."},
                {"title": "DAVID annotation database", "abstractText": "A database for annotation."},
                {"title": "Plain paper", "abstractText": "Mechanism only."},
            ]
        )

        screened = keyword_screen(df)

        self.assertEqual(len(screened), 1)
        terms = set(term for row in screened["matched_terms"] for term in row)
        scope_terms = set(term for row in screened["matched_scope_terms"] for term in row)
        self.assertTrue({"atlas", "portal"}.issubset(terms))
        self.assertIn("splicing", scope_terms)

    def test_keyword_candidate_pool_includes_seed_only_rows(self):
        detail_df = pd.DataFrame(
            [
                {
                    "title": "Reference transcript atlas",
                    "abstractText": "A transcriptomics database for RNA analysis.",
                    "doi": "10.1/reference",
                },
            ]
        )
        seed_df = pd.DataFrame(
            [
                {
                    "title": "PerturbAtlas",
                    "abstractText": "A transcriptome database and atlas with RNA perturbation profiles.",
                    "doi": "10.1093/nar/gkae851",
                },
            ]
        )

        candidate_df = build_keyword_candidate_pool(seed_df, detail_df)
        screened = keyword_screen(candidate_df)

        self.assertEqual(list(candidate_df["title"]), ["Reference transcript atlas", "PerturbAtlas"])
        self.assertEqual(list(candidate_df["candidate_source"]), ["reference_detail", "seed_article"])
        self.assertIn("PerturbAtlas", set(screened["title"]))

    def test_keyword_candidate_pool_dedupes_seed_by_doi_and_preserves_detail_order(self):
        detail_df = pd.DataFrame(
            [
                {"title": "First detail", "abstractText": "A transcript database.", "doi": "10.1/first"},
                {"title": "Shared detail", "abstractText": "A transcript atlas.", "doi": "10.1/shared"},
            ]
        )
        seed_df = pd.DataFrame(
            [
                {"title": "Shared seed", "abstractText": "A transcript atlas.", "doi": "https://doi.org/10.1/shared"},
                {"title": "Seed only", "abstractText": "A transcript resource.", "doi": "10.1/seed"},
            ]
        )

        candidate_df = build_keyword_candidate_pool(seed_df, detail_df)

        self.assertEqual(list(candidate_df["title"]), ["First detail", "Shared detail", "Seed only"])
        self.assertEqual(list(candidate_df["candidate_source"]), ["reference_detail", "reference_detail", "seed_article"])

    def test_keyword_screening_can_disable_rna_scope_filter(self):
        df = pd.DataFrame(
            [
                {"title": "A splicing atlas", "abstractText": "A portal for analysis."},
                {"title": "Signal pathway", "abstractText": "No resource is provided."},
            ]
        )

        screened = keyword_screen(df, scope_pattern=None)

        self.assertEqual(len(screened), 2)

    def test_missing_abstract_rows_are_split_for_codex_agent(self):
        df = pd.DataFrame(
            [
                {"title": "A splicing portal", "abstractText": "A resource is released."},
                {"title": "No abstract database", "abstractText": ""},
                {"title": "NaN abstract atlas", "abstractText": float("nan")},
            ]
        )

        with_abstract, missing_abstract = split_missing_abstract(df)

        self.assertEqual(len(with_abstract), 1)
        self.assertEqual(len(missing_abstract), 2)

    def test_default_ai_question_uses_external_knowledge_and_splicing_resource_scope(self):
        self.assertIn("your biomedical knowledge", DEFAULT_AI_QUESTION)
        self.assertIn("Return exactly one JSON object", DEFAULT_AI_QUESTION)
        self.assertIn("AI_reason", DEFAULT_AI_QUESTION)
        self.assertIn("alternative splicing", DEFAULT_AI_QUESTION)
        self.assertIn("primary focus is not splicing", DEFAULT_AI_QUESTION)

    def test_ai_json_response_parsing(self):
        parsed = parse_ai_json_response(
            '{"AI_check":"Yes","AI_reason":"Introduces a splicing-focused web server."}'
        )

        self.assertEqual(parsed["AI_check"], "Yes")
        self.assertEqual(parsed["AI_reason"], "Introduces a splicing-focused web server.")

    def test_ai_json_response_preserves_full_reason(self):
        reason = " ".join(f"word{i}" for i in range(50))
        parsed = parse_ai_json_response(
            f'{{"AI_check":"No","AI_reason":"{reason}"}}'
        )

        self.assertEqual(parsed["AI_reason"], reason)

    def test_ai_json_response_parsing_from_code_fence(self):
        parsed = parse_ai_json_response(
            '```json\n{"AI_check":"No","AI_reason":"General method without a reusable splicing resource."}\n```'
        )

        self.assertEqual(parsed["AI_check"], "No")
        self.assertEqual(parsed["AI_reason"], "General method without a reusable splicing resource.")

    def test_ai_json_response_fallback_for_legacy_yes_no(self):
        parsed = parse_ai_json_response("Yes")

        self.assertEqual(parsed["AI_check"], "Yes")
        self.assertIn("non-JSON", parsed["AI_reason"])

    def test_stage2_json_response_parsing_and_class_check_consistency(self):
        parsed = parse_stage2_json_response(
            json.dumps(
                {
                    "AI_class": 6,
                    "AI_check": "Yes",
                    "AI_reason": "uses TCGA only",
                    "resource_name": "TCGA",
                    "resource_type": "none",
                    "url_mentioned": "No",
                    "target_scope_hint": "transcript_level_abundance",
                }
            )
        )

        self.assertEqual(parsed["AI_class"], 6)
        self.assertEqual(parsed["AI_check"], "No")
        self.assertEqual(parsed["AI_reason"], "uses TCGA only")
        self.assertEqual(parsed["target_scope_hint"], "none")
        self.assertEqual(parsed["screening_policy_version"], SCREENING_POLICY_VERSION)

    def test_stage2_v2_prompt_rejects_gene_expression_only_and_requires_target_scope(self):
        from paper_screening import DEFAULT_STAGE2_EXPERT_PROMPT

        self.assertIn("gene-level expression alone", DEFAULT_STAGE2_EXPERT_PROMPT)
        self.assertIn("back-splice junctions", DEFAULT_STAGE2_EXPERT_PROMPT)
        self.assertIn("target_scope_hint", DEFAULT_STAGE2_EXPERT_PROMPT)
        self.assertIn("class 7, not class 4", DEFAULT_STAGE2_EXPERT_PROMPT)

    def test_stage2_json_response_rejects_invalid_json(self):
        with self.assertRaises(ValueError):
            parse_stage2_json_response("not json")

    def test_stage2_uses_responses_api_with_cache_and_reasoning_disabled(self):
        client = FakeOpenAIClient()

        result, usage = ask_stage2_with_usage(client, "Title", "Abstract", model="gpt-5.5")

        self.assertEqual(result["AI_check"], "Yes")
        self.assertEqual(usage["stage2_cached_tokens"], 2816)
        self.assertEqual(len(client.responses.calls), 1)
        call = client.responses.calls[0]
        self.assertEqual(call["service_tier"], DEFAULT_STAGE2_SERVICE_TIER)
        self.assertEqual(call["reasoning"], {"effort": DEFAULT_STAGE2_REASONING_EFFORT})
        self.assertEqual(call["prompt_cache_key"], DEFAULT_STAGE2_PROMPT_CACHE_KEY)
        self.assertEqual(call["prompt_cache_retention"], DEFAULT_STAGE2_PROMPT_CACHE_RETENTION)
        self.assertEqual(call["max_output_tokens"], DEFAULT_STAGE2_MAX_OUTPUT_TOKENS)

    def test_ai_screening_resume_skips_completed_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state = self.make_state(tmp_path)
            state.mark_key_complete("ai_rows", "doi:10.1/a")
            checkpoint = tmp_path / "ai.csv"
            append_csv_row(
                checkpoint,
                {
                    "title": "Already done",
                    "abstractText": "A database.",
                    "doi": "10.1/a",
                    "paper_key": "doi:10.1/a",
                    "AI_check": "Yes",
                },
                ["title", "abstractText", "doi", "paper_key", "AI_check"],
            )
            client = FakeOpenAIClient()
            matched_df = pd.DataFrame(
                [
                    {"title": "Already done", "abstractText": "A database.", "doi": "10.1/a"},
                    {"title": "Needs check", "abstractText": "A splicing portal.", "doi": "10.1/b"},
                ]
            )

            result = ai_screen_dataframe(matched_df, checkpoint, state, client=client)

            self.assertEqual(len(client.chat.completions.calls), 1)
            self.assertEqual(len(result), 2)
            self.assertEqual(len(yes_only(result)), 2)

    def test_gpt55_omits_temperature(self):
        client = FakeOpenAIClient()

        answer = ask_yes_no(client, "Title", "Abstract", model="gpt-5.5")

        self.assertEqual(answer, "Yes")
        self.assertEqual(len(client.chat.completions.calls), 1)
        self.assertNotIn("temperature", client.chat.completions.calls[0])

    def test_stage1_defaults_to_flex_service_tier(self):
        client = FakeOpenAIClient()

        ask_stage1_with_usage(client, "Title", "Abstract", model="gpt-5.4-mini")

        self.assertEqual(len(client.chat.completions.calls), 1)
        self.assertEqual(
            client.chat.completions.calls[0]["service_tier"],
            DEFAULT_STAGE1_SERVICE_TIER,
        )

    def test_ai_screening_can_fetch_with_worker_threads(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state = self.make_state(tmp_path)
            client = FakeOpenAIClient()
            matched_df = pd.DataFrame(
                [
                    {"title": "First", "abstractText": "A splicing portal.", "doi": "10.1/a"},
                    {"title": "Second", "abstractText": "A splicing atlas.", "doi": "10.1/b"},
                ]
            )

            result = ai_screen_dataframe(
                matched_df,
                tmp_path / "ai.csv",
                state,
                client=client,
                max_workers=2,
                rate_limit_per_sec=0,
            )

            self.assertEqual(len(result), 2)
            self.assertEqual(len(client.chat.completions.calls), 2)
            self.assertEqual(state.completed_keys("ai_rows"), {"doi:10.1/a", "doi:10.1/b"})
            self.assertTrue((tmp_path / "ai.csv").exists())

    def test_ai_screening_max_new_rows_limits_new_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state = self.make_state(tmp_path)
            first_client = FakeOpenAIClient()
            matched_df = pd.DataFrame(
                [
                    {"title": "First", "abstractText": "A splicing portal.", "doi": "10.1/a"},
                    {"title": "Second", "abstractText": "A splicing atlas.", "doi": "10.1/b"},
                    {"title": "Third", "abstractText": "A splicing database.", "doi": "10.1/c"},
                ]
            )
            checkpoint = tmp_path / "ai.csv"

            first_result = ai_screen_dataframe(
                matched_df,
                checkpoint,
                state,
                client=first_client,
                max_new_rows=1,
            )

            self.assertEqual(len(first_client.chat.completions.calls), 1)
            self.assertEqual(len(first_result), 1)
            self.assertEqual(state.completed_keys("ai_rows"), {"doi:10.1/a"})

            second_client = FakeOpenAIClient()
            second_result = ai_screen_dataframe(
                matched_df,
                checkpoint,
                state,
                client=second_client,
                max_new_rows=1,
            )

            self.assertEqual(len(second_client.chat.completions.calls), 1)
            self.assertEqual(len(second_result), 2)
            self.assertEqual(state.completed_keys("ai_rows"), {"doi:10.1/a", "doi:10.1/b"})

    def test_stage2_screening_max_new_rows_limits_new_requests_and_resumes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state = self.make_state(tmp_path)
            checkpoint = tmp_path / "stage2.csv"
            matched_df = pd.DataFrame(
                [
                    {"title": "First", "abstractText": "A splicing portal.", "doi": "10.1/a"},
                    {"title": "Second", "abstractText": "A splicing atlas.", "doi": "10.1/b"},
                    {"title": "Third", "abstractText": "A splicing database.", "doi": "10.1/c"},
                ]
            )
            first_client = FakeOpenAIClient()

            first_result = stage2_screen_dataframe(
                matched_df,
                checkpoint,
                state,
                client=first_client,
                max_new_rows=1,
            )

            self.assertEqual(len(first_client.responses.calls), 1)
            self.assertEqual(len(first_result), 1)
            self.assertEqual(state.completed_keys("stage2_ai_rows"), {"doi:10.1/a"})

            second_client = FakeOpenAIClient()
            second_result = stage2_screen_dataframe(
                matched_df,
                checkpoint,
                state,
                client=second_client,
                max_new_rows=1,
            )

            self.assertEqual(len(second_client.responses.calls), 1)
            self.assertEqual(len(second_result), 2)
            self.assertEqual(
                state.completed_keys("stage2_ai_rows"),
                {"doi:10.1/a", "doi:10.1/b"},
            )

    def test_stage2_rejects_legacy_checkpoint_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state = self.make_state(tmp_path)
            checkpoint = tmp_path / "stage2.csv"
            append_csv_row(
                checkpoint,
                {"paper_key": "doi:10.1/a", "AI_class": 1, "AI_check": "Yes"},
                ["paper_key", "AI_class", "AI_check"],
            )

            with self.assertRaisesRegex(ValueError, "screening policy mismatch"):
                stage2_screen_dataframe(
                    pd.DataFrame(
                        [{"title": "First", "abstractText": "A splicing portal.", "doi": "10.1/a"}]
                    ),
                    checkpoint,
                    state,
                    client=FakeOpenAIClient(),
                )

    def test_stage2_benchmark_priority_orders_matches_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            benchmark_path = Path(tmp) / "benchmark.xlsx"
            pd.DataFrame(
                [
                    {"title": "Benchmark paper", "doi": "10.1/bench", "pmid": "123"},
                ]
            ).to_excel(benchmark_path, index=False)
            matched_df = pd.DataFrame(
                [
                    {"title": "Ordinary paper", "doi": "10.1/ordinary", "pmid": "1"},
                    {"title": "Benchmark paper", "doi": "https://doi.org/10.1/bench", "pmid": "123.0"},
                    {"title": "Another paper", "doi": "10.1/another", "pmid": "2"},
                ]
            )

            prioritized = prioritize_stage2_benchmark_rows(matched_df, benchmark_path)

            self.assertEqual(prioritized.iloc[0]["title"], "Benchmark paper")
            self.assertTrue(bool(prioritized.iloc[0]["stage2_benchmark_priority"]))
            self.assertEqual(list(prioritized["stage2_original_order"]), [1, 0, 2])

    def test_stage7_url_extraction_filters_publication_urls(self):
        url = extract_database_url(
            "DOI page https://doi.org/10.1/example and PubMed https://pubmed.ncbi.nlm.nih.gov/123.",
            "Database available at http://example.org/db.",
            "Europe PMC https://europepmc.org/article/MED/123",
        )

        self.assertEqual(url, "http://example.org/db")

    def test_stage7_maps_agent_input_columns_and_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state = self.make_state(tmp_path)
            stage2_df = pd.DataFrame(
                [
                    {
                        "title": "Example transcript database",
                        "resource_name": "ExampleDB",
                        "abstractText": "Available at https://doi.org/10.1/a and https://example.org/db.",
                        "AI_reason": "AS database with URL",
                        "doi": "https://doi.org/10.1/a",
                        "pmid": 123.0,
                        "pubYear": 2025.0,
                    },
                    {
                        "title": "Unnamed transcript resource",
                        "resource_name": "none",
                        "abstractText": "No URL here.",
                        "doi": "",
                        "pmid": "",
                        "pubYear": "",
                    },
                ]
            )

            result = prepare_agent_input_dataframe(
                stage2_df,
                tmp_path / "07_agent_input_rows.csv",
                state,
            )

            self.assertEqual(list(result.columns), AGENT_INPUT_COLUMNS)
            self.assertEqual(len(result.columns), 30)
            self.assertEqual(
                AGENT_INPUT_COLUMNS.index("<sub,t-word> confirmation_reason"),
                AGENT_INPUT_COLUMNS.index("<main,t-bool> db_type_confirmation") + 1,
            )
            self.assertEqual(list(result["<main,t-word-id> id"]), [1, 2])
            self.assertEqual(result.iloc[0]["<main,t-word> database_name"], "ExampleDB")
            self.assertEqual(result.iloc[1]["<main,t-word> database_name"], "")
            self.assertEqual(result.iloc[0]["<main,t-word-url> database_url"], "https://example.org/db")
            self.assertEqual(result.iloc[0]["<sub,t-word-doi> doi"], "10.1/a")
            self.assertEqual(result.iloc[0]["<sub,t-word-pmid> pmid"], "123")
            self.assertEqual(result.iloc[0]["<sub,t-numeric> year"], 2025)
            self.assertEqual(result.iloc[0][ORIGINAL_DATABASE_COLUMN], "no")
            self.assertEqual(result.iloc[0][POLICY_VERSION_COLUMN], SCREENING_POLICY_VERSION)
            self.assertEqual(result.iloc[0]["<main,t-word-tag> accessibility"], "")
            self.assertEqual(result.iloc[0]["<sub,t-word> confirmation_reason"], "")
            self.assertEqual(result.iloc[0][EVIDENCE_URL_COLUMN], "")

    def test_stage7_curated_input_preserves_identity_and_clears_old_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state = self.make_state(tmp_path)
            curated_df = pd.DataFrame(
                [
                    {
                        "<main,t-word-id> id": 3325,
                        "<main,t-word> title": "Allen Cell Types Database",
                        "<main,t-word> database_name": "Allen Cell Types Database",
                        "<main,t-word-url> database_url": "https://celltypes.brain-map.org/",
                        "<sub,t-word-doi> doi": "10.1/example",
                        "<sub,t-word-pmid> pmid": "12345",
                        "<sub,t-numeric> year": 2019,
                        "<main,t-bool> db_type_confirmation": "yes",
                        "<sub,t-word> confirmation_reason": "old broad-atlas exception",
                        ORIGINAL_DATABASE_COLUMN: "yes",
                    }
                ]
            )

            result = prepare_agent_input_dataframe(
                curated_df,
                tmp_path / "07_agent_input_rows.csv",
                state,
                input_format="curated",
            )

            self.assertEqual(list(result["<main,t-word-id> id"]), [3325])
            self.assertEqual(result.iloc[0]["<main,t-word> database_name"], "Allen Cell Types Database")
            self.assertEqual(result.iloc[0][ORIGINAL_DATABASE_COLUMN], "yes")
            self.assertEqual(result.iloc[0][POLICY_VERSION_COLUMN], SCREENING_POLICY_VERSION)
            self.assertEqual(result.iloc[0]["<main,t-bool> db_type_confirmation"], "")
            self.assertEqual(result.iloc[0]["<sub,t-word> confirmation_reason"], "")
            self.assertEqual(result.iloc[0][EVIDENCE_URL_COLUMN], "")

    def test_stage7_curated_pipeline_writes_v2_chunks_only_in_temp_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            curated_path = base_dir / "curated.xlsx"
            pd.DataFrame(
                [
                    {
                        "<main,t-word-id> id": 42,
                        "<main,t-word> title": "Example curated database",
                        "<main,t-word> database_name": "ExampleDB",
                        "<main,t-word-url> database_url": "https://example.org/db",
                        "<sub,t-word-doi> doi": "10.1/example",
                        "<sub,t-word-pmid> pmid": "12345",
                        "<sub,t-numeric> year": 2025,
                        ORIGINAL_DATABASE_COLUMN: "yes",
                        "<main,t-bool> db_type_confirmation": "yes",
                    }
                ]
            ).to_excel(curated_path, index=False)
            args = argparse.Namespace(
                run_id="curated_stage7",
                resume=False,
                query=None,
                years=None,
                no_ai=False,
                stop_before_ai=False,
                stage2_only=False,
                stage7_only=True,
                stage8_only=False,
                stage7_input=str(curated_path),
                stage7_input_format="curated",
                stage7_forced_include=None,
                stage7_chunk_size=25,
                limit=None,
                model=None,
            )
            config = build_config(args)

            with patch("searchscreening_pipeline.DATA_COLLECTION_DIR", base_dir):
                run_pipeline(config)

            output_dir = base_dir / "runs" / "curated_stage7" / "outputs"
            all_rows = pd.read_excel(output_dir / "07_agent_input_all.xlsx")
            chunk_rows = pd.read_excel(
                output_dir / "07_agent_input_chunks" / "07_agent_input_part_001.xlsx"
            )
            self.assertEqual(list(all_rows.columns), AGENT_INPUT_COLUMNS)
            self.assertEqual(list(chunk_rows["<main,t-word-id> id"]), [42])
            self.assertEqual(chunk_rows.iloc[0][POLICY_VERSION_COLUMN], SCREENING_POLICY_VERSION)
            self.assertTrue(pd.isna(chunk_rows.iloc[0]["<main,t-bool> db_type_confirmation"]))
            pipeline_logger = logging.getLogger("searchscreening_pipeline")
            for handler in pipeline_logger.handlers:
                handler.close()
            pipeline_logger.handlers.clear()

    def test_stage7_forced_include_appends_and_dedupes(self):
        stage2_df = pd.DataFrame(
            [
                {
                    "title": "Existing PerturbAtlas paper",
                    "resource_name": "PerturbAtlas",
                    "doi": "10.1093/nar/gkae851",
                    "pubYear": 2025,
                },
            ]
        )
        forced_df = pd.DataFrame(
            [
                {
                    "title": "PerturbAtlas",
                    "resource_name": "PerturbAtlas",
                    "doi": "https://doi.org/10.1093/nar/gkae851",
                    "force_reason": "legacy forced include",
                },
                {
                    "title": "SplicingAD",
                    "resource_name": "SplicingAD",
                    "doi": "",
                    "force_reason": "legacy forced include",
                },
            ]
        )

        combined = append_stage7_forced_includes(stage2_df, forced_df)

        self.assertEqual(len(combined), 2)
        self.assertEqual(list(combined["resource_name"]), ["PerturbAtlas", "SplicingAD"])
        self.assertEqual(combined.iloc[1]["candidate_source"], "forced_include")
        self.assertEqual(combined.iloc[1]["AI_check"], "Yes")

    def test_stage7_forced_include_rows_can_be_prepared_without_doi(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state = self.make_state(tmp_path)
            combined = append_stage7_forced_includes(
                pd.DataFrame(columns=["title", "resource_name", "doi", "pubYear"]),
                pd.DataFrame(
                    [
                        {
                            "title": "SplicingAD",
                            "resource_name": "SplicingAD",
                            "doi": "",
                            "pubYear": "",
                            "force_reason": "legacy forced include",
                        }
                    ]
                ),
            )

            result = prepare_agent_input_dataframe(
                combined,
                tmp_path / "07_agent_input_rows.csv",
                state,
            )

            self.assertEqual(len(result), 1)
            self.assertEqual(result.iloc[0]["<main,t-word> database_name"], "SplicingAD")
            self.assertEqual(result.iloc[0]["<sub,t-word-doi> doi"], "")

    def test_stage7_chunk_writer_splits_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            df = pd.DataFrame(
                [
                    {column: "" for column in AGENT_INPUT_COLUMNS}
                    for _ in range(205)
                ]
            )
            df["<main,t-word-id> id"] = list(range(1, 206))

            chunk_paths = write_agent_input_chunks(df, tmp_path / "chunks", 100)

            self.assertEqual(len(chunk_paths), 3)
            self.assertEqual([len(pd.read_excel(path)) for path in chunk_paths], [100, 100, 5])

    def test_stage7_resume_uses_checkpoint_without_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state = self.make_state(tmp_path)
            checkpoint = tmp_path / "07_agent_input_rows.csv"
            first_record = {column: "" for column in AGENT_INPUT_COLUMNS}
            first_record["<main,t-word-id> id"] = 1
            first_record["<main,t-word> title"] = "Already prepared"
            first_record[ORIGINAL_DATABASE_COLUMN] = "no"
            first_record[POLICY_VERSION_COLUMN] = SCREENING_POLICY_VERSION
            append_csv_row(checkpoint, first_record, AGENT_INPUT_COLUMNS)
            stage2_df = pd.DataFrame(
                [
                    {"title": "Already prepared", "resource_name": "DoneDB", "pubYear": 2024},
                    {"title": "Needs preparation", "resource_name": "NextDB", "pubYear": 2025},
                ]
            )

            result = prepare_agent_input_dataframe(stage2_df, checkpoint, state)

            checkpoint_df = pd.read_csv(checkpoint)
            self.assertEqual(len(result), 2)
            self.assertEqual(len(checkpoint_df), 2)
            self.assertEqual(state.completed_keys("agent_prep_rows"), {"1", "2"})

    def test_stage7_rejects_legacy_checkpoint_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state = self.make_state(tmp_path)
            checkpoint = tmp_path / "07_agent_input_rows.csv"
            legacy = {column: "" for column in AGENT_INPUT_COLUMNS}
            legacy["<main,t-word-id> id"] = 1
            append_csv_row(checkpoint, legacy, AGENT_INPUT_COLUMNS)

            with self.assertRaisesRegex(ValueError, "screening policy mismatch"):
                prepare_agent_input_dataframe(
                    pd.DataFrame([{"title": "First", "resource_name": "FirstDB"}]),
                    checkpoint,
                    state,
                )

    def test_partial_stage7_pipeline_does_not_write_final_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            run_id = "partial_stage7"
            outputs_dir = base_dir / "runs" / run_id / "outputs"
            outputs_dir.mkdir(parents=True)
            pd.DataFrame(
                [
                    {"title": "First", "resource_name": "FirstDB", "pubYear": 2024},
                    {"title": "Second", "resource_name": "SecondDB", "pubYear": 2025},
                ]
            ).to_excel(outputs_dir / "06_stage2_ai_yes_only.xlsx", index=False)
            args = argparse.Namespace(
                run_id=run_id,
                resume=True,
                query=None,
                years=None,
                no_ai=False,
                stop_before_ai=False,
                stage2_only=False,
                stage7_only=True,
                stage7_input=None,
                stage7_chunk_size=100,
                limit=None,
                seed_limit_per_year=None,
                seed_dedupe_order=None,
                reference_workers=None,
                detail_workers=None,
                detail_batch_size=None,
                ai_workers=None,
                ai_rate_limit_per_sec=None,
                ai_max_new_rows=None,
                stage1_service_tier=None,
                stage2_workers=None,
                stage2_rate_limit_per_sec=None,
                stage2_max_new_rows=None,
                stage2_service_tier=None,
                stage2_reasoning_effort=None,
                stage2_prompt_cache_key=None,
                stage2_prompt_cache_retention=None,
                stage2_max_output_tokens=None,
                stage2_benchmark_path=None,
                model=None,
            )
            config = build_config(args)
            config["stage7_max_new_rows"] = 1

            try:
                with patch("searchscreening_pipeline.DATA_COLLECTION_DIR", base_dir):
                    run_pipeline(config)

                state_data = json.loads((base_dir / "runs" / run_id / "state.json").read_text(encoding="utf-8"))
                checkpoint = base_dir / "runs" / run_id / "checkpoints" / "07_agent_input_rows.csv"

                self.assertNotIn("agent_table_prep", state_data["completed_stages"])
                self.assertEqual(state_data["counts"]["agent_table_prep_rows"], 1)
                self.assertTrue(checkpoint.exists())
                self.assertEqual(len(pd.read_csv(checkpoint)), 1)
                self.assertFalse((outputs_dir / "07_agent_input_all.xlsx").exists())
                self.assertFalse((outputs_dir / "07_agent_input_chunks").exists())
            finally:
                logger = logging.getLogger("searchscreening_pipeline")
                for handler in logger.handlers[:]:
                    handler.close()
                    logger.removeHandler(handler)

    def test_stage8_scan_discovers_pending_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "07_chunks"
            result_dir = tmp_path / "08_chunk"
            input_dir.mkdir()
            write_excel_path = input_dir / "07_agent_input_part_001.xlsx"
            self.make_agent_rows(1, 2).to_excel(write_excel_path, index=False)
            state = self.make_state(tmp_path)

            manifest = scan_stage8_chunks(input_dir, result_dir, tmp_path / "08_agent_chunks.csv", state)

            self.assertEqual(len(manifest), 1)
            self.assertEqual(manifest.iloc[0]["chunk_id"], "001")
            self.assertEqual(manifest.iloc[0]["status"], "pending")
            self.assertTrue(str(manifest.iloc[0]["result_path"]).endswith("08_result_part_001.xlsx"))

    def test_stage8_scan_marks_existing_valid_result_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "07_chunks"
            result_dir = tmp_path / "08_chunk"
            input_dir.mkdir()
            result_dir.mkdir()
            df = self.make_agent_rows(1, 2)
            df.to_excel(input_dir / "07_agent_input_part_001.xlsx", index=False)
            df.to_excel(result_dir / "08_result_part_001.xlsx", index=False)
            state = self.make_state(tmp_path)

            manifest = scan_stage8_chunks(input_dir, result_dir, tmp_path / "08_agent_chunks.csv", state)

            self.assertEqual(manifest.iloc[0]["status"], "complete")
            self.assertEqual(state.completed_keys("agent_curation_chunks"), {"001"})

    def test_stage8_validation_rejects_bad_schema_and_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "07_agent_input_part_001.xlsx"
            result_path = tmp_path / "08_result_part_001.xlsx"
            self.make_agent_rows(1, 2).to_excel(input_path, index=False)
            bad_schema = self.make_agent_rows(1, 2).drop(columns=["<sub,t-word> visualization_methods"])
            bad_schema.to_excel(result_path, index=False)

            schema_result = validate_stage8_result_chunk(input_path, result_path)
            self.assertEqual(schema_result["status"], "invalid")
            self.assertIn("schema", schema_result["error"])

            bad_values = self.make_agent_rows(1, 2)
            bad_values.loc[0, "<main,t-word-tag> accessibility"] = "maybe"
            bad_values.to_excel(result_path, index=False)

            value_result = validate_stage8_result_chunk(input_path, result_path)
            self.assertEqual(value_result["status"], "invalid")
            self.assertIn("invalid values", value_result["error"])

    def test_stage8_validation_requires_confirmation_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "07_agent_input_part_001.xlsx"
            result_path = tmp_path / "08_result_part_001.xlsx"
            self.make_agent_rows(1, 2).to_excel(input_path, index=False)
            bad_values = self.make_agent_rows(1, 2)
            bad_values.loc[0, "<sub,t-word> confirmation_reason"] = ""
            bad_values.to_excel(result_path, index=False)

            result = validate_stage8_result_chunk(input_path, result_path)

            self.assertEqual(result["status"], "invalid")
            self.assertIn("required agent-filled columns are blank", result["error"])

            bad_values = self.make_agent_rows(1, 2)
            bad_values.loc[0, "<sub,t-word> confirmation_reason"] = "x" * 81
            bad_values.to_excel(result_path, index=False)

            result = validate_stage8_result_chunk(input_path, result_path)

            self.assertEqual(result["status"], "invalid")
            self.assertIn("confirmation_reason", result["error"])

    def test_stage8_validation_requires_combined_evidence_for_yes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "07_agent_input_part_001.xlsx"
            result_path = tmp_path / "08_result_part_001.xlsx"
            self.make_agent_rows(1, 2).to_excel(input_path, index=False)
            bad_values = self.make_agent_rows(1, 2)
            bad_values.loc[0, EVIDENCE_URL_COLUMN] = ""
            bad_values.to_excel(result_path, index=False)

            result = validate_stage8_result_chunk(input_path, result_path)

            self.assertEqual(result["status"], "invalid")
            self.assertIn("required agent-filled columns are blank", result["error"])

            bad_values = self.make_agent_rows(1, 2)
            bad_values.loc[0, EVIDENCE_CHECKED_DATE_COLUMN] = "2026-7-1"
            bad_values.to_excel(result_path, index=False)
            result = validate_stage8_result_chunk(input_path, result_path)
            self.assertEqual(result["status"], "invalid")
            self.assertIn("invalid evidence date", result["error"])

    def test_stage8_validation_rejects_inconsistent_yes_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "07_agent_input_part_001.xlsx"
            result_path = tmp_path / "08_result_part_001.xlsx"
            self.make_agent_rows(1, 2).to_excel(input_path, index=False)
            bad_values = self.make_agent_rows(1, 2)
            bad_values.loc[0, QUALIFICATION_BASIS_COLUMN] = "not_applicable"
            bad_values.loc[0, EXCLUSION_CODE_COLUMN] = "gene_expression_only"
            bad_values.to_excel(result_path, index=False)

            result = validate_stage8_result_chunk(input_path, result_path)

            self.assertEqual(result["status"], "invalid")
            self.assertIn("inconsistent yes decision", result["error"])

    def test_stage8_manual_review_is_no_and_capped_at_five_percent(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "07_agent_input_part_001.xlsx"
            result_path = tmp_path / "08_result_part_001.xlsx"
            input_df = self.make_agent_rows(1, 25)
            input_df.to_excel(input_path, index=False)

            one_review = self.make_agent_rows(1, 25)
            one_review.loc[0, "<main,t-bool> db_type_confirmation"] = "no"
            one_review.loc[0, "<sub,t-word> confirmation_reason"] = "target scope could not be verified"
            one_review.loc[0, QUALIFICATION_BASIS_COLUMN] = "not_applicable"
            one_review.loc[0, EXCLUSION_CODE_COLUMN] = "insufficient_evidence"
            one_review.loc[0, MANUAL_REVIEW_COLUMN] = "yes"
            one_review.loc[0, "<main,t-word-tag> focus"] = "unknown"
            one_review.loc[0, "<main,t-bool> gene_expression_available"] = "no"
            one_review.to_excel(result_path, index=False)

            valid = validate_stage8_result_chunk(input_path, result_path)
            self.assertEqual(valid["status"], "complete")

            two_reviews = one_review.copy()
            two_reviews.loc[1, "<main,t-bool> db_type_confirmation"] = "no"
            two_reviews.loc[1, "<sub,t-word> confirmation_reason"] = "target scope could not be verified"
            two_reviews.loc[1, QUALIFICATION_BASIS_COLUMN] = "not_applicable"
            two_reviews.loc[1, EXCLUSION_CODE_COLUMN] = "insufficient_evidence"
            two_reviews.loc[1, MANUAL_REVIEW_COLUMN] = "yes"
            two_reviews.loc[1, "<main,t-word-tag> focus"] = "unknown"
            two_reviews.loc[1, "<main,t-bool> gene_expression_available"] = "no"
            two_reviews.to_excel(result_path, index=False)

            invalid = validate_stage8_result_chunk(input_path, result_path)
            self.assertEqual(invalid["status"], "invalid")
            self.assertIn("manual review limit exceeded", invalid["error"])

    def test_stage8_validation_rejects_boolean_disease_or_development_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "07_agent_input_part_001.xlsx"
            result_path = tmp_path / "08_result_part_001.xlsx"
            self.make_agent_rows(1, 2).to_excel(input_path, index=False)
            bad_values = self.make_agent_rows(1, 2)
            bad_values.loc[0, "<sub,t-word-tag> disease_association"] = "yes"
            bad_values.loc[1, "<sub,t-word-tag> developmental_association"] = "no"
            bad_values.to_excel(result_path, index=False)

            result = validate_stage8_result_chunk(input_path, result_path)

            self.assertEqual(result["status"], "invalid")
            self.assertIn("forbidden boolean-like values", result["error"])

    def test_stage8_validation_rejects_blank_or_none_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "07_agent_input_part_001.xlsx"
            result_path = tmp_path / "08_result_part_001.xlsx"
            self.make_agent_rows(1, 2).to_excel(input_path, index=False)
            bad_values = self.make_agent_rows(1, 2)
            bad_values.loc[0, "<main,t-word-tag> focus"] = ""
            bad_values.to_excel(result_path, index=False)

            result = validate_stage8_result_chunk(input_path, result_path)

            self.assertEqual(result["status"], "invalid")
            self.assertIn("required agent-filled columns are blank", result["error"])

            bad_values = self.make_agent_rows(1, 2)
            bad_values.loc[0, "<sub,t-word> visualization_methods"] = "none"
            bad_values.to_excel(result_path, index=False)

            result = validate_stage8_result_chunk(input_path, result_path)

            self.assertEqual(result["status"], "invalid")
            self.assertIn("missing values must use unknown", result["error"])

    def test_stage8_validation_allows_neural_none_and_unknown_focus_for_rejects(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "07_agent_input_part_001.xlsx"
            result_path = tmp_path / "08_result_part_001.xlsx"
            self.make_agent_rows(1, 2).to_excel(input_path, index=False)
            result_df = self.make_agent_rows(1, 2)
            result_df.loc[0, "<main,t-bool> db_type_confirmation"] = "no"
            result_df.loc[0, "<sub,t-word> confirmation_reason"] = "gene expression only"
            result_df.loc[0, QUALIFICATION_BASIS_COLUMN] = "not_applicable"
            result_df.loc[0, EXCLUSION_CODE_COLUMN] = "gene_expression_only"
            result_df.loc[0, MANUAL_REVIEW_COLUMN] = "no"
            result_df.loc[0, "<main,t-word-tag> neural_link"] = "none"
            result_df.loc[0, "<main,t-word-tag> focus"] = "unknown"
            result_df.loc[0, "<main,t-bool> gene_expression_available"] = "no"
            result_df.to_excel(result_path, index=False)

            result = validate_stage8_result_chunk(input_path, result_path)

            self.assertEqual(result["status"], "complete")

    def test_stage8_validation_rejects_unknown_resolution_or_read_technology(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "07_agent_input_part_001.xlsx"
            result_path = tmp_path / "08_result_part_001.xlsx"
            self.make_agent_rows(1, 2).to_excel(input_path, index=False)
            bad_values = self.make_agent_rows(1, 2)
            bad_values.loc[0, "<main,t-word-tag> sequencing_resolution"] = "unknown"
            bad_values.to_excel(result_path, index=False)

            result = validate_stage8_result_chunk(input_path, result_path)

            self.assertEqual(result["status"], "invalid")
            self.assertIn("invalid values", result["error"])

            bad_values = self.make_agent_rows(1, 2)
            bad_values.loc[0, "<main,t-word-tag> read_technology"] = "unknown"
            bad_values.to_excel(result_path, index=False)

            result = validate_stage8_result_chunk(input_path, result_path)

            self.assertEqual(result["status"], "invalid")
            self.assertIn("invalid values", result["error"])

            bad_values = self.make_agent_rows(1, 2)
            bad_values.loc[0, "<main,t-word-tag> read_technology"] = "short;long"
            bad_values.to_excel(result_path, index=False)

            result = validate_stage8_result_chunk(input_path, result_path)

            self.assertEqual(result["status"], "invalid")
            self.assertIn("invalid values", result["error"])

            good_values = self.make_agent_rows(1, 2)
            good_values.loc[0, "<main,t-word-tag> read_technology"] = "long;short"
            good_values.to_excel(result_path, index=False)

            result = validate_stage8_result_chunk(input_path, result_path)

            self.assertEqual(result["status"], "complete")

    def test_stage8_validation_rejects_unclear_and_vague_placeholders(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "07_agent_input_part_001.xlsx"
            result_path = tmp_path / "08_result_part_001.xlsx"
            self.make_agent_rows(1, 2).to_excel(input_path, index=False)
            bad_values = self.make_agent_rows(1, 2)
            bad_values.loc[0, "<main,t-word-tag> species"] = "multiple species"
            bad_values.to_excel(result_path, index=False)

            result = validate_stage8_result_chunk(input_path, result_path)

            self.assertEqual(result["status"], "invalid")
            self.assertIn("forbidden vague or legacy placeholder values", result["error"])

            bad_values = self.make_agent_rows(1, 2)
            bad_values.loc[1, "<sub,t-word-tag> disease_association"] = "Unclear"
            bad_values.to_excel(result_path, index=False)

            result = validate_stage8_result_chunk(input_path, result_path)

            self.assertEqual(result["status"], "invalid")
            self.assertIn("forbidden boolean-like values", result["error"])

    def test_stage8_validation_rejects_id_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "07_agent_input_part_001.xlsx"
            result_path = tmp_path / "08_result_part_001.xlsx"
            self.make_agent_rows(1, 2).to_excel(input_path, index=False)
            result_df = self.make_agent_rows(1, 2)
            result_df.loc[1, "<main,t-word-id> id"] = 99
            result_df.to_excel(result_path, index=False)

            result = validate_stage8_result_chunk(input_path, result_path)

            self.assertEqual(result["status"], "invalid")
            self.assertIn("id order mismatch", result["error"])

    def test_stage8_merge_combines_completed_chunks_by_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result_dir = tmp_path / "08_chunk"
            result_dir.mkdir()
            first = self.make_agent_rows(101, 2)
            second = self.make_agent_rows(1, 2)
            first_path = result_dir / "08_result_part_001.xlsx"
            second_path = result_dir / "08_result_part_002.xlsx"
            first.to_excel(first_path, index=False)
            second.to_excel(second_path, index=False)
            manifest = pd.DataFrame(
                [
                    {"chunk_id": "001", "result_path": str(first_path), "status": "complete"},
                    {"chunk_id": "002", "result_path": str(second_path), "status": "complete"},
                ]
            )

            merged = merge_stage8_results(manifest, tmp_path / "08_agent_merged.xlsx")

            self.assertEqual(list(merged["<main,t-word-id> id"]), [1, 2, 101, 102])
            self.assertTrue((tmp_path / "08_agent_merged.xlsx").exists())

    def test_stage8_pipeline_missing_results_does_not_write_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            run_id = "partial_stage8"
            input_dir = base_dir / "runs" / run_id / "outputs" / "07_agent_input_chunks"
            input_dir.mkdir(parents=True)
            self.make_agent_rows(1, 2).to_excel(input_dir / "07_agent_input_part_001.xlsx", index=False)
            args = argparse.Namespace(
                run_id=run_id,
                resume=True,
                query=None,
                years=None,
                no_ai=False,
                stop_before_ai=False,
                stage2_only=False,
                stage7_only=False,
                stage7_input=None,
                stage7_chunk_size=100,
                stage8_only=True,
                stage8_agent_count=5,
                stage8_input_dir=None,
                stage8_merge_only=False,
                limit=None,
                seed_limit_per_year=None,
                seed_dedupe_order=None,
                reference_workers=None,
                detail_workers=None,
                detail_batch_size=None,
                ai_workers=None,
                ai_rate_limit_per_sec=None,
                ai_max_new_rows=None,
                stage1_service_tier=None,
                stage2_workers=None,
                stage2_rate_limit_per_sec=None,
                stage2_max_new_rows=None,
                stage2_service_tier=None,
                stage2_reasoning_effort=None,
                stage2_prompt_cache_key=None,
                stage2_prompt_cache_retention=None,
                stage2_max_output_tokens=None,
                stage2_benchmark_path=None,
                model=None,
            )
            config = build_config(args)

            try:
                with patch("searchscreening_pipeline.DATA_COLLECTION_DIR", base_dir):
                    run_pipeline(config)

                outputs_dir = base_dir / "runs" / run_id / "outputs"
                state_data = json.loads((base_dir / "runs" / run_id / "state.json").read_text(encoding="utf-8"))
                checkpoint = base_dir / "runs" / run_id / "checkpoints" / "08_agent_chunks.csv"

                self.assertNotIn("agent_web_curation", state_data["completed_stages"])
                self.assertTrue(checkpoint.exists())
                self.assertEqual(pd.read_csv(checkpoint).iloc[0]["status"], "pending")
                self.assertFalse((outputs_dir / "08_agent_merged.xlsx").exists())
            finally:
                logger = logging.getLogger("searchscreening_pipeline")
                for handler in logger.handlers[:]:
                    handler.close()
                    logger.removeHandler(handler)

    def test_stage8_merge_only_pipeline_writes_merged_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            run_id = "merge_stage8"
            outputs_dir = base_dir / "runs" / run_id / "outputs"
            input_dir = outputs_dir / "07_agent_input_chunks"
            result_dir = outputs_dir / "08_chunk"
            input_dir.mkdir(parents=True)
            result_dir.mkdir()
            first = self.make_agent_rows(1, 2)
            second = self.make_agent_rows(3, 1)
            first.to_excel(input_dir / "07_agent_input_part_001.xlsx", index=False)
            second.to_excel(input_dir / "07_agent_input_part_002.xlsx", index=False)
            first.to_excel(result_dir / "08_result_part_001.xlsx", index=False)
            second.to_excel(result_dir / "08_result_part_002.xlsx", index=False)
            args = argparse.Namespace(
                run_id=run_id,
                resume=True,
                query=None,
                years=None,
                no_ai=False,
                stop_before_ai=False,
                stage2_only=False,
                stage7_only=False,
                stage7_input=None,
                stage7_chunk_size=100,
                stage8_only=True,
                stage8_agent_count=2,
                stage8_input_dir=None,
                stage8_merge_only=True,
                limit=None,
                seed_limit_per_year=None,
                seed_dedupe_order=None,
                reference_workers=None,
                detail_workers=None,
                detail_batch_size=None,
                ai_workers=None,
                ai_rate_limit_per_sec=None,
                ai_max_new_rows=None,
                stage1_service_tier=None,
                stage2_workers=None,
                stage2_rate_limit_per_sec=None,
                stage2_max_new_rows=None,
                stage2_service_tier=None,
                stage2_reasoning_effort=None,
                stage2_prompt_cache_key=None,
                stage2_prompt_cache_retention=None,
                stage2_max_output_tokens=None,
                stage2_benchmark_path=None,
                model=None,
            )
            config = build_config(args)

            try:
                with patch("searchscreening_pipeline.DATA_COLLECTION_DIR", base_dir):
                    run_pipeline(config)

                state_data = json.loads((base_dir / "runs" / run_id / "state.json").read_text(encoding="utf-8"))
                merged = pd.read_excel(outputs_dir / "08_agent_merged.xlsx")
                summary = json.loads((outputs_dir / "08_agent_merged.summary.json").read_text(encoding="utf-8"))

                self.assertIn("agent_web_curation", state_data["completed_stages"])
                self.assertEqual(len(merged), 3)
                self.assertEqual(summary["status_counts"]["complete"], 2)
                self.assertEqual(summary["agent_count"], 2)
            finally:
                logger = logging.getLogger("searchscreening_pipeline")
                for handler in logger.handlers[:]:
                    handler.close()
                    logger.removeHandler(handler)

    def test_partial_ai_pipeline_does_not_complete_stage_or_write_final_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            run_id = "partial_ai"
            outputs_dir = base_dir / "runs" / run_id / "outputs"
            outputs_dir.mkdir(parents=True)
            for filename in (
                "01_seed_articles.xlsx",
                "02_reference_list_full.xlsx",
                "03_reference_details_dedup.xlsx",
            ):
                pd.DataFrame([{"title": "placeholder"}]).to_excel(outputs_dir / filename, index=False)
            pd.DataFrame(
                [
                    {"title": "First", "abstractText": "A splicing portal.", "doi": "10.1/a"},
                    {"title": "Second", "abstractText": "A splicing atlas.", "doi": "10.1/b"},
                ]
            ).to_excel(outputs_dir / "04_keyword_screened.xlsx", index=False)
            args = argparse.Namespace(
                run_id=run_id,
                resume=True,
                query=None,
                years=None,
                no_ai=False,
                limit=None,
                stop_before_ai=False,
                seed_limit_per_year=None,
                seed_dedupe_order=None,
                reference_workers=None,
                detail_workers=None,
                detail_batch_size=None,
                ai_workers=1,
                ai_rate_limit_per_sec=0,
                ai_max_new_rows=1,
                stage1_service_tier=None,
                model="gpt-5.4",
            )
            config = build_config(args)
            config["enabled_stages"] = ["ai_screen"]

            try:
                with patch("searchscreening_pipeline.DATA_COLLECTION_DIR", base_dir):
                    with patch("paper_screening.create_openai_client", return_value=FakeOpenAIClient()):
                        run_pipeline(config)

                state_data = json.loads((base_dir / "runs" / run_id / "state.json").read_text(encoding="utf-8"))
                checkpoint = base_dir / "runs" / run_id / "checkpoints" / "05_ai_check.csv"

                self.assertNotIn("ai_screen", state_data["completed_stages"])
                self.assertEqual(state_data["counts"]["ai_screen_rows"], 1)
                self.assertTrue(checkpoint.exists())
                self.assertEqual(len(pd.read_csv(checkpoint)), 1)
                self.assertFalse((outputs_dir / "05_ai_check.xlsx").exists())
                self.assertFalse((outputs_dir / "05_ai_yes_only.xlsx").exists())
            finally:
                logger = logging.getLogger("searchscreening_pipeline")
                for handler in logger.handlers[:]:
                    handler.close()
                    logger.removeHandler(handler)

    def test_partial_stage2_pipeline_does_not_complete_stage_or_write_final_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            run_id = "partial_stage2"
            outputs_dir = base_dir / "runs" / run_id / "outputs"
            outputs_dir.mkdir(parents=True)
            pd.DataFrame(
                [
                    {"title": "First", "abstractText": "A splicing portal.", "doi": "10.1/a"},
                    {"title": "Second", "abstractText": "A splicing atlas.", "doi": "10.1/b"},
                ]
            ).to_excel(outputs_dir / "05_stage1_pass_unclear_for_stage2.xlsx", index=False)
            args = argparse.Namespace(
                run_id=run_id,
                resume=True,
                query=None,
                years=None,
                no_ai=False,
                stop_before_ai=False,
                stage2_only=True,
                limit=None,
                seed_limit_per_year=None,
                seed_dedupe_order=None,
                reference_workers=None,
                detail_workers=None,
                detail_batch_size=None,
                ai_workers=None,
                ai_rate_limit_per_sec=None,
                ai_max_new_rows=None,
                stage1_service_tier=None,
                stage2_workers=1,
                stage2_rate_limit_per_sec=0,
                stage2_max_new_rows=1,
                stage2_service_tier=None,
                stage2_reasoning_effort=None,
                stage2_prompt_cache_key=None,
                stage2_prompt_cache_retention=None,
                stage2_max_output_tokens=None,
                stage2_benchmark_path=None,
                model="gpt-5.5",
            )
            config = build_config(args)

            try:
                with patch("searchscreening_pipeline.DATA_COLLECTION_DIR", base_dir):
                    with patch("paper_screening.create_openai_client", return_value=FakeOpenAIClient()):
                        run_pipeline(config)

                state_data = json.loads((base_dir / "runs" / run_id / "state.json").read_text(encoding="utf-8"))
                checkpoint = base_dir / "runs" / run_id / "checkpoints" / "06_stage2_ai_check.csv"

                self.assertNotIn("stage2_ai_check", state_data["completed_stages"])
                self.assertEqual(state_data["counts"]["stage2_ai_check_rows"], 1)
                self.assertTrue(checkpoint.exists())
                self.assertEqual(len(pd.read_csv(checkpoint)), 1)
                self.assertFalse((outputs_dir / "06_stage2_ai_check.xlsx").exists())
                self.assertFalse((outputs_dir / "06_stage2_ai_yes_only.xlsx").exists())
            finally:
                logger = logging.getLogger("searchscreening_pipeline")
                for handler in logger.handlers[:]:
                    handler.close()
                    logger.removeHandler(handler)

    def test_cli_arguments_override_defaults(self):
        args = argparse.Namespace(
            run_id="manual_run",
            resume=True,
            query='"splicing"',
            years="2026-2024",
            no_ai=True,
            limit=5,
            stop_before_ai=False,
            stage2_only=False,
            seed_limit_per_year=None,
            seed_dedupe_order=None,
            reference_workers=None,
            detail_workers=None,
            detail_batch_size=None,
            ai_workers=None,
            ai_rate_limit_per_sec=None,
            ai_max_new_rows=25,
            stage1_service_tier=None,
            stage2_workers=None,
            stage2_rate_limit_per_sec=None,
            stage2_max_new_rows=None,
            stage2_service_tier=None,
            stage2_reasoning_effort=None,
            stage2_prompt_cache_key=None,
            stage2_prompt_cache_retention=None,
            stage2_max_output_tokens=None,
            stage2_benchmark_path=None,
            model="test-model",
        )

        config = build_config(args)

        self.assertEqual(config["run_id"], "manual_run")
        self.assertEqual(config["search_query"], '"splicing"')
        self.assertEqual(config["search_years"], [2026, 2025, 2024])
        self.assertFalse(config["enable_ai"])
        self.assertEqual(config["max_rows"], 5)
        self.assertEqual(config["openai_model"], "test-model")
        self.assertEqual(config["ai_workers"], 1)
        self.assertAlmostEqual(config["ai_rate_limit_per_sec"], 500 / 60)
        self.assertEqual(config["ai_max_new_rows"], 25)
        self.assertEqual(config["stage1_service_tier"], DEFAULT_STAGE1_SERVICE_TIER)
        self.assertFalse(config["stage2_only"])

    def test_cli_can_stop_before_ai_and_limit_seed_per_year(self):
        args = argparse.Namespace(
            run_id="manual_run",
            resume=False,
            query=None,
            years="2026-2015",
            no_ai=False,
            stop_before_ai=True,
            stage2_only=False,
            limit=None,
            seed_limit_per_year=500,
            seed_dedupe_order="relevance",
            reference_workers=8,
            detail_workers=4,
            detail_batch_size=50,
            ai_workers=32,
            ai_rate_limit_per_sec=4.0,
            ai_max_new_rows=1000,
            stage1_service_tier="default",
            stage2_workers=None,
            stage2_rate_limit_per_sec=None,
            stage2_max_new_rows=None,
            stage2_service_tier=None,
            stage2_reasoning_effort=None,
            stage2_prompt_cache_key=None,
            stage2_prompt_cache_retention=None,
            stage2_max_output_tokens=None,
            stage2_benchmark_path=None,
            model=None,
        )

        config = build_config(args)

        self.assertEqual(config["search_years"], [2026, 2025, 2024, 2023, 2022, 2021, 2020, 2019, 2018, 2017, 2016, 2015])
        self.assertEqual(config["seed_limit_per_year"], 500)
        self.assertEqual(config["seed_page_size"], 500)
        self.assertEqual(config["seed_dedupe_order"], "relevance")
        self.assertEqual(config["reference_workers"], 8)
        self.assertEqual(config["detail_workers"], 4)
        self.assertEqual(config["detail_batch_size"], 50)
        self.assertEqual(config["ai_workers"], 32)
        self.assertEqual(config["ai_rate_limit_per_sec"], 4.0)
        self.assertEqual(config["ai_max_new_rows"], 1000)
        self.assertEqual(config["stage1_service_tier"], "default")
        self.assertNotIn("ai_screen", config["enabled_stages"])

    def test_cli_stage2_only_defaults_to_gpt55_and_stage2_stage(self):
        args = argparse.Namespace(
            run_id="manual_run",
            resume=True,
            query=None,
            years=None,
            no_ai=False,
            stop_before_ai=False,
            stage2_only=True,
            limit=None,
            seed_limit_per_year=None,
            seed_dedupe_order=None,
            reference_workers=None,
            detail_workers=None,
            detail_batch_size=None,
            ai_workers=None,
            ai_rate_limit_per_sec=None,
            ai_max_new_rows=None,
            stage1_service_tier=None,
            stage2_workers=32,
            stage2_rate_limit_per_sec=0,
            stage2_max_new_rows=1000,
            stage2_service_tier=None,
            stage2_reasoning_effort=None,
            stage2_prompt_cache_key=None,
            stage2_prompt_cache_retention=None,
            stage2_max_output_tokens=None,
            stage2_benchmark_path=None,
            model=None,
        )

        config = build_config(args)

        self.assertTrue(config["stage2_only"])
        self.assertEqual(config["enabled_stages"], ["stage2_ai_check"])
        self.assertEqual(config["openai_model"], "gpt-5.5")
        self.assertEqual(config["stage2_workers"], 32)
        self.assertEqual(config["stage2_max_new_rows"], 1000)
        self.assertEqual(config["stage2_reasoning_effort"], DEFAULT_STAGE2_REASONING_EFFORT)

    def test_cli_stage8_only_sets_agent_config_and_stage(self):
        args = argparse.Namespace(
            run_id="manual_run",
            resume=True,
            query=None,
            years=None,
            no_ai=False,
            stop_before_ai=False,
            stage2_only=False,
            stage7_only=False,
            stage8_only=True,
            stage8_agent_count=10,
            stage8_input_dir="custom_chunks",
            stage8_merge_only=True,
            limit=None,
            seed_limit_per_year=None,
            seed_dedupe_order=None,
            reference_workers=None,
            detail_workers=None,
            detail_batch_size=None,
            ai_workers=None,
            ai_rate_limit_per_sec=None,
            ai_max_new_rows=None,
            stage1_service_tier=None,
            stage2_workers=None,
            stage2_rate_limit_per_sec=None,
            stage2_max_new_rows=None,
            stage2_service_tier=None,
            stage2_reasoning_effort=None,
            stage2_prompt_cache_key=None,
            stage2_prompt_cache_retention=None,
            stage2_max_output_tokens=None,
            stage2_benchmark_path=None,
            model=None,
        )

        config = build_config(args)

        self.assertTrue(config["stage8_only"])
        self.assertEqual(config["enabled_stages"], ["agent_web_curation"])
        self.assertEqual(config["stage8_agent_count"], 10)
        self.assertEqual(config["stage8_input_dir"], "custom_chunks")
        self.assertTrue(config["stage8_merge_only"])

    def test_cli_stage7_curated_input_and_default_chunk_size(self):
        args = argparse.Namespace(
            run_id="manual_run",
            resume=True,
            query=None,
            years=None,
            no_ai=False,
            stop_before_ai=False,
            stage2_only=False,
            stage7_only=True,
            stage8_only=False,
            stage7_input=None,
            stage7_input_format="curated",
            stage7_forced_include=None,
            stage7_chunk_size=None,
            limit=None,
            model=None,
        )

        config = build_config(args)

        self.assertTrue(config["stage7_only"])
        self.assertEqual(config["enabled_stages"], ["agent_table_prep"])
        self.assertEqual(config["stage7_input_format"], "curated")
        self.assertEqual(config["stage7_chunk_size"], 25)
        self.assertEqual(config["screening_policy_version"], SCREENING_POLICY_VERSION)

    def test_large_seed_limit_caps_page_size_to_europe_pmc_max(self):
        args = argparse.Namespace(
            run_id="manual_run",
            resume=False,
            query=None,
            years="2026-2025",
            no_ai=False,
            stop_before_ai=True,
            stage2_only=False,
            limit=None,
            seed_limit_per_year=2000,
            seed_dedupe_order=None,
            reference_workers=None,
            detail_workers=None,
            detail_batch_size=None,
            ai_workers=None,
            ai_rate_limit_per_sec=None,
            ai_max_new_rows=None,
            stage1_service_tier=None,
            stage2_workers=None,
            stage2_rate_limit_per_sec=None,
            stage2_max_new_rows=None,
            stage2_service_tier=None,
            stage2_reasoning_effort=None,
            stage2_prompt_cache_key=None,
            stage2_prompt_cache_retention=None,
            stage2_max_output_tokens=None,
            stage2_benchmark_path=None,
            model=None,
        )

        config = build_config(args)

        self.assertEqual(config["seed_limit_per_year"], 2000)
        self.assertEqual(config["seed_page_size"], 1000)

    def test_reference_details_can_fetch_with_worker_threads(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state = self.make_state(tmp_path)
            reference_df = pd.DataFrame(
                [
                    {"ref_source": "MED", "ref_id": "1"},
                    {"ref_source": "MED", "ref_id": "2"},
                ]
            )

            def fake_fetch(source, ref_id, **kwargs):
                return {
                    "title": f"Title {ref_id}",
                    "source": source,
                    "pmid": ref_id,
                    "citedByCount": "1",
                }

            with patch("paper_search.fetch_article_detail", side_effect=fake_fetch):
                result = collect_reference_details(
                    reference_df,
                    tmp_path / "details.csv",
                    state,
                    rate_limit_per_sec=0,
                    max_workers=2,
                )

            self.assertEqual(len(result), 2)
            self.assertEqual(state.completed_keys("detail_refs"), {"MED/1", "MED/2"})
            self.assertTrue((tmp_path / "details.csv").exists())

    def test_reference_details_batch_falls_back_for_missing_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state = self.make_state(tmp_path)
            reference_df = pd.DataFrame(
                [
                    {"ref_source": "MED", "ref_id": "1"},
                    {"ref_source": "MED", "ref_id": "2"},
                ]
            )

            def fake_batch(source, ref_ids, **kwargs):
                return [
                    {
                        "id": "1",
                        "title": "Title 1",
                        "source": source,
                        "pmid": "1",
                        "citedByCount": "1",
                    }
                ]

            def fake_fetch(source, ref_id, **kwargs):
                return {
                    "id": ref_id,
                    "title": f"Title {ref_id}",
                    "source": source,
                    "pmid": ref_id,
                    "citedByCount": "1",
                }

            with patch("paper_search.fetch_article_details_batch", side_effect=fake_batch) as batch_mock:
                with patch("paper_search.fetch_article_detail", side_effect=fake_fetch) as fetch_mock:
                    result = collect_reference_details(
                        reference_df,
                        tmp_path / "details.csv",
                        state,
                        rate_limit_per_sec=0,
                        max_workers=2,
                        batch_size=2,
                    )

            self.assertEqual(len(result), 2)
            self.assertEqual(batch_mock.call_count, 1)
            fetch_mock.assert_called_once()
            self.assertEqual(state.completed_keys("detail_refs"), {"MED/1", "MED/2"})


if __name__ == "__main__":
    unittest.main()
