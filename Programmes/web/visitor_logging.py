"""Private request diagnostics, independent of the browser's visitor map."""

from datetime import datetime, timezone
from ipaddress import ip_address
import json
import logging
import os
import sys

from flask import request


LOGGER = logging.getLogger("dbcas.visitors")


def normalize_ip(value):
    if not value or "%" in value:
        return None
    try:
        address = ip_address(value.strip())
    except ValueError:
        return None
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    return str(address)


def request_ip_details(environ, proxy_hops):
    """Select from the right of XFF only after the proxy chain is configured."""
    peer = normalize_ip(environ.get("REMOTE_ADDR"))
    forwarded = environ.get("HTTP_X_FORWARDED_FOR", "")
    # Keep invalid entries in place: dropping one could change the trusted hop.
    oversized = len(forwarded) > 2048 or forwarded.count(",") >= 32
    chain = [] if oversized or not forwarded else [
        normalize_ip(part) for part in forwarded.split(",")
    ]
    details = {
        "client_ip": None,
        "ip_version": None,
        "ip_source": None,
        "ip_status": "proxy_not_configured",
        "peer_ip": peer,
        "reported_forwarded_for": chain,
        "reported_cf_ip": normalize_ip(environ.get("HTTP_CF_CONNECTING_IP")),
        "reported_cf_ipv6": normalize_ip(environ.get("HTTP_CF_CONNECTING_IPV6")),
    }
    # Reported headers are evidence, not authenticated client addresses.
    if proxy_hops is None:
        return details
    if proxy_hops == 0:
        candidate, source = peer, "remote_addr"
    elif oversized or len(chain) < proxy_hops:
        details["ip_status"] = "invalid_proxy_chain"
        return details
    else:
        candidate, source = chain[-proxy_hops], "x_forwarded_for"
    if not candidate:
        details["ip_status"] = "invalid_ip"
        return details
    details.update(
        client_ip=candidate,
        ip_version=ip_address(candidate).version,
        ip_source=source,
        ip_status="resolved",
    )
    return details


def init_visitor_logging(app):
    enabled = os.environ.get("VISITOR_LOG_ENABLED", os.environ.get("RENDER", "false"))
    hops = os.environ.get("VISITOR_LOG_PROXY_HOPS")
    hops = None if hops is None else int(hops)
    if hops is not None and not 0 <= hops <= 32:
        raise ValueError("VISITOR_LOG_PROXY_HOPS must be between 0 and 32")
    app.config.setdefault("VISITOR_LOG_ENABLED", enabled.lower() in {"1", "true", "yes"})
    app.config.setdefault("VISITOR_LOG_PROXY_HOPS", hops)

    if not LOGGER.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False

    @app.after_request
    def log_visit(response):
        if not app.config["VISITOR_LOG_ENABLED"] or request.endpoint == "static":
            return response
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": "info",
            "event": "dbcas.visit",
            "request_id": request.headers.get("Rndr-Id", "")[:128] or None,
            "method": request.method,
            # Route patterns avoid saving search terms or arbitrary sensitive URLs.
            "route": request.url_rule.rule if request.url_rule else "<unmatched>",
            "status": response.status_code,
            "page_response": (
                request.method == "GET"
                and 200 <= response.status_code < 300
                and response.mimetype == "text/html"
            ),
            **request_ip_details(request.environ, app.config["VISITOR_LOG_PROXY_HOPS"]),
        }
        LOGGER.info(json.dumps(record, ensure_ascii=True, separators=(",", ":")))
        return response
