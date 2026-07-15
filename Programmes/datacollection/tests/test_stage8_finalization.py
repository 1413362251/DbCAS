import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


MODULE_DIR = Path(__file__).resolve().parents[1]
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

from paper_screening import SCREENING_POLICY_VERSION  # noqa: E402
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
)
from stage8_finalization import (  # noqa: E402
    ACCESSIBILITY_COLUMN,
    AGENT_CHECKED_URL_COLUMN,
    AGENT_CLICK_PATH_COLUMN,
    AGENT_REVIEW_STATEMENT_COLUMN,
    AGENT_VISIT_STATUS_COLUMN,
    CANONICAL_ID_COLUMN,
    DATABASE_NAME_COLUMN,
    DATABASE_URL_COLUMN,
    DECISION_COLUMN,
    DUPLICATE_GROUP_COLUMN,
    ID_COLUMN,
    INCREMENTAL_ACTION_COLUMN,
    INCREMENTAL_CANONICAL_COLUMN,
    INCREMENTAL_GROUP_COLUMN,
    INCREMENTAL_REPRESENTATIVE_COLUMN,
    INCREMENTAL_STATEMENT_COLUMN,
    REPRESENTATIVE_ID_COLUMN,
    TITLE_COLUMN,
    apply_incremental_duplicate_decisions,
    build_duplicate_candidate_map,
    augment_evidence_accessibility_audit,
    enrich_accessibility_audit,
    merge_final_review,
    normalize_neural_link_consistency,
    prepare_incremental_duplicate_review,
    prepare_final_review_dataframe,
    run_accessibility_audit,
    validate_conditional_evidence,
    validate_final_review_result,
    write_final_review_chunks,
)


