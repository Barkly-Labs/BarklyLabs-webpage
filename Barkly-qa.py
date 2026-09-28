#!/usr/bin/env python3
"""
BARKLY PAGE QA

Crawls an Astro website and checks actual webpage navigation links.

Checks:
    <a href="...">

Ignores:
    <script src="...">
    <link href="...">
    <img src="...">
    <source src="...">
    /@vite/...
    /@id/...
    /src/...
    /_astro/...
    ?astro=...
    mailto:
    tel:
    javascript:
    data:

Reports:
    - Which page contains the broken link
    - The broken URL
    - HTTP status / connection error

Standard library only.
"""

from __future__ import annotations

import argparse
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


USER_AGENT = (
    "BarklyLabs-PageQA/1.0 "
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


@dataclass
class LinkResult:
    source_page: str
    target_url: str
    status: str
    detail: str


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
            self.links.append(href.strip())


def normalize_url(url: str) -> str:
    """
    Normalize URLs so duplicates are not checked repeatedly.
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

    if scheme.lower() == "https":
        return 443

    return 80


def same_origin(
    first: str,
    second: str,
) -> bool:

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


def fetch(
    url: str,
    timeout: float,
) -> tuple[int, str, bytes]:

    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,*/*;q=0.8",
        },
        method="GET",
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

        raise RuntimeError(
            str(exc.reason)
        ) from exc

    except TimeoutError:

        raise RuntimeError(
            "request timed out"
        )


def parse_page_links(
    data: bytes,
) -> list[str]:

    text = data.decode(
        "utf-8",
        errors="replace",
    )

    parser = PageLinkParser()

    try:
        parser.feed(text)
    except Exception:
        pass

    return parser.links


def check_url(
    source_page: str,
    target_url: str,
    timeout: float,
) -> LinkResult:

    try:

        status, _, _ = fetch(
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
            "BROKEN",
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

            target = clean_link(
                page_url,
                raw_link,
            )

            if not target:
                continue

            page_targets.add(
                target
            )

        for target in sorted(
            page_targets
        ):

            # Check each URL only once.
            if target not in checked_links:

                checked_links.add(
                    target
                )

                links_checked += 1

                result = check_url(
                    page_url,
                    target,
                    timeout,
                )

                if result.status == "BROKEN":

                    broken.append(
                        result
                    )

                    print(
                        "  ❌ BROKEN LINK"
                    )

                    print(
                        f"     {target}"
                    )

                    print(
                        f"     HTTP/error: "
                        f"{result.detail}"
                    )

                else:

                    print(
                        "  ✓ "
                        f"{target}"
                    )

            # Crawl only internal webpage links.
            if same_origin(
                start_url,
                target,
            ):

                parsed = urlparse(
                    target
                )

                # Never crawl ignored infrastructure.
                if is_ignored_url(
                    target
                ):
                    continue

                # Only queue likely webpage URLs.
                path = parsed.path.lower()

                if (
                    path.endswith(
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
                    )
                ):
                    continue

                if target not in visited_pages:

                    queue.append(
                        target
                    )

            elif not internal_only:

                # External links are checked,
                # but never crawled.
                pass

        if delay > 0:
            time.sleep(delay)

    return (
        broken,
        pages_checked,
        links_checked,
    )


def write_report(
    output: str,
    broken: list[LinkResult],
) -> None:

    lines: list[str] = []

    lines.append(
        "BARKLY LABS — PAGE QA REPORT"
    )

    lines.append(
        "=" * 70
    )

    lines.append("")

    if not broken:

        lines.append(
            "NO BROKEN PAGE LINKS FOUND."
        )

    else:

        lines.append(
            f"Broken page links: "
            f"{len(broken)}"
        )

        lines.append("")

        for number, result in enumerate(
            broken,
            start=1,
        ):

            lines.append(
                f"{number}. {result.target_url}"
            )

            lines.append(
                f"   Found on: "
                f"{result.source_page}"
            )

            lines.append(
                f"   Problem: "
                f"{result.detail}"
            )

            lines.append("")

    with open(
        output,
        "w",
        encoding="utf-8",
    ) as file:

        file.write(
            "\n".join(lines)
        )


def main() -> int:

    parser = argparse.ArgumentParser(
        description=(
            "Check an Astro website for "
            "broken webpage navigation links."
        )
    )

    parser.add_argument(
        "url",
        help="Website URL to crawl.",
    )

    parser.add_argument(
        "--internal-only",
        action="store_true",
        help=(
            "Only check links belonging "
            "to this website."
        ),
    )

    parser.add_argument(
        "--max-pages",
        type=int,
        default=500,
        help=(
            "Maximum pages to crawl "
            "(default: 500)."
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
            "Delay between pages "
            "(default: 0.1)."
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

    print()
    print(
        "🐾 BARKLY LABS PAGE QA"
    )
    print(
        "=" * 70
    )

    print(
        f"Website      : {args.url}"
    )

    print(
        "Checking     : webpage navigation only"
    )

    print(
        "Ignoring     : Astro/Vite internals"
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
    print(
        "=" * 70
    )
    print(
        "BARKLY PAGE QA COMPLETE"
    )
    print(
        "=" * 70
    )

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

    if not broken:

        print(
            "🎉 NO BROKEN PAGE LINKS FOUND!"
        )

    else:

        print(
            "BROKEN PAGE LINKS:"
        )

        print()

        for result in broken:

            print(
                f"❌ {result.target_url}"
            )

            print(
                f"   Found on: "
                f"{result.source_page}"
            )

            print(
                f"   Reason: "
                f"{result.detail}"
            )

            print()

    write_report(
        args.output,
        broken,
    )

    print(
        f"Report: {args.output}"
    )

    print()

    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )