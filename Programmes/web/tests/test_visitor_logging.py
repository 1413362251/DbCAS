import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


WEB_DIR = Path(__file__).resolve().parents[1]
if str(WEB_DIR) not in sys.path:
    sys.path.insert(0, str(WEB_DIR))

import app as web_app  # noqa: E402
from flask import Flask  # noqa: E402
from visitor_logging import init_visitor_logging, request_ip_details  # noqa: E402


class RequestIPTests(unittest.TestCase):
    def test_direct_connection_ignores_spoofed_forwarding_headers(self):
        result = request_ip_details({
            "REMOTE_ADDR": "198.51.100.20",
            "HTTP_X_FORWARDED_FOR": "192.0.2.99",
            "HTTP_CF_CONNECTING_IP": "192.0.2.99",
        }, 0)
        self.assertEqual(result["client_ip"], "198.51.100.20")
        self.assertEqual(result["ip_source"], "remote_addr")

    def test_configured_proxy_chain_ignores_spoofed_leftmost_ip(self):
        result = request_ip_details({
            "REMOTE_ADDR": "10.0.0.2",
            "HTTP_X_FORWARDED_FOR": "192.0.2.99, 2001:db8::20, 198.51.100.8",
        }, 2)
        self.assertEqual(result["client_ip"], "2001:db8::20")
        self.assertEqual(result["ip_version"], 6)
        self.assertEqual(result["ip_source"], "x_forwarded_for")

    def test_unconfigured_proxy_keeps_evidence_without_guessing_client(self):
        result = request_ip_details({
            "REMOTE_ADDR": "10.0.0.2",
            "HTTP_X_FORWARDED_FOR": "2001:db8::20, 198.51.100.8",
        }, None)
        self.assertIsNone(result["client_ip"])
        self.assertEqual(result["ip_status"], "proxy_not_configured")
        self.assertEqual(result["reported_forwarded_for"], ["2001:db8::20", "198.51.100.8"])

    def test_invalid_or_short_chains_never_fall_back_to_proxy_address(self):
        for chain, hops in [("", 1), ("192.0.2.20", 2),
                            ("192.0.2.20, invalid", 1),
                            ("192.0.2.20, fe80::1%eth0", 1),
                            ("192.0.2.20," * 33, 1)]:
            with self.subTest(chain=chain, hops=hops):
                result = request_ip_details({
                    "REMOTE_ADDR": "10.0.0.2", "HTTP_X_FORWARDED_FOR": chain,
                }, hops)
                self.assertIsNone(result["client_ip"])
                self.assertIn(result["ip_status"], {"invalid_ip", "invalid_proxy_chain"})

    def test_mapped_ipv4_and_original_ipv6_are_preserved(self):
        result = request_ip_details({
            "REMOTE_ADDR": "::ffff:192.0.2.20",
            "HTTP_CF_CONNECTING_IPV6": "2001:0db8:0000::20",
        }, 0)
        self.assertEqual(result["client_ip"], "192.0.2.20")
        self.assertEqual(result["ip_version"], 4)
        self.assertEqual(result["reported_cf_ipv6"], "2001:db8::20")

    def test_invalid_proxy_configuration_fails_explicitly(self):
        for value in ("-1", "33", "two"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"VISITOR_LOG_PROXY_HOPS": value}):
                    with self.assertRaises(ValueError):
                        init_visitor_logging(Flask(__name__))


class VisitorLoggingTests(unittest.TestCase):
    def setUp(self):
        self.config = patch.dict(web_app.app.config, {
            "TESTING": True, "VISITOR_LOG_ENABLED": True, "VISITOR_LOG_PROXY_HOPS": 1,
        })
        self.config.start()
        self.addCleanup(self.config.stop)
        self.client = web_app.app.test_client()

    def test_pages_log_without_javascript_and_do_not_expose_ips(self):
        headers = {"X-Forwarded-For": "2001:db8::20", "Rndr-Id": "test-request-id"}
        with self.assertLogs("dbcas.visitors", level="INFO") as logs:
            home = self.client.get("/?private-search=secret", headers=headers)
            self.client.get("/about", headers=headers)
        records = [json.loads(row.getMessage()) for row in logs.records]
        self.assertEqual([row["route"] for row in records], ["/", "/about"])
        self.assertTrue(all(row["page_response"] for row in records))
        self.assertEqual(records[0]["client_ip"], "2001:db8::20")
        self.assertEqual(records[0]["request_id"], "test-request-id")
        self.assertNotIn("secret", json.dumps(records))
        self.assertNotIn("2001:db8::20", home.get_data(as_text=True))
        self.assertNotIn("2001:db8::20", str(home.headers))

    def test_static_assets_and_disabled_logging_produce_no_visitor_logs(self):
        with self.assertNoLogs("dbcas.visitors"):
            self.client.get("/static/js/main.js").close()
            web_app.app.config["VISITOR_LOG_ENABLED"] = False
            self.client.get("/")

    def test_errors_head_and_redirects_are_not_counted_as_page_responses(self):
        with self.assertLogs("dbcas.visitors", level="INFO") as logs:
            self.client.get("/not-a-route/private-token?token=secret")
            self.client.head("/")
            self.client.get("/contribute")
        records = [json.loads(row.getMessage()) for row in logs.records]
        self.assertEqual([row["status"] for row in records], [404, 200, 302])
        self.assertTrue(all(not row["page_response"] for row in records))
        self.assertNotIn("private-token", json.dumps(records))
        self.assertNotIn("secret", json.dumps(records))


if __name__ == "__main__":
    unittest.main()
