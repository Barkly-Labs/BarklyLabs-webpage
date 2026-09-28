#!/usr/bin/env python3
"""
BARKLY PAGE QA

Multi-target website navigation quality checker.

Checks actual webpage navigation links:

    <a href="...">

Supports testing multiple deployment targets in one run.

Each target may optionally specify its HTTP Host header:

    name=URL|HOST

Example:

    public=http://97.107.133.215/|barklylabs.space

This means:

    Connect to: 97.107.133.215
    Send Host:  barklylabs.space

This is useful for testing Nginx virtual-host routing through
a public server IP.

Supported deployment targets:

    1. Local temporary server
       http://127.0.0.1:4321/

    2. Public server IP
       http://203.0.113.10/|barklylabs.space

    3. Primary domain
       http://barklylabs.space/|barklylabs.space

    4. WWW domain
       http://www.barklylabs.space/|www.barklylabs.space

Each target is crawled independently.

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

Reports:

    - Which deployment target failed
    - Which page contains the broken link
    - The broken URL
    - HTTP status / connection error
    - Pages checked
    - Links checked
    - Target pass/fail status

Standard library only.

Examples:

    # Single target
    python3 Barkly-qa.py \
        --target http://127.0.0.1:4321/

    # Multiple targets
    python3 Barkly-qa.py \
        --target http://127.0.0.1:4321/ \
        --target http://203.0.113.10/ \
        --target http://barklylabs.space/ \
        --target http://www.barklylabs.space/

    # Public IP using Barkly hostname routing
    python3 Barkly-qa.py \
        --target public=http://203.0.113.10/|barklylabs.space

    # Named targets
    python3 Barkly-qa.py \
        --target local=http://127.0.0.1:4321/ \
        --target public=http://203.0.113.10/|barklylabs.space \
        --target domain=http://barklylabs.space/|barklylabs.space \
        --target www=http://www.barklylabs.space/|www.barklylabs.space

    # Backwards-compatible positional URL
    python3 Barkly-qa.py http://127.0.0.1:4321/

"""

from __future__ import annotations

import argparse
import sys
import time

from collections import deque
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import (
    urljoin,
    urlparse,
    urlunparse,
)
from urllib.request import (
    Request,
    urlopen,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

USER_AGENT = (
    "BarklyLabs-PageQA/2.1 "
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
    """
    One independently tested deployment target.

    name:
        Human-readable target name.

    url:
        URL used for the actual network connection.

    host_header:
        Optional HTTP Host header.

        This is particularly useful when testing a public IP while
        still asking Nginx to route the request to the Barkly virtual host.
    """

    name: str
    url: str
    host_header: str | None = None


@dataclass
class LinkResult:
    """
    Result of checking one webpage link.
    """

    source_page: str
    target_url: str
    status: str
    detail: str


@dataclass
class TargetResult:
    """
    Complete result for one QA target.
    """

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
    """
    Extract ONLY actual <a href=""> navigation links.
    """

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
            self.links.append(
                href.strip()
            )


# ---------------------------------------------------------------------------
# URL handling
# ---------------------------------------------------------------------------

def normalize_url(
    url: str,
) -> str:
    """
    Normalize URLs so duplicates are not checked repeatedly.

    Fragments are removed because they do not represent separate HTTP
    resources.
    """

    parsed = urlparse(url)

    parsed = parsed._replace(
        fragment=""
    )

    hostname = parsed.hostname

    if hostname:
        hostname = hostname.lower()

    netloc = hostname or ""

    port = parsed.port

    if port:
        if not (
            parsed.scheme == "http"
            and port == 80
        ) and not (
            parsed.scheme == "https"
            and port == 443
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


def default_port(
    scheme: str,
) -> int:
    """
    Return the default HTTP port for a scheme.
    """

    if scheme.lower() == "https":
        return 443

    return 80


def same_origin(
    first: str,
    second: str,
) -> bool:
    """
    Determine whether two URLs share scheme, hostname and port.

    IMPORTANT:

    This deliberately examines the URL hostname, NOT the HTTP Host
    header.

    Therefore:

        http://97.107.133.215/about

    remains part of the public-IP crawl even when the request sends:

        Host: barklylabs.space
    """

    a = urlparse(first)
    b = urlparse(second)

    return (
        a.scheme.lower()
        == b.scheme.lower()
        and (
            a.hostname or ""
        ).lower()
        == (
            b.hostname or ""
        ).lower()
        and (
            a.port
            or default_port(a.scheme)
        )
        == (
            b.port
            or default_port(b.scheme)
        )
    )


def is_ignored_url(
    url: str,
) -> bool:
    """
    Determine whether a URL belongs to ignored infrastructure.
    """

    parsed = urlparse(url)

    scheme = parsed.scheme.lower()

    if scheme in IGNORED_SCHEMES:
        return True

    path = parsed.path.lower()

    for prefix in IGNORED_PATH_PREFIXES:

        if path.startswith(prefix):
            return True

    # Astro development resources.
    if "astro=" in parsed.query.lower():
        return True

    return False


def clean_link(
    source_page: str,
    raw_link: str,
) -> str | None:
    """
    Resolve and normalize a raw href from a webpage.
    """

    raw_link = raw_link.strip()

    if not raw_link:
        return None

    # Same-page fragment.
    if raw_link.startswith("#"):
        return None

    target = urljoin(
        source_page,
        raw_link,
    )

    parsed = urlparse(target)

    if parsed.scheme.lower() not in {
        "http",
        "https",
    }:
        return None

    target = normalize_url(
        target
    )

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
    """
    Build an HTTP request.

    If host_header is supplied, it is sent ONLY as the HTTP Host header.

    The URL remains responsible for the actual network connection.

    Example:

        URL:
            http://97.107.133.215/about

        Host header:
            barklylabs.space

    Network connection:
        97.107.133.215

    HTTP routing:
        barklylabs.space
    """

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,*/*;q=0.8",
    }

    if host_header:
        headers["Host"] = host_header

    return Request(
        url,
        headers=headers,
        method="GET",
    )


def fetch(
    url: str,
    timeout: float,
    host_header: str | None = None,
) -> tuple[int, str, bytes]:
    """
    Fetch a webpage.

    Returns:

        HTTP status
        Content-Type
        Response body

    IMPORTANT:

    host_header changes only the HTTP Host header.

    It does NOT replace the hostname used for the connection.
    """

    request = build_request(
        url,
        host_header,
    )

    try:

        with urlopen(
            request,
            timeout=timeout,
        ) as response:

            return (
                response.status,
                response.headers.get(
                    "Content-Type",
                    "",
                ),
                response.read(),
            )

    except HTTPError as exc:

        return (
            exc.code,
            exc.headers.get(
                "Content-Type",
                "",
            ),
            b"",
        )

    except URLError as exc:

        reason = exc.reason

        if isinstance(
            reason,
            OSError,
        ):

            raise RuntimeError(
                str(reason)
            ) from exc

        raise RuntimeError(
            str(reason)
        ) from exc

    except TimeoutError:

        raise RuntimeError(
            "request timed out"
        )


# ---------------------------------------------------------------------------
# Page parsing
# ---------------------------------------------------------------------------

def parse_page_links(
    data: bytes,
) -> list[str]:
    """
    Parse webpage HTML and return its <a href=""> links.
    """

    text = data.decode(
        "utf-8",
        errors="replace",
    )

    parser = PageLinkParser()

    try:

        parser.feed(
            text
        )

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
    """
    Check one URL.

    For same-origin URLs, the target's Host header is preserved.

    External links receive no custom Host header.

    IMPORTANT:

    Same-origin is determined from the actual URL hostname.

    A public-IP target therefore continues using:

        http://97.107.133.215/...

    while sending:

        Host: barklylabs.space
    """

    try:

        parsed_target = urlparse(
            target_url
        )

        parsed_source = urlparse(
            source_page
        )

        target_is_same_host = (
            (
                parsed_target.hostname or ""
            ).lower()
            ==
            (
                parsed_source.hostname or ""
            ).lower()
            and (
                parsed_target.port
                or default_port(
                    parsed_target.scheme
                )
            )
            ==
            (
                parsed_source.port
                or default_port(
                    parsed_source.scheme
                )
            )
        )

        request_host = (
            host_header
            if target_is_same_host
            else None
        )

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
    internal_only: bool,
    max_pages: int,
    timeout: float,
    delay: float,
) -> tuple[
    list[LinkResult],
    int,
    int,
]:
    """
    Crawl one QA target.

    Each target gets its own independent crawl state.
    """

    start_url = normalize_url(
        target.url
    )

    queue = deque([
        start_url
    ])

    visited_pages: set[str] = set()

    checked_links: set[str] = set()

    broken: list[LinkResult] = []

    pages_checked = 0
    links_checked = 0

    while queue:

        if pages_checked >= max_pages:

            print()

            print(
                f"Reached maximum page limit: "
                f"{max_pages}"
            )

            break

        page_url = queue.popleft()

        if page_url in visited_pages:
            continue

        visited_pages.add(
            page_url
        )

        print(
            f"[PAGE {pages_checked + 1}] "
            f"{page_url}"
        )

        try:

            status, content_type, data = fetch(
                page_url,
                timeout,
                target.host_header,
            )

        except Exception as exc:

            broken.append(
                LinkResult(
                    page_url,
                    page_url,
                    "BROKEN",
                    str(exc),
                )
            )

            pages_checked += 1

            continue

        pages_checked += 1

        if not (
            200 <= status < 400
        ):

            broken.append(
                LinkResult(
                    page_url,
                    page_url,
                    "BROKEN",
                    f"HTTP {status}",
                )
            )

            continue

        # We only crawl HTML pages.
        if (
            "text/html"
            not in content_type.lower()
            and "application/xhtml+xml"
            not in content_type.lower()
        ):

            continue

        raw_links = parse_page_links(
            data
        )

        page_targets: set[str] = set()

        for raw_link in raw_links:

            target_url = clean_link(
                page_url,
                raw_link,
            )

            if not target_url:
                continue

            page_targets.add(
                target_url
            )

        for target_url in sorted(
            page_targets
        ):

            # Check each URL only once.
            if target_url not in checked_links:

                checked_links.add(
                    target_url
                )

                links_checked += 1

                result = check_url(
                    page_url,
                    target_url,
                    timeout,
                    target.host_header,
                )

                if result.status == "BROKEN":

                    broken.append(
                        result
                    )

                    print(
                        "  ❌ BROKEN LINK"
                    )

                    print(
                        f"     {target_url}"
                    )

                    print(
                        f"     HTTP/error: "
                        f"{result.detail}"
                    )

                else:

                    print(
                        "  ✓ "
                        f"{target_url}"
                    )

            # Crawl only internal webpage links.
            if same_origin(
                start_url,
                target_url,
            ):

                parsed = urlparse(
                    target_url
                )

                # Never crawl ignored infrastructure.
                if is_ignored_url(
                    target_url
                ):
                    continue

                # Only queue likely webpage URLs.
                path = parsed.path.lower()

                if path.endswith(
                    (
                        ".js",
                        ".css",
                        ".png",
                        ".jpg",
                        ".jpeg",
                        ".gif",
                        ".svg",
                        ".webp",
                        ".ico",
                        ".pdf",
                        ".json",
                        ".xml",
                    )
                ):
                    continue

                if target_url not in visited_pages:

                    queue.append(
                        target_url
                    )

            elif not internal_only:

                # External links are checked,
                # but never crawled.
                pass

        if delay > 0:

            time.sleep(
                delay
            )

    return (
        broken,
        pages_checked,
        links_checked,
    )


# ---------------------------------------------------------------------------
# Target parsing
# ---------------------------------------------------------------------------

def parse_target(
    value: str,
    index: int,
) -> QATarget:
    """
    Parse a target supplied through --target.

    Supported forms:

        http://127.0.0.1:4321/

        local=http://127.0.0.1:4321/

        public=http://97.107.133.215/|barklylabs.space

    The final |HOST portion is optional.

    IMPORTANT:

        URL|HOST

    means:

        connect to URL
        send HOST as the HTTP Host header

    It does NOT replace the URL hostname.

    This makes public-IP virtual-host testing safe and explicit.
    """

    value = value.strip()

    if not value:
        raise ValueError(
            "QA target cannot be empty."
        )

    # ---------------------------------------------------------------
    # Separate optional target Host header.
    #
    # Example:
    #
    # public=http://97.107.133.215/|barklylabs.space
    # ---------------------------------------------------------------

    host_header: str | None = None

    if "|" in value:

        value, host_header = value.split(
            "|",
            1,
        )

        value = value.strip()
        host_header = host_header.strip()

        if not host_header:

            raise ValueError(
                "Target Host header cannot be empty."
            )

        # Host headers should not contain URL schemes.
        if "://" in host_header:

            raise ValueError(
                "Target Host header must be a hostname, "
                "not a URL."
            )

    # ---------------------------------------------------------------
    # Separate optional target name.
    # ---------------------------------------------------------------

    if "=" in value:

        name, url = value.split(
            "=",
            1,
        )

        name = name.strip()
        url = url.strip()

        if not name:

            raise ValueError(
                f"Invalid target name: {value}"
            )

    else:

        url = value

        parsed = urlparse(
            url
        )

        hostname = parsed.hostname

        if hostname:

            name = hostname

        else:

            name = f"target-{index}"

    parsed = urlparse(
        url
    )

    if parsed.scheme.lower() not in {
        "http",
        "https",
    }:

        raise ValueError(
            f"Target must use http:// or https://: "
            f"{url}"
        )

    if not parsed.hostname:

        raise ValueError(
            f"Target has no hostname: "
            f"{url}"
        )

    return QATarget(
        name=name,
        url=normalize_url(url),
        host_header=host_header,
    )


def apply_host_headers(
    targets: list[QATarget],
    hosts: list[str],
) -> list[QATarget]:
    """
    Apply legacy --host values to targets.

    Explicit URL|HOST target configuration always wins.

    Rules:

        Explicit target Host:
            preserve it.

        One target + one legacy --host:
            applies host to target.

        Multiple targets + one legacy --host:
            host applies to targets that do not already have
            an explicit Host header.

        Multiple targets + multiple legacy --host:
            hosts are assigned by position only where the target
            does not already have an explicit Host header.

        No hosts:
            targets remain unchanged.

    The --host option remains for backwards compatibility.
    New deployments should prefer:

        name=URL|HOST
    """

    if not hosts:
        return targets

    updated: list[QATarget] = []

    if len(targets) == 1:

        target = targets[0]

        if target.host_header:

            updated.append(
                target
            )

            return updated

        if len(hosts) != 1:

            raise ValueError(
                "A single target can only use "
                "one --host value."
            )

        return [
            QATarget(
                name=target.name,
                url=target.url,
                host_header=hosts[0],
            )
        ]

    # Multiple targets + one Host:
    #
    # Preserve explicitly configured Hosts.
    if len(hosts) == 1:

        host = hosts[0]

        for target in targets:

            updated.append(
                QATarget(
                    name=target.name,
                    url=target.url,
                    host_header=(
                        target.host_header
                        or host
                    ),
                )
            )

        return updated

    # Multiple targets + multiple Hosts.
    if len(hosts) != len(targets):

        raise ValueError(
            "When multiple --host values are supplied, "
            "the number of --host values must match "
            "the number of targets."
        )

    for target, host in zip(
        targets,
        hosts,
    ):

        updated.append(
            QATarget(
                name=target.name,
                url=target.url,
                host_header=(
                    target.host_header
                    or host
                ),
            )
        )

    return updated


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_target_summary(
    result: TargetResult,
) -> None:
    """
    Print a concise target result.
    """

    target = result.target

    print()

    print(
        "-" * 70
    )

    print(
        f"TARGET: {target.name}"
    )

    print(
        f"URL:    {target.url}"
    )

    if target.host_header:

        print(
            f"Host:   {target.host_header}"
        )

    print(
        f"Pages checked : "
        f"{result.pages_checked}"
    )

    print(
        f"Links checked : "
        f"{result.links_checked}"
    )

    print(
        f"Broken links  : "
        f"{len(result.broken)}"
    )

    if result.passed:

        print(
            "STATUS        : PASS ✓"
        )

    else:

        print(
            "STATUS        : FAIL ❌"
        )

    print(
        "-" * 70
    )


def write_report(
    output: str,
    results: list[TargetResult],
) -> None:
    """
    Write the complete multi-target QA report.
    """

    lines: list[str] = []

    lines.append(
        "BARKLY LABS — PAGE QA REPORT"
    )

    lines.append(
        "=" * 70
    )

    lines.append("")

    lines.append(
        f"Targets tested: {len(results)}"
    )

    lines.append(
        f"Targets passed: "
        f"{sum(result.passed for result in results)}"
    )

    lines.append(
        f"Targets failed: "
        f"{sum(not result.passed for result in results)}"
    )

    lines.append("")

    for result in results:

        target = result.target

        lines.append(
            "-" * 70
        )

        lines.append(
            f"TARGET: {target.name}"
        )

        lines.append(
            f"URL: {target.url}"
        )

        if target.host_header:

            lines.append(
                f"Host: {target.host_header}"
            )

        lines.append("")

        lines.append(
            f"Pages checked: "
            f"{result.pages_checked}"
        )

        lines.append(
            f"Links checked: "
            f"{result.links_checked}"
        )

        lines.append(
            f"Broken links: "
            f"{len(result.broken)}"
        )

        lines.append(
            f"Status: "
            f"{'PASS' if result.passed else 'FAIL'}"
        )

        lines.append("")

        if result.broken:

            for number, broken in enumerate(
                result.broken,
                start=1,
            ):

                lines.append(
                    f"{number}. {broken.target_url}"
                )

                lines.append(
                    f"   Found on: "
                    f"{broken.source_page}"
                )

                lines.append(
                    f"   Problem: "
                    f"{broken.detail}"
                )

                lines.append("")

        else:

            lines.append(
                "No broken page links found."
            )

            lines.append("")

    lines.append(
        "=" * 70
    )

    lines.append(
        "FINAL STATUS: "
        + (
            "PASS"
            if all(
                result.passed
                for result in results
            )
            else "FAIL"
        )
    )

    with open(
        output,
        "w",
        encoding="utf-8",
    ) as file:

        file.write(
            "\n".join(lines)
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:

    parser = argparse.ArgumentParser(
        description=(
            "Check one or more Barkly website "
            "deployment targets for broken "
            "webpage navigation links."
        )
    )

    parser.add_argument(
        "url",
        nargs="?",
        help=(
            "Backwards-compatible single "
            "website URL."
        ),
    )

    parser.add_argument(
        "--target",
        action="append",
        default=[],
        help=(
            "Website target. May be supplied "
            "multiple times. Forms: URL, "
            "name=URL, or name=URL|HOST."
        ),
    )

    parser.add_argument(
        "--host",
        action="append",
        default=[],
        help=(
            "Legacy HTTP Host header option. "
            "May be supplied multiple times. "
            "Prefer name=URL|HOST."
        ),
    )

    parser.add_argument(
        "--internal-only",
        action="store_true",
        help=(
            "Only check links belonging "
            "to the target website."
        ),
    )

    parser.add_argument(
        "--max-pages",
        type=int,
        default=DEFAULT_MAX_PAGES,
        help=(
            f"Maximum pages per target "
            f"(default: {DEFAULT_MAX_PAGES})."
        ),
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=(
            f"HTTP timeout in seconds "
            f"(default: {DEFAULT_TIMEOUT})."
        ),
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help=(
            f"Delay between pages "
            f"(default: {DEFAULT_DELAY})."
        ),
    )

    parser.add_argument(
        "--output",
        default="barklyqa.txt",
        help=(
            "Report filename "
            "(default: barklyqa.txt)."
        ),
    )

    args = parser.parse_args()

    # ---------------------------------------------------------------
    # Build target list.
    # ---------------------------------------------------------------

    target_values = list(
        args.target
    )

    # Preserve backwards compatibility:
    #
    # python3 Barkly-qa.py http://localhost:4321/
    #
    if args.url:

        if target_values:

            parser.error(
                "Do not combine the positional URL "
                "with --target."
            )

        target_values.append(
            args.url
        )

    if not target_values:

        parser.error(
            "At least one target is required."
        )

    try:

        targets = [
            parse_target(
                value,
                index,
            )
            for index, value
            in enumerate(
                target_values,
                start=1,
            )
        ]

        targets = apply_host_headers(
            targets,
            args.host,
        )

    except ValueError as exc:

        parser.error(
            str(exc)
        )

    # ---------------------------------------------------------------
    # Header.
    # ---------------------------------------------------------------

    print()

    print(
        "🐾 BARKLY LABS PAGE QA"
    )

    print(
        "=" * 70
    )

    print(
        f"Targets      : {len(targets)}"
    )

    print(
        "Checking     : webpage navigation only"
    )

    print(
        "Ignoring     : Astro/Vite internals"
    )

    print()

    # ---------------------------------------------------------------
    # Run each target independently.
    # ---------------------------------------------------------------

    results: list[TargetResult] = []

    for target_number, target in enumerate(
        targets,
        start=1,
    ):

        print()

        print(
            "=" * 70
        )

        print(
            f"TARGET {target_number}/{len(targets)}"
        )

        print(
            f"Name : {target.name}"
        )

        print(
            f"URL  : {target.url}"
        )

        if target.host_header:

            print(
                f"Host : {target.host_header}"
            )

        print(
            "=" * 70
        )

        broken, pages, links = crawl(
            target,
            internal_only=args.internal_only,
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

        results.append(
            result
        )

        print_target_summary(
            result
        )

    # ---------------------------------------------------------------
    # Final report.
    # ---------------------------------------------------------------

    print()

    print(
        "=" * 70
    )

    print(
        "BARKLY PAGE QA COMPLETE"
    )

    print(
        "=" * 70
    )

    passed_targets = sum(
        result.passed
        for result in results
    )

    failed_targets = len(results) - passed_targets

    total_pages = sum(
        result.pages_checked
        for result in results
    )

    total_links = sum(
        result.links_checked
        for result in results
    )

    total_broken = sum(
        len(result.broken)
        for result in results
    )

    print(
        f"Targets tested : "
        f"{len(results)}"
    )

    print(
        f"Targets passed : "
        f"{passed_targets}"
    )

    print(
        f"Targets failed : "
        f"{failed_targets}"
    )

    print(
        f"Pages checked  : "
        f"{total_pages}"
    )

    print(
        f"Links checked  : "
        f"{total_links}"
    )

    print(
        f"Broken links   : "
        f"{total_broken}"
    )

    print()

    # ---------------------------------------------------------------
    # Target-level final status.
    # ---------------------------------------------------------------

    for result in results:

        status = (
            "PASS ✓"
            if result.passed
            else "FAIL ❌"
        )

        print(
            f"{result.target.name:<20} "
            f"{status}"
        )

    print()

    if failed_targets == 0:

        print(
            "🎉 ALL BARKLY DEPLOYMENT TARGETS PASSED!"
        )

    else:

        print(
            "❌ ONE OR MORE BARKLY DEPLOYMENT TARGETS FAILED."
        )

        print()

        print(
            "Failed targets:"
        )

        for result in results:

            if not result.passed:

                print(
                    f"  ❌ {result.target.name}"
                )

                for broken in result.broken:

                    print(
                        f"     {broken.target_url}"
                    )

                    print(
                        f"     Found on: "
                        f"{broken.source_page}"
                    )

                    print(
                        f"     Reason: "
                        f"{broken.detail}"
                    )

    # ---------------------------------------------------------------
    # Write report.
    # ---------------------------------------------------------------

    write_report(
        args.output,
        results,
    )

    print()

    print(
        f"Report: {args.output}"
    )

    print()

    return (
        0
        if failed_targets == 0
        else 1
    )


if __name__ == "__main__":

    raise SystemExit(
        main()
    )