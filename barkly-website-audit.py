#!/usr/bin/env python3
"""
Barkly Labs — Human-Centered Compliance Auditor
================================================

Audits the public Barkly Labs website against observable
Barkly Standard evidence.

This tool intentionally does NOT produce a numeric "compliance score".
It reports what the site actually exposes and separates:

    PASS
    REVIEW
    NOT TESTED
    NOT FOUND

Standard library only.
"""

import re
import socket
import ssl
import sys
import time

from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

BASE_URL = "https://barklylabs.space/"
HOST = urlparse(BASE_URL).hostname

MAX_PAGES = 50
MAX_LINK_CHECKS = 30
REQUEST_TIMEOUT = 5
TOTAL_TIMEOUT = 60
WORKERS = 8

REQUIRED_PAGES = {
    "/standard": "Barkly Standard",
    "/standardfull": "Full Barkly Standard",
    "/principles": "Principles",
    "/charter": "Charter",
    "/architecture": "Architecture",
    "/capabilities": "Capabilities",
    "/about": "About",
    "/cyn-x": "CYN-X",
    "/funding": "Funding",
    "/financial": "Financial",
    "/plans": "Plans",
    "/sponsors": "Sponsors",
    "/support": "Support",
    "/docs": "Barkly Docs",
    "/projects": "Projects",
}

EXPECTED_STANDARD_LANGUAGE = [
    "human",
    "accessible",
    "documentation",
    "open",
    "experimental",
    "privacy",
    "safety",
    "transparency",
    "autonomy",
]

EXPECTED_DOCUMENTATION_LANGUAGE = [
    "functional requirements",
    "documentation",
    "system",
]

EXPECTED_PAYMENT_FEATURES = [
    "paypal.com/sdk/js",
    "paypal.buttons",
    "createsubscription",
]


# ---------------------------------------------------------------------------
# NETWORK
# ---------------------------------------------------------------------------

SSL_CONTEXT = ssl.create_default_context()


def fetch(url):
    request = Request(
        url,
        headers={
            "User-Agent": "Barkly-Labs-Compliance-Auditor/1.0",
            "Accept": "text/html,application/xhtml+xml",
        },
    )

    with urlopen(
        request,
        timeout=REQUEST_TIMEOUT,
        context=SSL_CONTEXT,
    ) as response:
        return (
            response.status,
            response.geturl(),
            response.headers.get("content-type", ""),
            response.read(),
        )


# ---------------------------------------------------------------------------
# HTML PARSER
# ---------------------------------------------------------------------------

class SiteParser(HTMLParser):

    def __init__(self):
        super().__init__()

        self.title = ""
        self.lang = None

        self.links = []
        self.images = []

        self.headings = []
        self.forms = []

        self.has_main = False
        self.has_nav = False
        self.has_footer = False
        self.has_header = False

        self._inside_title = False
        self._heading_tag = None
        self._heading_text = []

    def handle_starttag(self, tag, attrs):

        attributes = dict(attrs)

        if tag == "html":
            self.lang = attributes.get("lang")

        elif tag == "title":
            self._inside_title = True

        elif tag == "a":
            href = attributes.get("href")
            if href:
                self.links.append((href, attributes))

        elif tag == "img":
            self.images.append(attributes)

        elif tag == "main":
            self.has_main = True

        elif tag == "nav":
            self.has_nav = True

        elif tag == "footer":
            self.has_footer = True

        elif tag == "header":
            self.has_header = True

        elif tag == "form":
            self.forms.append(attributes)

        elif tag in (
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
        ):
            self._heading_tag = tag
            self._heading_text = []

    def handle_endtag(self, tag):

        if tag == "title":
            self._inside_title = False

        if self._heading_tag == tag:

            text = " ".join(
                self._heading_text
            ).strip()

            if text:
                self.headings.append(
                    (tag, text)
                )

            self._heading_tag = None
            self._heading_text = []

    def handle_data(self, data):

        if self._inside_title:
            self.title += data

        if self._heading_tag:
            self._heading_text.append(data)


# ---------------------------------------------------------------------------
# URL HELPERS
# ---------------------------------------------------------------------------

def normalize_url(base, href):

    if not href:
        return None

    if href.startswith(
        (
            "#",
            "mailto:",
            "tel:",
            "javascript:",
        )
    ):
        return None

    absolute = urljoin(base, href)

    parsed = urlparse(absolute)

    if parsed.scheme not in (
        "http",
        "https",
    ):
        return None

    return absolute.split("#", 1)[0].rstrip("/")


def internal(url):

    return (
        urlparse(url).netloc.lower()
        == urlparse(BASE_URL).netloc.lower()
    )


# ---------------------------------------------------------------------------
# PAGE ANALYSIS
# ---------------------------------------------------------------------------

def analyse_page(url, raw):

    html = raw.decode(
        "utf-8",
        errors="replace",
    )

    parser = SiteParser()
    parser.feed(html)

    title = re.sub(
        r"\s+",
        " ",
        parser.title,
    ).strip()

    path = (
        urlparse(url)
        .path
        .rstrip("/")
        or "/"
    )

    text = html.lower()

    issues = []
    reviews = []

    # -------------------------------------------------------
    # METADATA
    # -------------------------------------------------------

    if not title:
        issues.append(
            "missing page title"
        )

    if not parser.lang:
        reviews.append(
            "missing html lang attribute"
        )

    # Detect duplicated page-name titles.
    page_name = (
        path
        .split("/")[-1]
        .replace("-", " ")
        .strip()
        .lower()
    )

    if page_name:
        compact_title = title.lower()

        if (
            compact_title.count(page_name)
            > 1
        ):
            reviews.append(
                "possible duplicated page name in title"
            )

    # -------------------------------------------------------
    # HEADING STRUCTURE
    # -------------------------------------------------------

    h1_count = sum(
        1
        for tag, _ in parser.headings
        if tag == "h1"
    )

    if not parser.headings:
        issues.append(
            "no headings found"
        )

    if h1_count == 0:
        reviews.append(
            "no h1 heading"
        )

    elif h1_count > 1:
        reviews.append(
            f"{h1_count} h1 headings"
        )

    # -------------------------------------------------------
    # LANDMARKS
    # -------------------------------------------------------

    if not parser.has_main:
        reviews.append(
            "no main landmark"
        )

    if not parser.has_nav:
        reviews.append(
            "no navigation landmark"
        )

    if not parser.has_footer:
        reviews.append(
            "no footer landmark"
        )

    # -------------------------------------------------------
    # IMAGE ACCESSIBILITY
    # -------------------------------------------------------

    missing_alt = 0

    for image in parser.images:

        if "alt" not in image:
            missing_alt += 1

    if missing_alt:
        reviews.append(
            f"{missing_alt} images without alt attributes"
        )

    # -------------------------------------------------------
    # INTERNAL LINKS
    # -------------------------------------------------------

    internal_links = []

    for href, _ in parser.links:

        target = normalize_url(
            url,
            href,
        )

        if (
            target
            and internal(target)
        ):
            internal_links.append(
                target
            )

    # -------------------------------------------------------
    # HUMAN-CENTERED LANGUAGE
    # -------------------------------------------------------

    human_terms = [
        "human",
        "accessible",
        "understand",
        "documentation",
        "open",
        "experiment",
        "agency",
        "privacy",
        "safety",
        "transparency",
    ]

    human_hits = [
        term
        for term in human_terms
        if term in text
    ]

    # -------------------------------------------------------
    # STANDARD PAGE
    # -------------------------------------------------------

    standard_hits = []

    if path in (
        "/standard",
        "/standardfull",
    ):

        standard_hits = [
            term
            for term in EXPECTED_STANDARD_LANGUAGE
            if term in text
        ]

    # -------------------------------------------------------
    # DOCUMENTATION
    # -------------------------------------------------------

    documentation_hits = [
        term
        for term in EXPECTED_DOCUMENTATION_LANGUAGE
        if term in text
    ]

    # -------------------------------------------------------
    # PAYPAL
    # -------------------------------------------------------

    paypal_sdk = (
        "paypal.com/sdk/js"
        in text
    )

    paypal_buttons = bool(
        re.search(
            r"paypal\s*\.\s*buttons\s*\(",
            text,
        )
    )

    paypal_subscription = (
        "createsubscription"
        in text
    )

    paypal_plan_ids = sorted(
        set(
            re.findall(
                r"P-[A-Z0-9]+",
                html,
            )
        )
    )

    if path == "/plans":

        if not paypal_sdk:
            issues.append(
                "PayPal SDK not found"
            )

        if not paypal_buttons:
            issues.append(
                "PayPal Buttons API not found"
            )

        if not paypal_subscription:
            issues.append(
                "PayPal subscription API not found"
            )

        if len(paypal_plan_ids) < 2:
            reviews.append(
                "fewer than two PayPal plan IDs detected"
            )

    return {
        "url": url,
        "path": path,
        "title": title,
        "headings": parser.headings,
        "links": internal_links,

        "issues": issues,
        "reviews": reviews,

        "h1_count": h1_count,
        "images": len(parser.images),
        "missing_alt": missing_alt,

        "lang": parser.lang,

        "main": parser.has_main,
        "nav": parser.has_nav,
        "header": parser.has_header,
        "footer": parser.has_footer,

        "human_hits": human_hits,
        "standard_hits": standard_hits,
        "documentation_hits": documentation_hits,

        "paypal_sdk": paypal_sdk,
        "paypal_buttons": paypal_buttons,
        "paypal_subscription": paypal_subscription,
        "paypal_plan_ids": paypal_plan_ids,
    }


# ---------------------------------------------------------------------------
# CRAWLER
# ---------------------------------------------------------------------------

def crawl(start_time):

    queue = deque([
        BASE_URL.rstrip("/")
    ])

    visited = set()
    pages = {}
    errors = []

    while (
        queue
        and len(visited) < MAX_PAGES
        and time.monotonic()
        - start_time
        < TOTAL_TIMEOUT
    ):

        url = queue.popleft()

        if url in visited:
            continue

        visited.add(url)

        print(
            f"[PAGE {len(visited):02d}/{MAX_PAGES}] {url}",
            flush=True,
        )

        try:

            status, final_url, content_type, raw = fetch(
                url
            )

        except Exception as error:

            errors.append(
                (
                    url,
                    str(error),
                )
            )

            print(
                f"  ERROR: {error}",
                flush=True,
            )

            continue

        if (
            "text/html"
            not in content_type.lower()
        ):
            continue

        page = analyse_page(
            final_url,
            raw,
        )

        page["status"] = status

        pages[final_url] = page

        print(
            f"  {status} | "
            f"{page['title'] or '(no title)'} | "
            f"h1={page['h1_count']} | "
            f"issues={len(page['issues'])} | "
            f"review={len(page['reviews'])}",
            flush=True,
        )

        for target in page["links"]:

            if (
                target not in visited
                and target not in queue
            ):
                queue.append(target)

    return pages, errors


# ---------------------------------------------------------------------------
# LINK CHECKING
# ---------------------------------------------------------------------------

def check_link(url):

    try:

        status, final, content_type, raw = fetch(
            url
        )

        return (
            url,
            status,
            None,
        )

    except Exception as error:

        return (
            url,
            None,
            str(error),
        )


def check_links(pages, start_time):

    candidates = []

    for page in pages.values():

        for link in page["links"]:

            if link not in candidates:
                candidates.append(link)

    candidates = candidates[
        :MAX_LINK_CHECKS
    ]

    broken = []

    if not candidates:
        return broken

    print(
        f"\n[LINK CHECK] {len(candidates)} links",
        flush=True,
    )

    with ThreadPoolExecutor(
        max_workers=WORKERS
    ) as executor:

        futures = [
            executor.submit(
                check_link,
                link,
            )
            for link in candidates
        ]

        for future in as_completed(futures):

            if (
                time.monotonic()
                - start_time
                >= TOTAL_TIMEOUT
            ):
                break

            url, status, error = (
                future.result()
            )

            if (
                error
                or (
                    status is not None
                    and status >= 400
                )
            ):

                broken.append(
                    (
                        url,
                        status or error,
                    )
                )

    return broken


# ---------------------------------------------------------------------------
# REPORTING
# ---------------------------------------------------------------------------

def result(found, expected=True):

    if found:
        return "PASS"

    if expected:
        return "NOT FOUND"

    return "NOT TESTED"


