import contextlib
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd


DATABASE_DIR = Path(__file__).resolve().parents[1]
if str(DATABASE_DIR) not in sys.path:
    sys.path.insert(0, str(DATABASE_DIR))

import database_initialize as loader  # noqa: E402


def make_workbook_frame(ids=("1",)):
    headers = [
        f"<{position_tag},{data_type}> {name}"
        for name, position_tag, data_type in loader.EXPECTED_DISPLAY_SCHEMA
    ] + ["id"]
    base_row = {
        "database_name": "Example Database",
        "database_url": "https://example.org/database",
        "accessibility": "yes",
        "year": 2024,
        "citation": pd.NA,
        "species": "Homo sapiens",
        "tissue_or_brain_region": "brain",
        "sequencing_resolution": "bulk",
        "read_technology": "short",
        "classification_code": "I_1",
        "title": "Example database paper",
        "doi": "10.1000/example",
        "description": "Example description",
        "disease_association": "unknown",
        "developmental_association": "adult",
        "cell_type": "neuron",
    }
    rows = []
    for row_id in ids:
        values = [base_row[name] for name, _, _ in loader.EXPECTED_DISPLAY_SCHEMA]
        rows.append(values + [row_id])
    return pd.DataFrame(rows, columns=headers)


class DatabaseInitializeTests(unittest.TestCase):
    def test_doi_normalization_and_highest_citation(self):
        value = (
            "https://doi.org/10.1000/First; doi:10.1000/second;"
            "10.1000/First"
        )
        self.assertEqual(
            loader.normalize_doi_value(value),
            "10.1000/First;10.1000/second",
        )
        citations = {"10.1000/first": 18, "10.1000/second": 42}
        result = loader.get_max_citation_count(
            value, lambda doi: citations.get(doi.casefold())
        )
        self.assertEqual(result, 42)
        self.assertTrue(pd.isna(loader.normalize_doi_value("unknown")))

    def test_batch_citations_deduplicate_and_fallback_for_missing_item(self):
        response = Mock(status_code=200)
        response.json.return_value = [
            {"citationCount": 18},
            None,
        ]
        session = Mock()
        session.post.return_value = response

        with patch.object(
            loader, "get_semantic_scholar_citations", return_value=42
        ) as fallback:
            citations = loader.get_semantic_scholar_batch_citations(
                [
                    "10.1000/First",
                    "doi:10.1000/second",
                    "10.1000/first",
                ],
                session=session,
            )

        self.assertEqual(
            citations,
            {"10.1000/first": 18, "10.1000/second": 42},
        )
        request = session.post.call_args
        self.assertEqual(
            request.kwargs["json"],
            {"ids": ["DOI:10.1000/First", "DOI:10.1000/second"]},
        )
        self.assertEqual(request.kwargs["params"], {"fields": "citationCount"})
        fallback.assert_called_once_with("10.1000/second", session=session)

    def test_batch_request_failure_does_not_trigger_single_request_storm(self):
        response = Mock(status_code=429)
        session = Mock()
        session.post.return_value = response

        with patch.object(loader, "get_semantic_scholar_citations") as fallback:
            citations = loader.get_semantic_scholar_batch_citations(
                ["10.1000/first", "10.1000/second"],
                session=session,
                max_attempts=1,
            )

        self.assertEqual(
            citations,
            {"10.1000/first": None, "10.1000/second": None},
        )
        fallback.assert_not_called()

    def test_tag_normalization_removes_spaces_empty_items_and_duplicates(self):
        self.assertEqual(
            loader.normalize_tag_value("brain; cerebellum;;Brain; spinal cord "),
            "brain;cerebellum;spinal cord",
        )

    def test_missing_url_is_inaccessible(self):
        self.assertFalse(
            loader.accessibility_from_result(
                {"status": "missing", "accessible": None}
            )
        )

    def test_standard_schema_and_data_pass_validation(self):
        df_raw = make_workbook_frame()
        metadata = loader.parse_columns(df_raw.columns)
        loader.validate_schema(metadata)
        rename_map = {
            meta["original_name"]: meta["column_name"] for meta in metadata
        }
        df_data = df_raw.rename(columns=rename_map)
        loader.normalize_loader_values(df_raw, df_data, metadata)
        loader.validate_data(metadata, df_data)

    def test_duplicate_id_is_rejected_with_row_context(self):
        df_raw = make_workbook_frame(ids=("1", "1"))
        metadata = loader.parse_columns(df_raw.columns)
        loader.validate_schema(metadata)
        rename_map = {
            meta["original_name"]: meta["column_name"] for meta in metadata
        }
        df_data = df_raw.rename(columns=rename_map)
        with self.assertRaisesRegex(ValueError, "duplicate id"):
            loader.validate_data(metadata, df_data)

    def test_unapproved_display_column_is_rejected(self):
        df_raw = make_workbook_frame()
        columns = list(df_raw.columns)
        columns[5] = "<main,t-word-tag> unapproved_feature"
        with self.assertRaisesRegex(ValueError, "unapproved display column"):
            loader.validate_schema(loader.parse_columns(columns))

    def test_invalid_url_is_rejected(self):
        df_raw = make_workbook_frame()
        df_raw.iloc[0, 1] = "not a URL"
        metadata = loader.parse_columns(df_raw.columns)
        loader.validate_schema(metadata)
        rename_map = {
            meta["original_name"]: meta["column_name"] for meta in metadata
        }
        df_data = df_raw.rename(columns=rename_map)
        with self.assertRaisesRegex(ValueError, "valid HTTP"):
            loader.validate_data(metadata, df_data)

    def test_main_normalizes_and_writes_expected_values(self):
        df_raw = make_workbook_frame(ids=("1", "2"))
        column_by_name = {
            name: index
            for index, (name, _, _) in enumerate(loader.EXPECTED_DISPLAY_SCHEMA)
        }
        df_raw.iloc[0, column_by_name["doi"]] = "10.1000/first;10.1000/second"
        df_raw.iloc[1, column_by_name["doi"]] = "unknown"
        df_raw.iloc[0, column_by_name["tissue_or_brain_region"]] = (
            "brain; cerebellum;brain"
        )
        df_raw.iloc[1, column_by_name["database_url"]] = pd.NA

        citation_counts = {"10.1000/first": 18, "10.1000/second": 42}

        def fake_batch_fetcher(dois, session=None):
            return {
                doi.casefold(): citation_counts.get(doi.casefold(), 7)
                for doi in dois
            }

        def fake_url_check(urls):
            return [
                {
                    "status": "missing" if pd.isna(url) else "reachable",
                    "accessible": None if pd.isna(url) else True,
                }
                for url in urls
            ]

        with tempfile.TemporaryDirectory() as temp_dir:
            excel_path = Path(temp_dir) / "data.xlsx"
            db_path = Path(temp_dir) / "data.db"
            df_raw.to_excel(excel_path, index=False)
            with (
                patch.object(loader, "EXCEL_PATH", excel_path),
                patch.object(loader, "DB_PATH", db_path),
                patch.object(
                    loader,
                    "get_semantic_scholar_batch_citations",
                    side_effect=fake_batch_fetcher,
                ),
                patch.object(loader, "check_urls", side_effect=fake_url_check),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                loader.main()

            output_excel = pd.read_excel(excel_path)
            self.assertEqual(output_excel.iloc[0, column_by_name["citation"]], 42)
            self.assertTrue(
                pd.isna(output_excel.iloc[1, column_by_name["citation"]])
            )
            self.assertEqual(
                output_excel.iloc[0, column_by_name["tissue_or_brain_region"]],
                "brain;cerebellum",
            )
            self.assertTrue(pd.isna(output_excel.iloc[1, column_by_name["doi"]]))
            self.assertFalse(
                bool(output_excel.iloc[1, column_by_name["accessibility"]])
            )

            conn = sqlite3.connect(db_path)
            try:
                stored = conn.execute(
                    "SELECT citation, accessibility FROM database_info ORDER BY id"
                ).fetchall()
                expand_columns = conn.execute(
                    "SELECT column_name FROM display_columns "
                    "WHERE display_group = 'expand' ORDER BY order_index"
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual(stored, [(42.0, 1), (None, 0)])
            self.assertEqual(
                [row[0] for row in expand_columns],
                [
                    "title",
                    "doi",
                    "disease_association",
                    "developmental_association",
                    "cell_type",
                    "description",
                ],
            )


if __name__ == "__main__":
    unittest.main()
