"""Reusable URL accessibility checks with bounded concurrent execution."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlsplit, urlunsplit

import requests
import urllib3


DEFAULT_WORKERS = 32
DEFAULT_TIMEOUT = 120.0
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
RESTRICTED_STATUS_CODES = {401, 403, 429}
CONTINUE_PATTERNS = (
    r">\s*(?:continue(?:\s+(?:anyway|to(?:\s+the)?\s+site))?|proceed(?:\s+anyway)?|enter(?:\s+the)?\s+site|i\s+understand)\s*<",
    r">\s*(?:继续访问|继续前往|进入网站|我已了解)\s*<",
)
RESTRICTED_PATTERNS = (
    r"captcha",
    r"verify\s+(?:that\s+)?you\s+are\s+(?:a\s+)?human",
    r"checking\s+your\s+browser",
    r"attention\s+required[^<]{0,80}cloudflare",
    r"buy\s+(?:this|the)\s+domain",
    r"domain\s+(?:name\s+)?(?:is\s+)?(?:for\s+sale|parked)",
    r"(?:sedo|afternic|hugedomains)\s+(?:domain\s+)?(?:parking|marketplace)",
)


@dataclass(frozen=True)
class AccessibilityResult:
    original_url: str
    status: str
    checked_url: str = ""
    final_url: str = ""
    http_status: int | None = None
    redirect_chain: tuple[str, ...] = ()
    elapsed_seconds: float = 0.0
    error_category: str = ""
    error_message: str = ""
    tls_warning: bool = False
    checked_date: str = ""

    @property
    def accessible(self) -> bool | None:
        if self.status == "missing":
            return None
        return self.status in {"reachable", "restricted", "continue_required"}

    def to_dict(self) -> dict:
        data = asdict(self)
        data["redirect_chain"] = list(self.redirect_chain)
        data["accessible"] = self.accessible
        return data


def _clean_url(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "<na>", "none"} else text


def normalize_url_key(value: object) -> str:
    """Return a conservative key used only to avoid duplicate network checks."""
    text = _clean_url(value)
    if not text:
        return ""
    candidate = text if "://" in text else f"https://{text}"
    try:
        parts = urlsplit(candidate)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return text.lower()
    if port and not ((parts.scheme == "http" and port == 80) or (parts.scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    if host.startswith("www."):
        host = host[4:]
    return urlunsplit(("", host, path, parts.query, ""))


def build_url_candidates(value: object) -> list[str]:
    """Build HTTP/HTTPS and www/non-www variants without changing path/query."""
    text = _clean_url(value)
    if not text:
        return []
    base = text if "://" in text else f"https://{text}"
    try:
        parts = urlsplit(base)
        _ = parts.port
    except ValueError:
        return []
    host = parts.netloc
    if not host:
        return []
    schemes = [parts.scheme.lower()]
    alternate = "http" if schemes[0] == "https" else "https"
    if alternate not in schemes:
        schemes.append(alternate)
    hosts = [host]
    if host.lower().startswith("www."):
        hosts.append(host[4:])
    else:
        hosts.append(f"www.{host}")
    candidates = []
    for scheme in schemes:
        for candidate_host in hosts:
            candidate = urlunsplit((scheme, candidate_host, parts.path, parts.query, ""))
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


def _redirect_chain(response) -> tuple[str, ...]:
    urls = [str(item.url) for item in getattr(response, "history", ()) if getattr(item, "url", None)]
    final_url = str(getattr(response, "url", "") or "")
    if final_url and (not urls or urls[-1] != final_url):
        urls.append(final_url)
    return tuple(urls)


def _page_status(status_code: int, body: str) -> str:
    if status_code in RESTRICTED_STATUS_CODES:
        return "restricted"
    lowered = body.lower()
    if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in CONTINUE_PATTERNS):
        return "continue_required"
    if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in RESTRICTED_PATTERNS):
        return "restricted"
    return "reachable" if status_code < 400 else "unreachable"


def _response_body(response, deadline: float, limit: int = 262144) -> str:
    """Read enough HTML for interstitial detection without unbounded downloads."""
    if not hasattr(response, "iter_content"):
        return str(getattr(response, "text", "") or "")[:limit]
    chunks = bytearray()
    for chunk in response.iter_content(chunk_size=16384):
        if chunk:
            chunks.extend(chunk[: limit - len(chunks)])
        if len(chunks) >= limit or time.monotonic() >= deadline:
            break
    encoding = getattr(response, "encoding", None) or "utf-8"
    return bytes(chunks).decode(encoding, errors="replace")


def _request_timeout(deadline: float, cap: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("URL check exceeded its total time budget")
    return max(0.001, min(cap, remaining))


def check_url(
    url: object,
    timeout: float = DEFAULT_TIMEOUT,
    session=None,
    request_gate: Callable[[str], None] | None = None,
) -> AccessibilityResult:
    """Check one URL within a total time budget and return diagnostics."""
    original = _clean_url(url)
    checked_date = date.today().isoformat()
    if not original:
        return AccessibilityResult(original_url="", status="missing", checked_date=checked_date)
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")

    client = session or requests.Session()
    started = time.monotonic()
    deadline = started + timeout
    headers = {"User-Agent": USER_AGENT}
    last_status = None
    last_url = ""
    last_final = ""
    last_chain: tuple[str, ...] = ()
    last_error_category = ""
    last_error_message = ""
    tls_warning = False

    for candidate in build_url_candidates(original):
        if time.monotonic() >= deadline:
            last_error_category = "timeout"
            last_error_message = "URL check exceeded its total time budget"
            break
        last_url = candidate
        head_response = None
        try:
            if request_gate:
                request_gate(candidate)
            head_response = client.head(
                candidate,
                allow_redirects=True,
                timeout=_request_timeout(deadline, 30.0),
                headers=headers,
            )
            last_status = int(head_response.status_code)
            last_final = str(getattr(head_response, "url", "") or candidate)
            last_chain = _redirect_chain(head_response)
        except requests.exceptions.SSLError as exc:
            tls_warning = True
            last_error_category = "ssl_error"
            last_error_message = str(exc)
        except requests.exceptions.Timeout as exc:
            last_error_category = "timeout"
            last_error_message = str(exc)
        except TimeoutError as exc:
            last_error_category = "timeout"
            last_error_message = str(exc)
            break
        except requests.RequestException as exc:
            last_error_category = type(exc).__name__
            last_error_message = str(exc)

        for verify in ((True, False) if tls_warning else (True,)):
            try:
                if not verify:
                    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                if request_gate:
                    request_gate(candidate)
                response = client.get(
                    candidate,
                    allow_redirects=True,
                    timeout=_request_timeout(deadline, 60.0),
                    headers=headers,
                    verify=verify,
                    stream=True,
                )
                last_status = int(response.status_code)
                last_final = str(getattr(response, "url", "") or candidate)
                last_chain = _redirect_chain(response)
                body = _response_body(response, deadline)
                status = _page_status(last_status, body)
                if status != "unreachable":
                    return AccessibilityResult(
                        original_url=original,
                        status=status,
                        checked_url=candidate,
                        final_url=last_final,
                        http_status=last_status,
                        redirect_chain=last_chain,
                        elapsed_seconds=round(time.monotonic() - started, 3),
                        tls_warning=tls_warning,
                        checked_date=checked_date,
                    )
            except requests.exceptions.SSLError as exc:
                tls_warning = True
                last_error_category = "ssl_error"
                last_error_message = str(exc)
                if not verify:
                    break
                continue
            except requests.exceptions.Timeout as exc:
                last_error_category = "timeout"
                last_error_message = str(exc)
                break
            except TimeoutError as exc:
                last_error_category = "timeout"
                last_error_message = str(exc)
                break
            except requests.RequestException as exc:
                last_error_category = type(exc).__name__
                last_error_message = str(exc)
                break

        if head_response is not None and int(head_response.status_code) in RESTRICTED_STATUS_CODES:
            return AccessibilityResult(
                original_url=original,
                status="restricted",
                checked_url=candidate,
                final_url=last_final,
                http_status=last_status,
                redirect_chain=last_chain,
                elapsed_seconds=round(time.monotonic() - started, 3),
                tls_warning=tls_warning,
                checked_date=checked_date,
            )

    return AccessibilityResult(
        original_url=original,
        status="unreachable",
        checked_url=last_url,
        final_url=last_final,
        http_status=last_status,
        redirect_chain=last_chain,
        elapsed_seconds=round(time.monotonic() - started, 3),
        error_category=last_error_category or ("http_error" if last_status else "request_error"),
        error_message=last_error_message,
        tls_warning=tls_warning,
        checked_date=checked_date,
    )


def check_urls(
    urls: Iterable[object],
    workers: int = DEFAULT_WORKERS,
    timeout: float = DEFAULT_TIMEOUT,
    session_factory: Callable[[], object] | None = None,
    request_gate: Callable[[str], None] | None = None,
) -> list[dict]:
    """Check URLs concurrently, issuing only one check per normalized URL."""
    values = list(urls)
    if workers <= 0:
        raise ValueError("workers must be greater than zero")
    groups: dict[str, list[int]] = {}
    for index, value in enumerate(values):
        key = normalize_url_key(value)
        groups.setdefault(key, []).append(index)

    results: list[dict | None] = [None] * len(values)

    def run(key: str) -> AccessibilityResult:
        first_value = values[groups[key][0]]
        session = session_factory() if session_factory else None
        try:
            return check_url(
                first_value,
                timeout=timeout,
                session=session,
                request_gate=request_gate,
            )
        except Exception as exc:  # Preserve the batch audit for malformed/edge-case URLs.
            return AccessibilityResult(
                original_url=_clean_url(first_value),
                status="unreachable",
                error_category=type(exc).__name__,
                error_message=str(exc),
                checked_date=date.today().isoformat(),
            )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(run, key): key for key in groups}
        for future in as_completed(futures):
            key = futures[future]
            result = future.result()
            for index in groups[key]:
                item = result.to_dict()
                item["original_url"] = _clean_url(values[index])
                results[index] = item
    return [item for item in results if item is not None]


def check_url_accessible(url: object, timeout: float = DEFAULT_TIMEOUT) -> bool | None:
    """Compatibility adapter for callers that only need true/false/missing."""
    return check_url(url, timeout=timeout).accessible


def _read_cli_urls(path: Path, column: str) -> list[str]:
    if path.suffix.lower() in {".json", ".jsonl"}:
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [str(record.get(column, "")) if isinstance(record, dict) else str(record) for record in records]
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [row.get(column, "") for row in csv.DictReader(handle)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls", nargs="*", help="URLs to check")
    parser.add_argument("--input", type=Path, help="CSV or JSONL input file")
    parser.add_argument("--column", default="database_url", help="Input URL column")
    parser.add_argument("--output", type=Path, help="Write JSONL instead of stdout")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = parser.parse_args(argv)
    urls = list(args.urls)
    if args.input:
        urls.extend(_read_cli_urls(args.input, args.column))
    rows = check_urls(urls, workers=args.workers, timeout=args.timeout)
    output = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + ("\n" if output else ""), encoding="utf-8")
    elif output:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
