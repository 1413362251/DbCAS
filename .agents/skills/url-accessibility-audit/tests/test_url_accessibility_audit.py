import importlib.util
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "url_accessibility_audit.py"
SPEC = importlib.util.spec_from_file_location("url_accessibility_audit_skill", SCRIPT)
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)

from Programmes.scripts.check_url_accessibility import check_url  # noqa: E402


def write_jsonl(path, rows):
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )


def auto_result(status="reachable", url="https://example.org/db", **overrides):
    value = {
        "status": status,
        "accessible": status in {"reachable", "restricted", "continue_required"},
        "checked_url": url,
        "final_url": url,
        "http_status": 200 if status == "reachable" else 403,
        "redirect_chain": [url],
        "elapsed_seconds": 0.1,
        "error_category": "",
        "error_message": "",
        "tls_warning": False,
        "checked_date": "2026-07-12",
    }
    value.update(overrides)
    return value


class LocalHandler(BaseHTTPRequestHandler):
    def _respond(self, include_body=True):
        path = self.path
        if path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/reachable")
            self.end_headers()
            return
        status = 200
        body = b"database home"
        if path == "/403":
            status, body = 403, b"forbidden"
        elif path == "/429":
            status, body = 429, b"too many requests"
        elif path == "/continue":
            body = b'<a href="/reachable">Continue</a>'
        elif path == "/captcha":
            body = b"Verify you are human captcha"
        elif path == "/parked":
            body = b"Buy this domain"
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def do_HEAD(self):
        self._respond(include_body=False)

    def do_GET(self):
        self._respond(include_body=True)

    def log_message(self, *_args):
        return


class UrlAccessibilityAuditSkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_plain_and_tagged_column_detection(self):
        self.assertEqual(
            audit.detect_column(["id", "database_url"], "id"), "id"
        )
        self.assertEqual(
            audit.detect_column(
                ["<main,t-word-id> id", "<main,t-word-url> database_url"],
                "database_url",
            ),
            "<main,t-word-url> database_url",
        )
        self.assertEqual(
            audit.detect_column(["id", "database_url"], "accessibility"), ""
        )

    def test_ambiguous_missing_blank_and_duplicate_ids_are_rejected(self):
        with self.assertRaises(audit.AuditError):
            audit.detect_column(["id", "database_id"], "id")
        with self.assertRaises(audit.AuditError):
            audit.detect_column(["id"], "database_url")
        blank = self.root / "blank.jsonl"
        write_jsonl(blank, [{"id": "", "database_url": "https://x.org"}])
        with self.assertRaises(audit.AuditError):
            audit.prepare_input_rows(blank)
        duplicate = self.root / "duplicate.jsonl"
        write_jsonl(
            duplicate,
            [
                {"id": "1", "database_url": "https://x.org"},
                {"id": "1", "database_url": "https://y.org"},
            ],
        )
        with self.assertRaises(audit.AuditError):
            audit.prepare_input_rows(duplicate)

    def test_first_run_broad_risk_queue(self):
        clean = audit.audit_from_auto(
            {"id": "1", "input_database_url": "https://a.org", "input_accessibility": ""},
            auto_result(url="https://a.org"),
        )
        audit.classify_comparison(clean, None)
        self.assertFalse(clean["agent_review_required"])
        self.assertEqual(clean["final_accessibility"], "live")

        for index, result in enumerate(
            [
                auto_result("restricted"),
                auto_result("continue_required"),
                auto_result("unreachable"),
                auto_result("missing", url="", final_url="", http_status=None),
                auto_result(tls_warning=True),
                auto_result(final_url="https://other.org/db"),
            ],
            start=2,
        ):
            row = audit.audit_from_auto(
                {
                    "id": str(index),
                    "input_database_url": "https://example.org/db",
                    "input_accessibility": "dead",
                },
                result,
            )
            audit.classify_comparison(row, None)
            self.assertTrue(row["agent_review_required"])
            self.assertEqual(row["final_accessibility"], "unresolved")

    def test_fingerprint_ignores_elapsed_time_and_error_wording(self):
        base = audit.audit_from_auto(
            {"id": "1", "input_database_url": "https://a.org", "input_accessibility": ""},
            auto_result("unreachable", elapsed_seconds=1, error_message="first"),
        )
        changed_text = dict(base)
        changed_text["auto_elapsed_seconds"] = 99
        changed_text["auto_error_message"] = "different"
        self.assertEqual(
            audit.risk_fingerprint(base), audit.risk_fingerprint(changed_text)
        )
        changed_text["auto_http_status_class"] = "5xx"
        self.assertNotEqual(
            audit.risk_fingerprint(base), audit.risk_fingerprint(changed_text)
        )

    def test_incremental_unchanged_reuses_agent_and_changed_queues(self):
        current = audit.audit_from_auto(
            {"id": "1", "input_database_url": "https://a.org", "input_accessibility": "dead"},
            auto_result("restricted", url="https://a.org"),
        )
        previous = dict(current)
        previous["agent_final_accessibility"] = "live"
        previous["final_accessibility"] = "live"
        audit.classify_comparison(current, previous)
        self.assertEqual(current["comparison_status"], "unchanged")
        self.assertFalse(current["agent_review_required"])
        self.assertEqual(current["final_accessibility"], "live")

        changed = dict(current)
        changed["auto_http_status_class"] = "5xx"
        changed["risk_fingerprint"] = audit.risk_fingerprint(changed)
        audit.classify_comparison(changed, previous)
        self.assertEqual(changed["comparison_status"], "changed")
        self.assertTrue(changed["agent_review_required"])

    def test_new_url_changed_and_missing_current_are_queued(self):
        input_path = self.root / "input.jsonl"
        previous_path = self.root / "previous.jsonl"
        output = self.root / "audit.jsonl"
        review = self.root / "review.jsonl"
        write_jsonl(
            input_path,
            [
                {"id": "1", "database_url": "https://new.org", "accessibility": "live"},
                {"id": "2", "database_url": "https://two.org", "accessibility": ""},
            ],
        )
        previous_one = audit.audit_from_auto(
            {"id": "1", "input_database_url": "https://old.org", "input_accessibility": "live"},
            auto_result(url="https://old.org"),
        )
        audit.classify_comparison(previous_one, None)
        previous_three = audit.audit_from_auto(
            {"id": "3", "input_database_url": "https://three.org", "input_accessibility": "dead"},
            auto_result("restricted", url="https://three.org"),
        )
        previous_three["agent_final_accessibility"] = "dead"
        previous_three["final_accessibility"] = "dead"
        write_jsonl(previous_path, [previous_one, previous_three])
        args = SimpleNamespace(
            input=input_path,
            id_column="",
            url_column="",
            accessibility_column="",
            previous_audit=previous_path,
            per_host_rps=1,
            workers=32,
            timeout=120,
            output=output,
            review_output=review,
        )
        with patch.object(
            audit,
            "check_urls",
            return_value=[auto_result(url="https://new.org"), auto_result(url="https://two.org")],
        ) as mocked:
            rows = audit.run_auto_check(args)
        self.assertEqual(mocked.call_args.kwargs["workers"], 32)
        self.assertEqual(mocked.call_args.kwargs["timeout"], 120)
        self.assertEqual(
            [row["comparison_status"] for row in rows],
            ["url_changed", "new_id", "missing_current"],
        )
        self.assertTrue(all(row["agent_review_required"] for row in rows))

    def test_agent_schema_controlled_values_dates_urls_and_length(self):
        valid = {
            "id": "1",
            "agent_visit_status": "database_opened",
            "agent_checked_url": "https://example.org",
            "agent_final_url": "https://example.org/db",
            "agent_statement": "The intended database page opened and showed its current entry point.",
            "agent_checked_date": "2026-07-12",
            "agent_model": "gpt-5.6-terra",
            "agent_final_accessibility": "live",
        }
        self.assertEqual(audit.validate_agent_result(valid), [])
        invalid = dict(valid)
        invalid.update(
            {
                "agent_visit_status": "clicked",
                "agent_checked_url": "not-a-url",
                "agent_statement": "short",
                "agent_checked_date": "2026-99-99",
                "agent_model": "",
                "agent_final_accessibility": "maybe",
            }
        )
        self.assertGreaterEqual(len(audit.validate_agent_result(invalid)), 5)

    def test_agent_unresolved_preserves_old_accessibility(self):
        auto_path = self.root / "auto.jsonl"
        agent_path = self.root / "agent.jsonl"
        output = self.root / "final.jsonl"
        updates = self.root / "updates.jsonl"
        row = audit.audit_from_auto(
            {"id": "1", "input_database_url": "https://a.org", "input_accessibility": "dead"},
            auto_result("restricted", url="https://a.org"),
        )
        audit.classify_comparison(row, None)
        write_jsonl(auto_path, [row])
        write_jsonl(
            agent_path,
            [
                {
                    "id": "1",
                    "agent_visit_status": "unresolved",
                    "agent_checked_url": "https://a.org",
                    "agent_final_url": "",
                    "agent_click_path": "",
                    "agent_statement": "The site remained inconclusive after a restricted access response.",
                    "agent_checked_date": "2026-07-12",
                    "agent_model": "gpt-5.6-terra",
                    "agent_final_accessibility": "unresolved",
                }
            ],
        )
        rows = audit.finalize(
            SimpleNamespace(
                auto_audit=auto_path,
                agent_results=agent_path,
                output=output,
                updates_output=updates,
            )
        )
        self.assertEqual(rows[0]["final_accessibility"], "dead")
        manifest = audit.read_records(updates)
        self.assertFalse(manifest[0]["apply_update"])
        self.assertFalse(manifest[0]["changed"])

    def test_rate_limiter_uses_shared_file_and_monotonic_delay(self):
        state = self.root / "locks"
        limiter = audit.HostFileRateLimiter(2, state_dir=state)
        with patch.object(audit.time, "monotonic", side_effect=[10.0, 10.0, 10.1, 10.5]), patch.object(
            audit.time, "sleep"
        ) as sleep:
            limiter("https://example.org/a")
            limiter("https://example.org/b")
        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args.args[0], 0.4, places=6)
        self.assertEqual(len(list(state.glob("*.lock"))), 1)

    def test_local_http_service_covers_common_interstitials(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), LocalHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        root = f"http://127.0.0.1:{server.server_port}"
        try:
            expected = {
                "/reachable": "reachable",
                "/redirect": "reachable",
                "/403": "restricted",
                "/429": "restricted",
                "/continue": "continue_required",
                "/captcha": "restricted",
                "/parked": "restricted",
            }
            for path, status in expected.items():
                with self.subTest(path=path):
                    self.assertEqual(check_url(root + path, timeout=5).status, status)
        finally:
            server.shutdown()
            server.server_close()

    def test_skill_documents_fixed_xlsx_outputs_and_dependencies(self):
        text = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("<input_stem>_url_accessibility_audit.xlsx", text)
        self.assertIn("<input_stem>_accessibility_updated.xlsx", text)
        self.assertIn("spreadsheets:Spreadsheets", text)
        self.assertIn("browser:control-in-app-browser", text)
        self.assertIn("--workers 32 --timeout 120 --per-host-rps 1", text)


if __name__ == "__main__":
    unittest.main()