class Stage8FinalizationTests(unittest.TestCase):
    def make_rows(self, count=3):
        rows = []
        for row_id in range(1, count + 1):
            row = {column: "" for column in AGENT_INPUT_COLUMNS}
            row[ID_COLUMN] = row_id
            row[TITLE_COLUMN] = f"Database paper {row_id}"
            row[DATABASE_NAME_COLUMN] = f"DB{row_id}"
            row[DATABASE_URL_COLUMN] = f"https://example.org/db{row_id}"
            row["<sub,t-word-doi> doi"] = f"10.1/{row_id}"
            row["<sub,t-word-pmid> pmid"] = str(1000 + row_id)
            row["<sub,t-numeric> year"] = 2025
            row[ORIGINAL_DATABASE_COLUMN] = "no"
            row[POLICY_VERSION_COLUMN] = SCREENING_POLICY_VERSION
            row[ACCESSIBILITY_COLUMN] = "live"
            row[DECISION_COLUMN] = "yes"
            row["<sub,t-word> confirmation_reason"] = "searchable transcript records"
            row[QUALIFICATION_BASIS_COLUMN] = "transcript_or_isoform_model"
            row[EXCLUSION_CODE_COLUMN] = "not_applicable"
            row[EVIDENCE_URL_COLUMN] = f"https://example.org/db{row_id}/records"
            row[EVIDENCE_SOURCE_TYPE_COLUMN] = "official_database"
            row[EVIDENCE_STATEMENT_COLUMN] = "The official database shows searchable transcript records."
            row[EVIDENCE_CHECKED_DATE_COLUMN] = "2026-07-11"
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

    def access_rows(self, df, status="reachable"):
        return pd.DataFrame(
            [
                {
                    ID_COLUMN: row[ID_COLUMN],
                    "status": status,
                    "final_url": row[DATABASE_URL_COLUMN],
                }
                for _, row in df.iterrows()
            ]
        )

    def complete_review(self, review, canonical_by_id=None, representative_by_id=None):
        result = review.copy()
        canonical_by_id = canonical_by_id or {}
        representative_by_id = representative_by_id or {}
        for index, row in result.iterrows():
            row_id = str(int(row[ID_COLUMN]))
            result.at[index, CANONICAL_ID_COLUMN] = canonical_by_id.get(row_id, f"db-{row_id}")
            result.at[index, REPRESENTATIVE_ID_COLUMN] = representative_by_id.get(row_id, row_id)
            result.at[index, AGENT_VISIT_STATUS_COLUMN] = "entered_database"
            result.at[index, AGENT_CHECKED_URL_COLUMN] = row[DATABASE_URL_COLUMN]
            result.at[index, AGENT_CLICK_PATH_COLUMN] = "direct"
            result.at[index, AGENT_REVIEW_STATEMENT_COLUMN] = "Agent entered the official database and verified its records."
        return result

    def test_yes_only_ascot_name_builds_duplicate_candidate(self):
        df = self.make_rows()
        df.loc[0, DATABASE_NAME_COLUMN] = "ASCOT"
        df.loc[1, DATABASE_NAME_COLUMN] = "ASCOT database"
        df.loc[2, DECISION_COLUMN] = "no"
        df.loc[2, DATABASE_NAME_COLUMN] = "ASCOT"

        mapping = build_duplicate_candidate_map(df)

        self.assertEqual(set(mapping[ID_COLUMN].astype(str)), {"1", "2"})
        self.assertEqual(mapping[DUPLICATE_GROUP_COLUMN].nunique(), 1)

    def test_shared_host_different_paths_does_not_group(self):
        df = self.make_rows(2)
        df[DATABASE_NAME_COLUMN] = ["Resource Alpha", "Resource Beta"]
        df[DATABASE_URL_COLUMN] = [
            "https://www.ncbi.nlm.nih.gov/resource-alpha",
            "https://www.ncbi.nlm.nih.gov/resource-beta",
        ]

        self.assertTrue(build_duplicate_candidate_map(df).empty)

    def test_placeholder_names_and_urls_do_not_group(self):
        df = self.make_rows(2)
        df[DATABASE_NAME_COLUMN] = ["unknown", "unknown"]
        df[DATABASE_URL_COLUMN] = ["unknown", "unknown"]

        self.assertTrue(build_duplicate_candidate_map(df).empty)

    def test_reachable_redirect_final_url_can_create_exact_duplicate_group(self):
        df = self.make_rows(2)
        df[DATABASE_NAME_COLUMN] = ["Old label A", "Different label B"]
        df[DATABASE_URL_COLUMN] = ["https://legacy-a.example/start", "https://legacy-b.example/start"]
        audit = self.access_rows(df)
        audit["final_url"] = ["https://current.example/ascot", "https://current.example/ascot/"]

        mapping = build_duplicate_candidate_map(df, audit)

        self.assertEqual(set(mapping[ID_COLUMN].astype(str)), {"1", "2"})
        self.assertEqual(mapping[DUPLICATE_GROUP_COLUMN].nunique(), 1)

    def test_conditional_evidence_rules(self):
        df = self.make_rows(2)
        df.loc[1, ACCESSIBILITY_COLUMN] = "dead"
        df.loc[1, EVIDENCE_SOURCE_TYPE_COLUMN] = "publication"
        self.assertIsNone(validate_conditional_evidence(df))

        df.loc[0, EVIDENCE_SOURCE_TYPE_COLUMN] = "publication"
        error = validate_conditional_evidence(df)
        self.assertIn("live evidence must be official", error)

        df.loc[0, EVIDENCE_SOURCE_TYPE_COLUMN] = "official_database"
        df.loc[1, EVIDENCE_SOURCE_TYPE_COLUMN] = "web_archive"
        error = validate_conditional_evidence(df)
        self.assertIn("dead evidence must be publication", error)

    def test_renal_cortex_does_not_trigger_neural_none_conflict(self):
        df = self.make_rows(1)
        df.loc[0, "<main,t-word-tag> tissue_or_brain_region"] = "kidney;renal cortex;renal medulla"
        from stage8_finalization import validate_final_decision_fields

        self.assertIsNone(validate_final_decision_fields(df))

    def test_explicit_neural_metadata_promotes_none_to_partial(self):
        df = self.make_rows(1)
        df.loc[0, "<main,t-word-tag> tissue_or_brain_region"] = "brain;hippocampus"

        normalized = normalize_neural_link_consistency(df)

        self.assertEqual(normalized.loc[0, "<main,t-word-tag> neural_link"], "partial")

    def test_final_review_allows_url_change_but_not_identity_change(self):
        raw = self.make_rows(1)
        review, _ = prepare_final_review_dataframe(raw, self.access_rows(raw), AGENT_INPUT_COLUMNS)
        result = self.complete_review(review)
        result.loc[0, DATABASE_URL_COLUMN] = "https://example.org/current-database"
        result.loc[0, AGENT_CHECKED_URL_COLUMN] = "https://example.org/current-database"
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.xlsx"
            result_path = Path(tmp) / "result.xlsx"
            review.to_excel(input_path, index=False)
            result.to_excel(result_path, index=False)
            self.assertEqual(validate_final_review_result(input_path, result_path, AGENT_INPUT_COLUMNS)["status"], "complete")

            result.loc[0, TITLE_COLUMN] = "Changed paper identity"
            result.to_excel(result_path, index=False)
            invalid = validate_final_review_result(input_path, result_path, AGENT_INPUT_COLUMNS)
            self.assertEqual(invalid["status"], "invalid")
            self.assertIn("immutable", invalid["error"])

    def test_duplicate_merge_uses_representative_and_original48_or(self):
        raw = self.make_rows(2)
        raw[DATABASE_NAME_COLUMN] = ["ASCOT", "ASCOT database"]
        raw.loc[0, ORIGINAL_DATABASE_COLUMN] = "yes"
        review, _ = prepare_final_review_dataframe(raw, self.access_rows(raw), AGENT_INPUT_COLUMNS)
        result = self.complete_review(
            review,
            canonical_by_id={"1": "ascot", "2": "ascot"},
            representative_by_id={"1": "2", "2": "2"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "result.xlsx"
            result.to_excel(result_path, index=False)
            manifest = pd.DataFrame([{"status": "complete", "result_path": str(result_path)}])

            final, audit = merge_final_review(raw, manifest, AGENT_INPUT_COLUMNS)

            self.assertEqual(len(final), 1)
            self.assertEqual(str(int(final.iloc[0][ID_COLUMN])), "2")
            self.assertEqual(final.iloc[0][ORIGINAL_DATABASE_COLUMN], "yes")
            self.assertEqual(audit.iloc[0]["member_ids"], "1;2")
            self.assertEqual(audit.iloc[0]["removed_ids"], "1")

    def test_duplicate_group_is_not_split_across_chunks(self):
        raw = self.make_rows(27)
        raw.loc[:2, DATABASE_NAME_COLUMN] = "ASCOT"
        review, _ = prepare_final_review_dataframe(raw, self.access_rows(raw), AGENT_INPUT_COLUMNS)
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_final_review_chunks(review, Path(tmp), chunk_size=2)
            chunks = [pd.read_excel(path, dtype=object).fillna("") for path in paths]
            member_chunks = [chunk for chunk in chunks if set(chunk[ID_COLUMN].astype(str)) & {"1", "2", "3"}]
            self.assertEqual(len(member_chunks), 1)
            self.assertTrue({"1", "2", "3"}.issubset(set(member_chunks[0][ID_COLUMN].astype(str))))

    def test_accessibility_audit_checks_and_prefixes_evidence_urls(self):
        raw = self.make_rows(2)

        def fake_check(urls, workers, timeout):
            self.assertEqual(workers, 32)
            self.assertEqual(timeout, 120)
            self.assertEqual(len(urls), 4)
            return [
                {
                    "original_url": url,
                    "status": "reachable" if index != 3 else "unreachable",
                    "final_url": url,
                    "http_status": 200 if index != 3 else 404,
                    "redirect_chain": [],
                    "error_category": "" if index != 3 else "http_error",
                    "tls_warning": False,
                }
                for index, url in enumerate(urls)
            ]

        audit = run_accessibility_audit(raw, check_urls_func=fake_check)

        self.assertEqual(audit.loc[0, "status"], "reachable")
        self.assertEqual(audit.loc[1, "evidence_status"], "unreachable")
        self.assertEqual(audit.loc[1, "evidence_http_status"], 404)
        self.assertEqual(audit.loc[1, "evidence_error_category"], "http_error")

    def test_legacy_database_only_audit_can_supplement_evidence(self):
        raw = self.make_rows(1)
        legacy = self.access_rows(raw)

        def fake_check(urls, workers, timeout):
            return [{"original_url": urls[0], "status": "continue_required", "final_url": urls[0], "redirect_chain": [urls[0]], "tls_warning": True}]

        augmented = augment_evidence_accessibility_audit(raw, legacy, check_urls_func=fake_check)

        self.assertEqual(augmented.loc[0, "status"], "reachable")
        self.assertEqual(augmented.loc[0, "evidence_status"], "continue_required")
        self.assertTrue(augmented.loc[0, "evidence_tls_warning"])

    def test_accessibility_audit_is_enriched_with_agent_browser_conclusion(self):
        raw = self.make_rows(2)
        audit = self.access_rows(raw)
        review, _ = prepare_final_review_dataframe(raw, audit, AGENT_INPUT_COLUMNS)
        result = self.complete_review(review)
        result.loc[0, DATABASE_URL_COLUMN] = "https://example.org/direct-db1"
        result.loc[0, AGENT_CHECKED_URL_COLUMN] = "https://example.org/direct-db1"
        result.loc[0, AGENT_CLICK_PATH_COLUMN] = "warning -> Continue -> database"
        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "result.xlsx"
            result.to_excel(result_path, index=False)
            manifest = pd.DataFrame([{"status": "complete", "result_path": str(result_path)}])

            enriched = enrich_accessibility_audit(audit, manifest)

        self.assertEqual(enriched["agent_reviewed"].tolist(), ["yes", "yes"])
        self.assertEqual(enriched.loc[0, "agent_final_database_url"], "https://example.org/direct-db1")
        self.assertEqual(enriched.loc[0, AGENT_CHECKED_URL_COLUMN], "https://example.org/direct-db1")
        self.assertEqual(enriched.loc[0, AGENT_CLICK_PATH_COLUMN], "warning -> Continue -> database")
        self.assertEqual(enriched.loc[0, "agent_final_accessibility"], "live")

    def test_evidence_failure_cross_domain_redirect_or_tls_forces_review(self):
        raw = self.make_rows(3)
        raw[DECISION_COLUMN] = "no"
        audit = self.access_rows(raw)
        audit["evidence_status"] = "reachable"
        audit["redirect_chain"] = ""
        audit["evidence_redirect_chain"] = ""
        audit["tls_warning"] = False
        audit["evidence_tls_warning"] = False
        audit.loc[0, "evidence_status"] = "unreachable"
        audit.loc[1, "redirect_chain"] = "https://old.example -> https://new.example"
        audit.loc[1, "final_url"] = "https://new.example/db2"
        audit.loc[2, "evidence_tls_warning"] = True

        review, _ = prepare_final_review_dataframe(raw, audit, AGENT_INPUT_COLUMNS)

        self.assertEqual(set(review[ID_COLUMN].astype(str)), {"1", "2", "3"})

    def test_incremental_duplicate_merge_and_original48_or(self):
        final_df = self.make_rows(3)
        final_df.loc[0, DATABASE_NAME_COLUMN] = "Cross Chunk DB"
        final_df.loc[1, DATABASE_NAME_COLUMN] = "Cross Chunk DB database"
        final_df.loc[0, ORIGINAL_DATABASE_COLUMN] = "yes"
        review = prepare_incremental_duplicate_review(final_df)
        decisions = review.copy()
        decisions[INCREMENTAL_ACTION_COLUMN] = "merge"
        decisions[INCREMENTAL_CANONICAL_COLUMN] = "cross_chunk_db"
        decisions[INCREMENTAL_REPRESENTATIVE_COLUMN] = "2"
        decisions[INCREMENTAL_STATEMENT_COLUMN] = "The official pages confirm one database; ID 2 is the update paper."

        merged, audit = apply_incremental_duplicate_decisions(final_df, decisions)

        self.assertEqual(set(merged[ID_COLUMN].map(str)), {"2", "3"})
        self.assertEqual(merged.loc[merged[ID_COLUMN].map(str) == "2", ORIGINAL_DATABASE_COLUMN].iloc[0], "yes")
        self.assertEqual(audit.loc[0, "removed_ids"], "1")

    def test_incremental_duplicate_split_keeps_rows(self):
        final_df = self.make_rows(2)
        final_df[DATABASE_NAME_COLUMN] = ["Shared Label", "Shared Label"]
        review = prepare_incremental_duplicate_review(final_df)
        decisions = review.copy()
        decisions[INCREMENTAL_ACTION_COLUMN] = "split"
        decisions[INCREMENTAL_CANONICAL_COLUMN] = ["resource_1", "resource_2"]
        decisions[INCREMENTAL_REPRESENTATIVE_COLUMN] = decisions[ID_COLUMN].map(str)
        decisions[INCREMENTAL_STATEMENT_COLUMN] = "Official records show distinct databases despite the shared label."

        split, audit = apply_incremental_duplicate_decisions(final_df, decisions)

        self.assertEqual(len(split), 2)
        self.assertEqual(audit.loc[0, "incremental_action"], "split")

    def test_cross_candidate_canonical_collision_is_deferred_to_incremental_review(self):
        raw = self.make_rows(2)
        raw[DATABASE_NAME_COLUMN] = ["MIsoMine", "IsoFunc;MIsoMine;Hisonet"]
        raw[DATABASE_URL_COLUMN] = ["https://a.example/misomine", "https://b.example/isofunc"]
        review, _ = prepare_final_review_dataframe(raw, self.access_rows(raw), AGENT_INPUT_COLUMNS)
        result = self.complete_review(
            review,
            canonical_by_id={"1": "misomine", "2": "misomine"},
            representative_by_id={"1": "1", "2": "2"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "result.xlsx"
            result.to_excel(result_path, index=False)
            manifest = pd.DataFrame([{"status": "complete", "result_path": str(result_path)}])

            initial, canonical_audit = merge_final_review(raw, manifest, AGENT_INPUT_COLUMNS)
            incremental = prepare_incremental_duplicate_review(initial, canonical_audit=canonical_audit)

        self.assertEqual(len(initial), 2)
        self.assertEqual(set(incremental[ID_COLUMN].map(str)), {"1", "2"})
        self.assertEqual(incremental[INCREMENTAL_GROUP_COLUMN].nunique(), 1)


if __name__ == "__main__":
    unittest.main()
