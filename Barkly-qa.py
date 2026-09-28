#!/usr/bin/env python3
"""
BARKLY LINK CHECKER

Crawls a website and reports broken links.

Features:
- Recursively crawls internal pages
- Checks internal and external links
- Reports HTTP errors
- Reports exactly which page contains each broken link
- Handles relative URLs
- Handles fragments (#section)
- Ignores mailto:, tel:, javascript:, etc.
- Avoids duplicate crawling
- Uses Python standard library only

Usage:

    python barkly_link_checker.py https://barkly-labs.github.io/BarklyLabs-webpage/

Optional:

    python barkly_link_checker.py URL --internal-only

    python barkly_link_checker.py URL --max-pages 500

    python barkly_link_checker.py URL --timeout 10

    python barkly_link_checker.py URL --output broken_links.txt
"""

from __future__ import annotations

import argparse
import re
import sys
import time

from collections import deque
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
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


USER_AGENT = (
    "BarklyLabs-LinkChecker/1.0 "
    "(website quality assurance crawler)"
)


IGNORED_SCHEMES = {
    "mailto",
    "tel",
    "javascript",
    "data",
    "blob",
}


@dataclass
class LinkResult:
    source_page: str
    target_url: str
    status: str
    detail: str


class LinkParser(HTMLParser):
    """
    Extract links from HTML.
    """

    def __init__(self) -> None:
        super().__init__()

        self.links: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:

        tag = tag.lower()

        if tag not in {
            "a",
            "link",
            "script",
            "img",
            "iframe",
            "source",
        }:
            return

        attributes = dict(attrs)

        if tag in {"a", "link"}:
            value = attributes.get("href")

        else:
            value = attributes.get("src")

            if tag == "source":
                value = (
                    attributes.get("src")
                    or attributes.get("srcset")
                )

        if not value:
            return

        # Basic srcset support.
        if "," in value:
            value = value.split(",", 1)[0]

        value = value.strip().split(" ")[0]

        if value:
            self.links.append(value)


def normalize_url(url: str) -> str:
    """
    Normalize a URL so equivalent URLs are treated as one.
    """

    parsed = urlparse(url)

    # Remove fragment.
    parsed = parsed._replace(fragment="")

    # Remove default ports.
    hostname = parsed.hostname

    if hostname:
        hostname = hostname.lower()

    port = parsed.port

    netloc = hostname or ""

    if port:
        if not (
            parsed.scheme == "http"
            and port == 80
        ) and not (
            parsed.scheme == "https"
            and port == 443
        ):
            netloc += f":{port}"

    # Preserve username/password if present.
    if parsed.username:
        credentials = parsed.username

        if parsed.password:
            credentials += ":" + parsed.password

        netloc = credentials + "@" + netloc

    # Normalize empty path.
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


def is_http_url(url: str) -> bool:
    scheme = urlparse(url).scheme.lower()

    return scheme in {
        "http",
        "https",
    }


def same_origin(
    url_a: str,
    url_b: str,
) -> bool:

    a = urlparse(url_a)
    b = urlparse(url_b)

    return (
        a.scheme.lower() == b.scheme.lower()
        and a.hostname.lower()
        == b.hostname.lower()
        and (
            a.port
            or default_port(a.scheme)
        )
        == (
            b.port
            or default_port(b.scheme)
        )
    )


def default_port(scheme: str) -> int:
    return 443 if scheme.lower() == "https" else 80


