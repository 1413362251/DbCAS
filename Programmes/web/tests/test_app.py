import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


WEB_DIR = Path(__file__).resolve().parents[1]
if str(WEB_DIR) not in sys.path:
    sys.path.insert(0, str(WEB_DIR))

import app as web_app  # noqa: E402


class WebSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        tags = ";".join(f"tag{index:03d}" for index in range(1, 56))
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                CREATE TABLE database_info (
                    database_name TEXT,
                    species TEXT,
                    classification_code TEXT,
                    doi TEXT,
                    secret_note TEXT,
                    main_collection TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO database_info VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "Visible Alpha",
                    tags,
                    "I_1",
                    "10.1000/first;10.1000/second",
                    "hiddenneedle",
                    "yes",
                ),
            )
            conn.execute(
                "INSERT INTO database_info VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "Full Beta",
                    "tag001",
                    "II_1",
                    "10.1000/full",
                    "",
                    "no",
                ),
            )
            conn.execute(
                """
                CREATE TABLE display_columns (
                    column_name TEXT,
                    display_name TEXT,
                    display_group TEXT,
                    order_index INTEGER,
                    is_access INTEGER,
                    data_type TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO display_columns VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("database_name", "database_name", "main", 0, 0, "t-word"),
                    ("species", "species", "main", 1, 0, "t-word-tag"),
                    (
                        "classification_code",
                        "classification_code",
                        "main",
                        2,
                        0,
                        "t-word-tag",
                    ),
                    ("doi", "doi", "expand", 3, 0, "t-word-doi"),
                    ("secret_note", "secret_note", "hidden", 4, 0, None),
                    (
                        "main_collection",
                        "main_collection",
                        "hidden",
                        5,
                        0,
                        None,
                    ),
                ],
            )
            conn.commit()
        finally:
            conn.close()
        self.original_db_path = web_app.DB_PATH
        web_app.DB_PATH = self.db_path
        web_app.app.config.update(TESTING=True)
        self.client = web_app.app.test_client()

    def tearDown(self):
        web_app.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def test_hidden_column_does_not_participate_in_search(self):
        response = self.client.get("/search", query_string={"q": "hiddenneedle"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Visible Alpha", response.get_data(as_text=True))

        visible_response = self.client.get(
            "/search", query_string={"q": "Visible"}
        )
        self.assertIn("Visible Alpha", visible_response.get_data(as_text=True))

    def test_multiple_dois_render_as_separate_links(self):
        response = self.client.get("/search")
        html = response.get_data(as_text=True)
        self.assertIn('href="https://doi.org/10.1000/first"', html)
        self.assertIn('href="https://doi.org/10.1000/second"', html)
        self.assertNotIn(
            'href="https://doi.org/10.1000/first;10.1000/second"', html
        )

    def test_tag_filter_previews_50_and_exposes_expand_trigger(self):
        response = self.client.get("/search")
        html = response.get_data(as_text=True)
        self.assertIn("Expand (5 more)", html)
        self.assertIn('<option value="tag050">tag050</option>', html)
        self.assertNotIn('<option value="tag055">tag055</option>', html)
        self.assertIn('"tag055"', html)

    def test_classification_header_links_to_help_explanation(self):
        html = self.client.get("/search").get_data(as_text=True)

        self.assertIn("Classification Code", html)
        self.assertRegex(
            html,
            r'<a class="classification-help-link" '
            r'href="/about#classification-standard" '
            r'aria-label="Learn about database classification"[^>]*>\?</a>',
        )

    def test_dataset_switch_filters_main_collection_and_preserves_keyword(self):
        full_html = self.client.get("/search").get_data(as_text=True)
        self.assertIn("Visible Alpha", full_html)
        self.assertIn("Full Beta", full_html)
        full_button = re.search(
            r'<button\s+([^>]*\bvalue="full"[^>]*)>', full_html
        )
        self.assertIsNotNone(full_button)
        self.assertIn("is-active", full_button.group(1))
        self.assertIn('aria-pressed="true"', full_button.group(1))

        main_html = self.client.get(
            "/search",
            query_string={"q": "Visible", "dataset": "main"},
        ).get_data(as_text=True)
        self.assertIn("Visible Alpha", main_html)
        self.assertNotIn("Full Beta", main_html)
        self.assertIn('name="dataset" value="main"', main_html)
        self.assertIn('name="q" value="Visible"', main_html)
        self.assertRegex(
            main_html,
            r'<section class="header">\s*'
            r'<h1 class="search-header__keyword">[\s\S]*?</h1>\s*'
            r'<form class="dataset-switch"',
        )

        main_default_html = self.client.get(
            "/search", query_string={"dataset": "main"}
        ).get_data(as_text=True)
        self.assertIn("(Default: main collection)", main_default_html)
        self.assertIn("Search… (default: main collection)", main_default_html)


class HelpPageTests(unittest.TestCase):
    def setUp(self):
        web_app.app.config.update(TESTING=True)
        self.client = web_app.app.test_client()

    def test_help_page_contains_updated_content_and_three_ordered_cards(self):
        response = self.client.get("/about")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)

        self.assertIn("<title>Help &amp; Feedback</title>", html)
        self.assertIn("16,212 splicing-related publications", html)
        self.assertIn("candidate pool to 383,439 publications", html)
        self.assertIn("287 databases covering 109 species/taxa", html)

        card_ids = [
            'id="about-dbc"',
            'id="classification-standard"',
            'id="feedback"',
        ]
        card_positions = [html.index(card_id) for card_id in card_ids]
        self.assertEqual(card_positions, sorted(card_positions))
        self.assertIn("Feedback and Contribute", html)

        nav_labels = [
            ">Home</a>",
            ">Search</a>",
            ">Help &amp; Feedback</a>",
            ">SpliceLab</a>",
        ]
        nav_positions = [html.index(label) for label in nav_labels]
        self.assertEqual(nav_positions, sorted(nav_positions))
        self.assertNotIn('href="/contribute"', html)

        self.assertIn("docs.google.com/forms/", html)
        self.assertIn('href="mailto:kif.liakath-ali@soton.ac.uk"', html)

    def test_help_page_contains_complete_classification_snapshot(self):
        html = self.client.get("/about").get_data(as_text=True)

        self.assertEqual(html.count('class="classification-group"'), 4)
        self.assertEqual(html.count('class="classification-subcategory"'), 11)
        self.assertIn("four major classes and 11 subcategories", html)
        example_links = re.findall(
            r'<a class="classification-example" href="[^"]+" '
            r'target="_blank" rel="noopener noreferrer">',
            html,
        )
        self.assertEqual(len(example_links), 33)
        self.assertIn(">Ensembl</a>", html)
        self.assertIn(">AceView</a>", html)
        self.assertIn(">APPRIS</a>", html)
        self.assertIn(">CancerSplicingQTL</a>", html)
        self.assertIn(">ValidSpliceMut</a>", html)
        self.assertIn(">ExonSkipAD</a>", html)
        self.assertIn(">CIRCpedia v3</a>", html)
        self.assertIn(">FL-circAS</a>", html)
        self.assertIn(">ChiTaRS 2.1</a>", html)
        self.assertIn(">LncBook 2.0</a>", html)
        self.assertIn(">SpliceAPP Branch Point Query</a>", html)
        self.assertIn(">ExoPLOT</a>", html)
        self.assertIn(">NMD AS database</a>", html)
        self.assertNotIn(">ExonSkipDB</a>", html)
        self.assertNotIn(">BrainSeq eQTL Phase 1</a>", html)
        self.assertNotIn(">circASbase</a>", html)
        self.assertNotIn(">ChiTaRS 8.0</a>", html)
        self.assertNotIn(">Cattle BodyMap Transcriptome Database</a>", html)
        self.assertNotIn(">TranspoGene</a>", html)
        self.assertNotIn(">Database for Bacterial Group II Introns</a>", html)
        self.assertNotIn('classification-iv-6', html)
        self.assertNotIn(">circAtlas 3.0</a>", html)
        self.assertNotIn(">circBank</a>", html)
        self.assertNotIn(">FANTOM CAT</a>", html)
        self.assertNotIn(">DirectRMDB</a>", html)

    def test_contribute_redirects_to_feedback_card(self):
        response = self.client.get("/contribute", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/about#feedback"))


if __name__ == "__main__":
    unittest.main()
