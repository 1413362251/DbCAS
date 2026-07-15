import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import requests


PROGRAMMES_DIR = Path(__file__).resolve().parents[2]
if str(PROGRAMMES_DIR) not in sys.path:
    sys.path.insert(0, str(PROGRAMMES_DIR))

from scripts.check_url_accessibility import (  # noqa: E402
    build_url_candidates,
    check_url,
    check_urls,
)


class FakeResponse:
    def __init__(self, status_code=200, url="https://example.org/db", text="", history=()):
        self.status_code = status_code
        self.url = url
        self.text = text
        self.history = history


class FakeSession:
    def __init__(self, head=None, gets=None):
        self.head_result = head or FakeResponse()
        self.get_results = list(gets or [FakeResponse()])
        self.last_get_result = self.get_results[-1]
        self.calls = []

    def head(self, url, **kwargs):
        self.calls.append(("HEAD", url, kwargs))
        if isinstance(self.head_result, Exception):
            raise self.head_result
        return self.head_result

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        result = self.get_results.pop(0) if self.get_results else self.last_get_result
        if isinstance(result, Exception):
            raise result
        return result


class UrlAccessibilityTests(unittest.TestCase):
    def test_missing_url(self):
        result = check_url(None)
        self.assertEqual(result.status, "missing")
        self.assertIsNone(result.accessible)

    def test_continue_page_is_not_dead(self):
        session = FakeSession(
            gets=[FakeResponse(text='<a href="/db">Continue</a>')]
        )
        result = check_url("https://example.org", session=session)
        self.assertEqual(result.status, "continue_required")
        self.assertTrue(result.accessible)

    def test_restricted_status_and_captcha_are_not_dead(self):
        restricted = check_url(
            "https://example.org", session=FakeSession(gets=[FakeResponse(403)])
        )
        captcha = check_url(
            "https://example.org",
            session=FakeSession(gets=[FakeResponse(text="Verify you are human")]),
        )
        parked = check_url(
            "https://example.org",
            session=FakeSession(gets=[FakeResponse(text="Buy this domain")]),
        )
        self.assertEqual(restricted.status, "restricted")
        self.assertEqual(captcha.status, "restricted")
        self.assertEqual(parked.status, "restricted")

    def test_redirect_diagnostics_are_recorded(self):
        first = FakeResponse(301, "https://old.example.org")
        final = FakeResponse(200, "https://new.example.org/db", history=[first])
        result = check_url(
            "http://old.example.org", session=FakeSession(head=final, gets=[final])
        )
        self.assertEqual(result.status, "reachable")
        self.assertEqual(result.final_url, "https://new.example.org/db")
        self.assertEqual(
            result.redirect_chain,
            ("https://old.example.org", "https://new.example.org/db"),
        )

    def test_ssl_error_retries_get_without_verification(self):
        ssl_error = requests.exceptions.SSLError("certificate failed")
        session = FakeSession(head=ssl_error, gets=[ssl_error, FakeResponse()])
        result = check_url("https://example.org", session=session)
        self.assertEqual(result.status, "reachable")
        self.assertTrue(result.tls_warning)
        get_calls = [call for call in session.calls if call[0] == "GET"]
        self.assertEqual([call[2]["verify"] for call in get_calls], [True, False])

    def test_http_https_and_www_candidates(self):
        self.assertEqual(
            build_url_candidates("https://example.org/path?q=1"),
            [
                "https://example.org/path?q=1",
                "https://www.example.org/path?q=1",
                "http://example.org/path?q=1",
                "http://www.example.org/path?q=1",
            ],
        )

    def test_404_on_all_variants_is_unreachable(self):
        result = check_url(
            "https://example.org/missing",
            session=FakeSession(head=FakeResponse(404), gets=[FakeResponse(404)]),
        )
        self.assertEqual(result.status, "unreachable")
        self.assertFalse(result.accessible)
        self.assertEqual(result.error_category, "http_error")

    def test_request_timeout_is_diagnostic(self):
        timeout = requests.exceptions.Timeout("too slow")
        result = check_url(
            "https://example.org", session=FakeSession(head=timeout, gets=[timeout])
        )
        self.assertEqual(result.status, "unreachable")
        self.assertEqual(result.error_category, "timeout")

    def test_total_budget_expiry_is_returned_not_raised(self):
        with patch(
            "scripts.check_url_accessibility._request_timeout",
            side_effect=TimeoutError("total budget exhausted"),
        ):
            result = check_url("https://example.org", session=FakeSession())
        self.assertEqual(result.status, "unreachable")
        self.assertEqual(result.error_category, "timeout")

    def test_batch_deduplicates_normalized_urls_and_preserves_rows(self):
        sessions = []

        def factory():
            session = FakeSession()
            sessions.append(session)
            return session

        rows = check_urls(
            ["https://example.org/db/", "http://www.example.org/db", None],
            workers=32,
            timeout=120,
            session_factory=factory,
        )
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(sessions), 2)
        self.assertEqual(rows[0]["status"], "reachable")
        self.assertEqual(rows[1]["original_url"], "http://www.example.org/db")
        self.assertEqual(rows[2]["status"], "missing")

    def test_batch_records_unexpected_single_url_error(self):
        with patch(
            "scripts.check_url_accessibility.check_url",
            side_effect=ValueError("malformed URL edge case"),
        ):
            rows = check_urls(["https://example.org"], workers=1)
        self.assertEqual(rows[0]["status"], "unreachable")
        self.assertEqual(rows[0]["error_category"], "ValueError")

    def test_request_timeouts_do_not_exceed_total_budget(self):
        session = FakeSession()
        check_url("https://example.org", timeout=120, session=session)
        timeouts = [call[2]["timeout"] for call in session.calls]
        self.assertTrue(all(0 < value <= 120 for value in timeouts))
        self.assertLessEqual(timeouts[0], 30)
        self.assertLessEqual(timeouts[1], 60)

    def test_request_gate_runs_before_each_head_and_get(self):
        session = FakeSession()
        gated = []
        result = check_url(
            "https://example.org",
            session=session,
            request_gate=gated.append,
        )
        self.assertEqual(result.status, "reachable")
        self.assertEqual(gated, ["https://example.org", "https://example.org"])

    def test_batch_passes_shared_request_gate(self):
        gated = []
        rows = check_urls(
            ["https://example.org"],
            workers=1,
            session_factory=FakeSession,
            request_gate=gated.append,
        )
        self.assertEqual(rows[0]["status"], "reachable")
        self.assertEqual(gated, ["https://example.org", "https://example.org"])


if __name__ == "__main__":
    unittest.main()