def fetch(
    url: str,
    timeout: float,
) -> tuple[int, str, bytes]:

    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html,"
                "application/xhtml+xml,"
                "image/avif,"
                "image/webp,"
                "*/*;q=0.8"
            ),
        },
        method="GET",
    )

    try:

        with urlopen(
            request,
            timeout=timeout,
        ) as response:

            status = response.status
            content_type = (
                response.headers.get(
                    "Content-Type",
                    "",
                )
            )

            data = response.read()

            return (
                status,
                content_type,
                data,
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

        raise RuntimeError(
            str(exc.reason)
        ) from exc

    except TimeoutError:

        raise RuntimeError(
            "request timed out"
        )


def looks_like_html(
    content_type: str,
    url: str,
) -> bool:

    content_type = content_type.lower()

    if (
        "text/html" in content_type
        or "application/xhtml+xml"
        in content_type
    ):
        return True

    path = urlparse(url).path.lower()

    return (
        path.endswith(".html")
        or path.endswith("/")
    )


def extract_links(
    html: bytes,
) -> list[str]:

    try:

        text = html.decode(
            "utf-8",
            errors="replace",
        )

    except Exception:

        return []

    parser = LinkParser()

    try:

        parser.feed(text)

    except Exception:

        pass

    return parser.links


def clean_target(
    source_url: str,
    raw_link: str,
) -> str | None:

    raw_link = raw_link.strip()

    if not raw_link:
        return None

    # Ignore fragments.
    if raw_link.startswith("#"):
        return None

    parsed = urlparse(raw_link)

    if parsed.scheme.lower() in IGNORED_SCHEMES:
        return None

    # Protocol-relative URL.
    if raw_link.startswith("//"):
        target = (
            urlparse(source_url).scheme
            + ":"
            + raw_link
        )

    else:
        target = urljoin(
            source_url,
            raw_link,
        )

    if not is_http_url(target):
        return None

    return normalize_url(target)


def check_link(
    source_page: str,
    target_url: str,
    timeout: float,
) -> LinkResult:

    try:

        status, content_type, _ = fetch(
            target_url,
            timeout,
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
            "ERROR",
            str(exc),
        )


def crawl(
    start_url: str,
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

    start_url = normalize_url(
        start_url
    )

    queue = deque([start_url])

    visited_pages: set[str] = set()

    checked_links: set[str] = set()

    broken: list[LinkResult] = []

    pages_checked = 0
    links_checked = 0

    while queue:

        if pages_checked >= max_pages:
            print()
            print(
                f"Reached max page limit: "
                f"{max_pages}"
            )
            break

        page_url = queue.popleft()

        if page_url in visited_pages:
            continue

        visited_pages.add(page_url)

        print(
            f"[PAGE {pages_checked + 1}] "
            f"{page_url}"
        )

        try:

            status, content_type, data = fetch(
                page_url,
                timeout,
            )

        except Exception as exc:

            result = LinkResult(
                page_url,
                page_url,
                "BROKEN",
                str(exc),
            )

            broken.append(result)

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

        if not looks_like_html(
            content_type,
            page_url,
        ):

            continue

        raw_links = extract_links(data)

        page_links: set[str] = set()

        for raw_link in raw_links:

            target = clean_target(
                page_url,
                raw_link,
            )

            if not target:
                continue

            if target in page_links:
                continue

            page_links.add(target)

        for target in sorted(page_links):

            # Don't repeatedly check identical URLs
            # from every page.
            link_key = target

            should_check = (
                link_key not in checked_links
            )

            if should_check:

                checked_links.add(
                    link_key
                )

                links_checked += 1

                result = check_link(
                    page_url,
                    target,
                    timeout,
                )

                if result.status != "OK":

                    broken.append(result)

                    print(
                        "  !!! "
                        f"{result.status}: "
                        f"{target} "
                        f"({result.detail})"
                    )

            # Crawl internal pages.
            if same_origin(
                start_url,
                target,
            ):

                if target not in visited_pages:

                    queue.append(target)

            elif not internal_only:

                # External pages are checked,
                # but aren't crawled.
                pass

        if delay > 0:
            time.sleep(delay)

    return (
        broken,
        pages_checked,
        links_checked,
    )


def write_report(
    output: Path,
    broken: list[LinkResult],
) -> None:

    lines: list[str] = []

    lines.append(
        "BARKLY LABS — BROKEN LINK REPORT"
    )

    lines.append(
        "=" * 70
    )

    lines.append("")

    if not broken:

        lines.append(
            "NO BROKEN LINKS FOUND."
        )

    else:

        lines.append(
            f"Broken links: {len(broken)}"
        )

        lines.append("")

        for index, result in enumerate(
            broken,
            start=1,
        ):

            lines.append(
                f"{index}. {result.target_url}"
            )

            lines.append(
                f"   Found on: "
                f"{result.source_page}"
            )

            lines.append(
                f"   Status: "
                f"{result.detail}"
            )

            lines.append("")

    output.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def main() -> int:

    parser = argparse.ArgumentParser(
        description=(
            "Crawl a website and report "
            "broken links."
        )
    )

    parser.add_argument(
        "url",
        help="Starting website URL.",
    )

    parser.add_argument(
        "--internal-only",
        action="store_true",
        help=(
            "Only report broken links "
            "on the same website."
        ),
    )

    parser.add_argument(
        "--max-pages",
        type=int,
        default=1000,
        help=(
            "Maximum pages to crawl "
            "(default: 1000)."
        ),
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=10,
        help=(
            "HTTP timeout in seconds "
            "(default: 10)."
        ),
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=0.1,
        help=(
            "Delay between page requests "
            "(default: 0.1 seconds)."
        ),
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "barkly_broken_links.txt"
        ),
        help=(
            "Report output file."
        ),
    )

    args = parser.parse_args()

    print()
    print(
        "🐾 BARKLY LABS LINK CHECKER"
    )
    print("=" * 70)

    print(
        f"Starting URL : {args.url}"
    )

    print(
        f"Max pages    : {args.max_pages}"
    )

    print(
        f"Timeout      : {args.timeout}s"
    )

    print()

    broken, pages, links = crawl(
        args.url,
        internal_only=args.internal_only,
        max_pages=args.max_pages,
        timeout=args.timeout,
        delay=args.delay,
    )

    print()
    print("=" * 70)
    print("BARKLY LINK CHECK COMPLETE")
    print("=" * 70)

    print(
        f"Pages checked : {pages}"
    )

    print(
        f"Links checked : {links}"
    )

    print(
        f"Broken links  : {len(broken)}"
    )

    print()

    if broken:

        print(
            "BROKEN LINKS:"
        )

        print()

        for result in broken:

            print(
                f"  ❌ {result.target_url}"
            )

            print(
                f"     Found on: "
                f"{result.source_page}"
            )

            print(
                f"     Reason: "
                f"{result.detail}"
            )

            print()

    else:

        print(
            "✅ NO BROKEN LINKS FOUND."
        )

    write_report(
        args.output,
        broken,
    )

    print(
        f"Report written to: "
        f"{args.output}"
    )

    print()

    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )