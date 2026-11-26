#!/usr/bin/env python3
"""
BARKLY PAGE QA

Multi-target website navigation quality checker.

Checks actual webpage navigation links (<a href="">).

By default, checks ONLY links within the same origin as each target.
External links (CurseForge, Discord, GitHub, etc.) are skipped by default
so third-party bot protection (e.g. HTTP 403) cannot fail deployment QA.

Use --check-external to opt in to checking external links.

Each target may optionally specify an HTTP Host header:

    name=URL|HOST

Example:

    public=http://97.107.133.215/|barklylabs.space

This connects to the URL and sends the specified Host header.

Supported deployment targets:
    1. Local temporary server
       http://127.0.0.1:4321/

    2. Public server IP
       http://203.0.113.10/|barklylabs.space

    3. Primary domain
       http://barklylabs.space/

    4. WWW domain
       http://www.barklylabs.space/

Ignores:
    <script src="...">
    <link href="...">
    <img src="...">
    <source src="...">
    /@vite/...
    /@id/...
    /src/...
    /node_modules/...
    /_astro/...
    ?astro=...
    mailto:
    tel:
    javascript:
    data:
    blob:

Examples:

    # Single target (internal links only)
    python3 Barkly-qa.py --target http://127.0.0.1:4321/

    # Multiple targets
    python3 Barkly-qa.py \
        --target local=http://127.0.0.1:4321/ \
        --target public=http://203.0.113.10/|barklylabs.space \
        --target domain=http://barklylabs.space/ \
        --target www=http://www.barklylabs.space/

    # Optional external link checks
    python3 Barkly-qa.py --check-external \
        --target local=http://127.0.0.1:4321/

    # Backwards-compatible positional URL
    python3 Barkly-qa.py http://127.0.0.1:4321/

Standard library only.
"""

from __future__ import annotations

import argparse
import sys
import time

from collections import deque
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

USER_AGENT = (
    "BarklyLabs-PageQA/2.2 "
    "(website navigation quality checker)"
)

IGNORED_SCHEMES = {
    "mailto",
    "tel",
    "javascript",
    "data",
    "blob",
}

IGNORED_PATH_PREFIXES = (
    "/@vite/",
    "/@id/",
    "/src/",
    "/node_modules/",
    "/_astro/",
)

DEFAULT_MAX_PAGES = 500
DEFAULT_TIMEOUT = 10.0
DEFAULT_DELAY = 0.1


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class QATarget:
    """One independently tested deployment target."""

    name: str
    url: str
    host_header: str | None = None


@dataclass
class LinkResult:
    """Result of checking one webpage link."""

    source_page: str
    target_url: str
    status: str
    detail: str


@dataclass
class TargetResult:
    """Complete result for one QA target."""

    target: QATarget
    broken: list[LinkResult]
    pages_checked: int
    links_checked: int

    @property
    def passed(self) -> bool:
        return not self.broken


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------