def report(
    pages,
    errors,
    broken,
    elapsed,
):

    print()
    print("=" * 72)
    print(
        "BARKLY LABS — COMPLIANCE AUDIT"
    )
    print("=" * 72)

    print()
    print("AUDIT MODEL")
    print("-" * 72)
    print(
        "This audit reports observable evidence."
    )
    print(
        "It does not assign a numeric compliance score."
    )

    # -------------------------------------------------------
    # SITE INTEGRITY
    # -------------------------------------------------------

    print()
    print("1. SITE INTEGRITY")
    print("-" * 72)

    successful_pages = sum(
        1
        for page in pages.values()
        if page["status"] == 200
    )

    print(
        f"HTTP pages ............. "
        f"{successful_pages}/{len(pages)} PASS"
    )

    print(
        "Broken internal links .. "
        + (
            "PASS"
            if not broken
            else f"REVIEW ({len(broken)})"
        )
    )

    print(
        "Crawler errors ......... "
        + (
            "PASS"
            if not errors
            else f"REVIEW ({len(errors)})"
        )
    )

    # -------------------------------------------------------
    # REQUIRED SYSTEM
    # -------------------------------------------------------

    print()
    print("2. BARKLY SYSTEM SURFACE")
    print("-" * 72)

    discovered = {
        page["path"]
        for page in pages.values()
    }

    for path, name in REQUIRED_PAGES.items():

        print(
            f"{name:<32} "
            f"{'FOUND' if path in discovered else 'NOT FOUND'}"
        )

    # -------------------------------------------------------
    # HUMAN CENTERED
    # -------------------------------------------------------

    print()
    print("3. HUMAN-CENTERED EVIDENCE")
    print("-" * 72)

    human_page_count = sum(
        bool(page["human_hits"])
        for page in pages.values()
    )

    print(
        f"Pages containing human-centered "
        f"language ............ {human_page_count}/{len(pages)}"
    )

    standard_page = next(
        (
            page
            for page in pages.values()
            if page["path"] == "/standard"
        ),
        None,
    )

    if standard_page:

        print(
            "Standard page ........ PASS"
        )

        print(
            "Observable concepts .. "
            + (
                ", ".join(
                    standard_page[
                        "standard_hits"
                    ]
                )
                or "NONE DETECTED"
            )
        )

    else:

        print(
            "Standard page ........ NOT FOUND"
        )

    # -------------------------------------------------------
    # ACCESSIBILITY
    # -------------------------------------------------------

    print()
    print("4. ACCESSIBILITY / STRUCTURE")
    print("-" * 72)

    hard_issues = []

    reviews = []

    for page in pages.values():

        for issue in page["issues"]:

            hard_issues.append(
                (
                    page["path"],
                    issue,
                )
            )

        for review in page["reviews"]:

            reviews.append(
                (
                    page["path"],
                    review,
                )
            )

    print(
        f"Hard issues .......... {len(hard_issues)}"
    )

    print(
        f"Review items .......... {len(reviews)}"
    )

    if hard_issues:

        for path, issue in hard_issues:

            print(
                f"  ISSUE   {path}: {issue}"
            )

    # -------------------------------------------------------
    # DOCUMENTATION
    # -------------------------------------------------------

    print()
    print("5. DOCUMENTATION")
    print("-" * 72)

    docs = [
        page
        for page in pages.values()
        if page["path"].startswith("/docs")
    ]

    print(
        f"Documentation routes .. {len(docs)}"
    )

    print(
        "Documentation surface . "
        + (
            "PASS"
            if docs
            else "NOT FOUND"
        )
    )

    # -------------------------------------------------------
    # EXPERIMENTAL SYSTEMS
    # -------------------------------------------------------

    print()
    print("6. EXPERIMENTAL / ENGINEERING SURFACE")
    print("-" * 72)

    for path in (
        "/cyn-x",
        "/projects",
        "/laas",
        "/docs",
    ):

        print(
            f"{path:<24} "
            f"{'FOUND' if path in discovered else 'NOT FOUND'}"
        )

    # -------------------------------------------------------
    # TRANSPARENCY
    # -------------------------------------------------------

    print()
    print("7. TRANSPARENCY SURFACE")
    print("-" * 72)

    for path in (
        "/financial",
        "/funding",
        "/sponsors",
        "/plans",
        "/support",
    ):

        print(
            f"{path:<24} "
            f"{'FOUND' if path in discovered else 'NOT FOUND'}"
        )

    # -------------------------------------------------------
    # PAYPAL
    # -------------------------------------------------------

    print()
    print("8. PAYPAL / SUBSCRIPTION INTEGRATION")
    print("-" * 72)

    plans = next(
        (
            page
            for page in pages.values()
            if page["path"] == "/plans"
        ),
        None,
    )

    if plans:

        print(
            "PayPal SDK ............ "
            + (
                "PASS"
                if plans["paypal_sdk"]
                else "NOT FOUND"
            )
        )

        print(
            "Buttons API ........... "
            + (
                "PASS"
                if plans["paypal_buttons"]
                else "NOT FOUND"
            )
        )

        print(
            "Subscription API ...... "
            + (
                "PASS"
                if plans[
                    "paypal_subscription"
                ]
                else "NOT FOUND"
            )
        )

        print(
            "Plan IDs .............. "
            + (
                ", ".join(
                    plans[
                        "paypal_plan_ids"
                    ]
                )
                or "NONE"
            )
        )

        print(
            "Rendered browser UI ... NOT TESTED"
        )

    else:

        print(
            "Plans page ............ NOT FOUND"
        )

    # -------------------------------------------------------
    # METADATA REVIEW
    # -------------------------------------------------------

    print()
    print("9. METADATA REVIEW")
    print("-" * 72)

    duplicate_titles = [
        (
            page["path"],
            page["title"],
        )
        for page in pages.values()
        if any(
            "duplicated page name"
            in review
            for review in page["reviews"]
        )
    ]

    if duplicate_titles:

        print(
            f"Possible duplicated titles: "
            f"{len(duplicate_titles)}"
        )

        for path, title in duplicate_titles:

            print(
                f"  REVIEW {path}"
            )
            print(
                f"          {title}"
            )

    else:

        print(
            "Duplicate page titles ... PASS"
        )

    # -------------------------------------------------------
    # REVIEW ITEMS
    # -------------------------------------------------------

    print()
    print("10. ITEMS REQUIRING REVIEW")
    print("-" * 72)

    if reviews:

        for path, review in reviews:

            print(
                f"  REVIEW {path}: {review}"
            )

    else:

        print(
            "None detected."
        )

    # -------------------------------------------------------
    # BROKEN LINKS
    # -------------------------------------------------------

    print()
    print("11. BROKEN LINKS")
    print("-" * 72)

    if broken:

        for url, error in broken:

            print(
                f"  {url} -> {error}"
            )

    else:

        print(
            "None detected."
        )

    # -------------------------------------------------------
    # ERRORS
    # -------------------------------------------------------

    print()
    print("12. CRAWL ERRORS")
    print("-" * 72)

    if errors:

        for url, error in errors:

            print(
                f"  {url}: {error}"
            )

    else:

        print(
            "None."
        )

    # -------------------------------------------------------
    # FINAL
    # -------------------------------------------------------

    print()
    print("=" * 72)
    print(
        "AUDIT COMPLETE"
    )
    print("=" * 72)

    print(
        f"Pages: {len(pages)}"
    )

    print(
        f"Elapsed: {elapsed:.1f}s"
    )

    print()
    print(
        "Important: browser-rendered behavior, "
        "visual design, keyboard interaction, "
        "screen-reader behavior, and third-party "
        "payment rendering require browser testing "
        "and are not claimed as verified here."
    )


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():

    start = time.monotonic()

    print(
        "=" * 72
    )

    print(
        "BARKLY LABS — STARTING COMPLIANCE AUDIT"
    )

    print(
        "=" * 72
    )

    print(
        f"Target: {BASE_URL}"
    )

    print(
        f"Maximum runtime: {TOTAL_TIMEOUT}s"
    )

    print(
        "\nChecking DNS..."
    )

    try:

        socket.getaddrinfo(
            HOST,
            443,
            type=socket.SOCK_STREAM,
        )

    except Exception as error:

        print(
            f"DNS FAILED: {error}"
        )

        return 2

    print(
        "DNS OK."
    )

    pages, errors = crawl(
        start
    )

    if (
        time.monotonic()
        - start
        < TOTAL_TIMEOUT
    ):

        broken = check_links(
            pages,
            start,
        )

    else:

        broken = []

    elapsed = (
        time.monotonic()
        - start
    )

    report(
        pages,
        errors,
        broken,
        elapsed,
    )

    return 0


if __name__ == "__main__":

    raise SystemExit(
        main()
    )