class PageLinkParser(HTMLParser):
    """Extract only actual <a href=""> navigation links."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.lower() != "a":
            return

        attributes = dict(attrs)
        href = attributes.get("href")

        if href:
            self.links.append(href.strip())


# ---------------------------------------------------------------------------
# URL handling
# ---------------------------------------------------------------------------

def normalize_url(url: str) -> str:
    """Normalize URLs so duplicates are not checked repeatedly."""
    parsed = urlparse(url)
    parsed = parsed._replace(fragment="")

    hostname = parsed.hostname
    if hostname:
        hostname = hostname.lower()

    netloc = hostname or ""
    port = parsed.port

    if port:
        if not (
            parsed.scheme == "http" and port == 80
        ) and not (
            parsed.scheme == "https" and port == 443
        ):
            netloc += f":{port}"

    path = parsed.path or "/"

    return urlunparse(
        (
            parsed.scheme.lower(),
            netloc,
            path,
            parsed.params,
            parsed.query,
            "",
        )
    )


def default_port(scheme: str) -> int:
    """Return the default HTTP port for a scheme."""
    return 443 if scheme.lower() == "https" else 80


def same_origin(first: str, second: str) -> bool:
    """Determine whether two URLs share scheme, hostname and port."""
    a = urlparse(first)
    b = urlparse(second)

    return (
        a.scheme.lower() == b.scheme.lower()
        and (a.hostname or "").lower() == (b.hostname or "").lower()
        and (a.port or default_port(a.scheme))
        == (b.port or default_port(b.scheme))
    )


def is_ignored_url(url: str) -> bool:
    """Determine whether a URL belongs to ignored infrastructure."""
    parsed = urlparse(url)

    if parsed.scheme.lower() in IGNORED_SCHEMES:
        return True

    path = parsed.path.lower()

    if any(path.startswith(prefix) for prefix in IGNORED_PATH_PREFIXES):
        return True

    if "astro=" in parsed.query.lower():
        return True

    return False


def clean_link(source_page: str, raw_link: str) -> str | None:
    """Resolve and normalize a raw href from a webpage."""
    raw_link = raw_link.strip()

    if not raw_link or raw_link.startswith("#"):
        return None

    target = urljoin(source_page, raw_link)
    parsed = urlparse(target)

    if parsed.scheme.lower() not in {"http", "https"}:
        return None

    target = normalize_url(target)

    if is_ignored_url(target):
        return None

    return target


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def build_request(
    url: str,
    host_header: str | None = None,
) -> Request:
    """Build an HTTP request, optionally using a custom Host header."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,*/*;q=0.8",
    }

    if host_header:
        headers["Host"] = host_header

    return Request(url, headers=headers, method="GET")


def fetch(
    url: str,
    timeout: float,
    host_header: str | None = None,
) -> tuple[int, str, bytes]:
    """Fetch a webpage and return HTTP status, content type and body."""
    request = build_request(url, host_header)

    try:
        with urlopen(request, timeout=timeout) as response:
            return (
                response.status,
                response.headers.get("Content-Type", ""),
                response.read(),
            )

    except HTTPError as exc:
        return (
            exc.code,
            exc.headers.get("Content-Type", ""),
            b"",
        )

    except URLError as exc:
        reason = exc.reason
        raise RuntimeError(str(reason)) from exc

    except TimeoutError:
        raise RuntimeError("request timed out")


# ---------------------------------------------------------------------------
# Page parsing
# ---------------------------------------------------------------------------

def parse_page_links(data: bytes) -> list[str]:
    """Parse webpage HTML and return its <a href=""> links."""
    text = data.decode("utf-8", errors="replace")
    parser = PageLinkParser()

    try:
        parser.feed(text)
    except Exception:
        pass

    return parser.links


# ---------------------------------------------------------------------------
# Link checking
# ---------------------------------------------------------------------------

def check_url(
    source_page: str,
    target_url: str,
    timeout: float,
    host_header: str | None,
) -> LinkResult:
    """Check one URL."""
    try:
        parsed_target = urlparse(target_url)
        parsed_source = urlparse(source_page)

        target_is_same_host = (
            (parsed_target.hostname or "").lower()
            == (parsed_source.hostname or "").lower()
            and (
                parsed_target.port
                or default_port(parsed_target.scheme)
            )
            == (
                parsed_source.port
                or default_port(parsed_source.scheme)
            )
        )

        request_host = host_header if target_is_same_host else None

        status, _, _ = fetch(
            target_url,
            timeout,
            request_host,
        )

        if 200 <= status < 400:
            return LinkResult(
                source_page,
                target_url,
                "OK",
                f"HTTP {status}",
            )

        return LinkResult(
            source_page,
            target_url,
            "BROKEN",
            f"HTTP {status}",
        )

    except Exception as exc:
        return LinkResult(
            source_page,
            target_url,
            "BROKEN",
            str(exc),
        )


# ---------------------------------------------------------------------------
# Crawling
# ---------------------------------------------------------------------------

def crawl(
    target: QATarget,
    *,
    check_external: bool,
    max_pages: int,
    timeout: float,
    delay: float,
) -> tuple[list[LinkResult], int, int]:
    """
    Crawl one QA target.

    By default, links outside the target origin are skipped.
    External links can be checked with --check-external, but are never
    crawled as pages.
    """
    start_url = normalize_url(target.url)
    queue = deque([start_url])
    visited_pages: set[str] = set()
    checked_links: set[str] = set()
    broken: list[LinkResult] = []
    pages_checked = 0
    links_checked = 0

    while queue:
        if pages_checked >= max_pages:
            print()
            print(f"Reached maximum page limit: {max_pages}")
            break

        page_url = queue.popleft()

        if page_url in visited_pages:
            continue

        visited_pages.add(page_url)

        print(f"[PAGE {pages_checked + 1}] {page_url}")

        try:
            status, content_type, data = fetch(
                page_url,
                timeout,
                target.host_header,
            )
        except Exception as exc:
            broken.append(
                LinkResult(page_url, page_url, "BROKEN", str(exc))
            )
            pages_checked += 1
            continue

        pages_checked += 1

        if not (200 <= status < 400):
            broken.append(
                LinkResult(
                    page_url,
                    page_url,
                    "BROKEN",
                    f"HTTP {status}",
                )
            )
            continue

        if (
            "text/html" not in content_type.lower()
            and "application/xhtml+xml" not in content_type.lower()
        ):
            continue

        raw_links = parse_page_links(data)
        page_targets: set[str] = set()

        for raw_link in raw_links:
            target_url = clean_link(page_url, raw_link)

            if target_url:
                page_targets.add(target_url)

        for target_url in sorted(page_targets):
            is_internal = same_origin(start_url, target_url)

            # Skip external links by default. They are not failures.
            if not is_internal and not check_external:
                print(f"  ↗ EXTERNAL (not checked): {target_url}")
                continue

            if target_url not in checked_links:
                checked_links.add(target_url)
                links_checked += 1

                result = check_url(
                    page_url,
                    target_url,
                    timeout,
                    target.host_header,
                )

                if result.status == "BROKEN":
                    broken.append(result)
                    print("  ❌ BROKEN LINK")
                    print(f"     {target_url}")
                    print(f"     HTTP/error: {result.detail}")
                else:
                    print(f"  ✓ {target_url}")

            # Crawl only same-origin webpage links.
            if is_internal:
                parsed = urlparse(target_url)
                path = parsed.path.lower()

                if path.endswith(
                    (
                        ".js", ".css", ".png", ".jpg", ".jpeg",
                        ".gif", ".svg", ".webp", ".ico", ".pdf",
                        ".json", ".xml",
                    )
                ):
                    continue

                if target_url not in visited_pages:
                    queue.append(target_url)

        if delay > 0:
            time.sleep(delay)

    return broken, pages_checked, links_checked


# ---------------------------------------------------------------------------
# Target parsing
# ---------------------------------------------------------------------------

def parse_target(value: str, index: int) -> QATarget:
    """Parse a target supplied through --target."""
    value = value.strip()

    if not value:
        raise ValueError("QA target cannot be empty.")

    host_header: str | None = None

    if "|" in value:
        value, host_header = value.split("|", 1)
        value = value.strip()
        host_header = host_header.strip()

        if not host_header:
            raise ValueError("Target Host header cannot be empty.")

        if "://" in host_header:
            raise ValueError(
                "Target Host header must be a hostname, not a URL."
            )

    if "=" in value:
        name, url = value.split("=", 1)
        name = name.strip()
        url = url.strip()

        if not name:
            raise ValueError(f"Invalid target name: {value}")
    else:
        url = value
        parsed = urlparse(url)
        name = parsed.hostname or f"target-{index}"

    parsed = urlparse(url)

    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError(f"Target must use http:// or https://: {url}")

    if not parsed.hostname:
        raise ValueError(f"Target has no hostname: {url}")

    return QATarget(
        name=name,
        url=normalize_url(url),
        host_header=host_header,
    )


def apply_host_headers(
    targets: list[QATarget],
    hosts: list[str],
) -> list[QATarget]:
    """Apply legacy --host values to targets."""
    if not hosts:
        return targets

    updated: list[QATarget] = []

    if len(targets) == 1:
        target = targets[0]

        if target.host_header:
            return targets

        if len(hosts) != 1:
            raise ValueError(
                "A single target can only use one --host value."
            )

        return [
            QATarget(
                name=target.name,
                url=target.url,
                host_header=hosts[0],
            )
        ]

    if len(hosts) == 1:
        host = hosts[0]

        for target in targets:
            updated.append(
                QATarget(
                    name=target.name,
                    url=target.url,
                    host_header=target.host_header or host,
                )
            )

        return updated

    if len(hosts) != len(targets):
        raise ValueError(
            "When multiple --host values are supplied, "
            "the number of --host values must match the number of targets."
        )

    for target, host in zip(targets, hosts):
        updated.append(
            QATarget(
                name=target.name,
                url=target.url,
                host_header=target.host_header or host,
            )
        )

    return updated


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_target_summary(result: TargetResult) -> None:
    """Print a concise target result."""
    target = result.target

    print()
    print("-" * 70)
    print(f"TARGET: {target.name}")
    print(f"URL:    {target.url}")

    if target.host_header:
        print(f"Host:   {target.host_header}")

    print(f"Pages checked : {result.pages_checked}")
    print(f"Links checked : {result.links_checked}")
    print(f"Broken links  : {len(result.broken)}")
    print(f"STATUS        : {'PASS ✓' if result.passed else 'FAIL ❌'}")
    print("-" * 70)


def write_report(output: str, results: list[TargetResult]) -> None:
    """Write the complete multi-target QA report."""
    lines: list[str] = [
        "BARKLY LABS — PAGE QA REPORT",
        "=" * 70,
        "",
        f"Targets tested: {len(results)}",
        f"Targets passed: {sum(result.passed for result in results)}",
        f"Targets failed: {sum(not result.passed for result in results)}",
        "",
    ]

    for result in results:
        target = result.target

        lines.extend([
            "-" * 70,
            f"TARGET: {target.name}",
            f"URL: {target.url}",
        ])

        if target.host_header:
            lines.append(f"Host: {target.host_header}")

        lines.extend([
            "",
            f"Pages checked: {result.pages_checked}",
            f"Links checked: {result.links_checked}",
            f"Broken links: {len(result.broken)}",
            f"Status: {'PASS' if result.passed else 'FAIL'}",
            "",
        ])

        if result.broken:
            for number, broken in enumerate(result.broken, start=1):
                lines.extend([
                    f"{number}. {broken.target_url}",
                    f"   Found on: {broken.source_page}",
                    f"   Problem: {broken.detail}",
                    "",
                ])
        else:
            lines.extend([
                "No broken checked links found.",
                "",
            ])

    lines.extend([
        "=" * 70,
        "FINAL STATUS: "
        + (
            "PASS"
            if all(result.passed for result in results)
            else "FAIL"
        ),
    ])

    with open(output, "w", encoding="utf-8") as file:
        file.write("\n".join(lines))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Check one or more Barkly website deployment targets "
            "for broken webpage navigation links. External links "
            "are skipped by default."
        )
    )

    parser.add_argument(
        "url",
        nargs="?",
        help="Backwards-compatible single website URL.",
    )

    parser.add_argument(
        "--target",
        action="append",
        default=[],
        help=(
            "Website target. May be supplied multiple times. "
            "Forms: URL, name=URL, or name=URL|HOST."
        ),
    )

    parser.add_argument(
        "--host",
        action="append",
        default=[],
        help=(
            "Legacy HTTP Host header option. May be supplied "
            "multiple times. Prefer name=URL|HOST."
        ),
    )

    parser.add_argument(
        "--check-external",
        action="store_true",
        help="Also check external links (disabled by default).",
    )

    parser.add_argument(
        "--max-pages",
        type=int,
        default=DEFAULT_MAX_PAGES,
        help=f"Maximum pages per target (default: {DEFAULT_MAX_PAGES}).",
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT}).",
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help=f"Delay between pages (default: {DEFAULT_DELAY}).",
    )

    parser.add_argument(
        "--output",
        default="barklyqa.txt",
        help="Report filename (default: barklyqa.txt).",
    )

    args = parser.parse_args()

    target_values = list(args.target)

    if args.url:
        if target_values:
            parser.error(
                "Do not combine the positional URL with --target."
            )
        target_values.append(args.url)

    if not target_values:
        parser.error("At least one target is required.")

    try:
        targets = [
            parse_target(value, index)
            for index, value in enumerate(target_values, start=1)
        ]

        targets = apply_host_headers(targets, args.host)

    except ValueError as exc:
        parser.error(str(exc))

    print()
    print("🐾 BARKLY LABS PAGE QA")
    print("=" * 70)
    print(f"Targets      : {len(targets)}")
    print("Checking     : internal webpage navigation")
    print(
        "External     : "
        + (
            "enabled (--check-external)"
            if args.check_external
            else "skipped by default"
        )
    )
    print("Ignoring     : Astro/Vite internals")
    print()

    results: list[TargetResult] = []

    for target_number, target in enumerate(targets, start=1):
        print()
        print("=" * 70)
        print(f"TARGET {target_number}/{len(targets)}")
        print(f"Name : {target.name}")
        print(f"URL  : {target.url}")

        if target.host_header:
            print(f"Host : {target.host_header}")

        print("=" * 70)

        broken, pages, links = crawl(
            target,
            check_external=args.check_external,
            max_pages=args.max_pages,
            timeout=args.timeout,
            delay=args.delay,
        )

        result = TargetResult(
            target=target,
            broken=broken,
            pages_checked=pages,
            links_checked=links,
        )

        results.append(result)
        print_target_summary(result)

    print()
    print("=" * 70)
    print("BARKLY PAGE QA COMPLETE")
    print("=" * 70)

    passed_targets = sum(result.passed for result in results)
    failed_targets = len(results) - passed_targets
    total_pages = sum(result.pages_checked for result in results)
    total_links = sum(result.links_checked for result in results)
    total_broken = sum(len(result.broken) for result in results)

    print(f"Targets tested : {len(results)}")
    print(f"Targets passed : {passed_targets}")
    print(f"Targets failed : {failed_targets}")
    print(f"Pages checked  : {total_pages}")
    print(f"Links checked  : {total_links}")
    print(f"Broken links   : {total_broken}")
    print()

    for result in results:
        status = "PASS ✓" if result.passed else "FAIL ❌"
        print(f"{result.target.name:<20} {status}")

    print()

    if failed_targets == 0:
        print("🎉 ALL BARKLY DEPLOYMENT TARGETS PASSED!")
    else:
        print("❌ ONE OR MORE BARKLY DEPLOYMENT TARGETS FAILED.")
        print()
        print("Failed targets:")

        for result in results:
            if not result.passed:
                print(f"  ❌ {result.target.name}")

                for broken in result.broken:
                    print(f"     {broken.target_url}")
                    print(f"     Found on: {broken.source_page}")
                    print(f"     Reason: {broken.detail}")

    write_report(args.output, results)

    print()
    print(f"Report: {args.output}")
    print()

    return 0 if failed_targets == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
