
#!/usr/bin/env python3
"""
Barkly Labs — Astro SEO Metadata Automator

Updates SEO metadata for Barkly Labs Astro pages.

What it does:
- Finds the configured public Astro routes.
- Adds or updates:
    <title>
    meta description
    meta robots
    canonical
    Open Graph metadata
    Twitter metadata
- Adds Organization JSON-LD to the homepage.
- Preserves existing page body/content.
- Does NOT modify robots.txt or sitemap generation.
- Creates .bak backups before modifying files.
- Prints a verification report.

Run from the Astro project root:

    python barkly_seo.py

"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path


SITE = "https://www.barklylabs.space"

# Change this if your GitHub organization URL is different.
GITHUB_ORG = "https://github.com/Barkly-Labs"


PAGES = {
    "/": {
        "file": "src/pages/index.astro",
        "title": "Barkly Labs — Human-Centered Computing",
        "description": (
            "Barkly Labs is a Detroit-based human-centered technology "
            "laboratory exploring AI, software, hardware, robotics, "
            "computer vision, creative technology, education, and community."
        ),
    },

    "/about/": {
        "file": "src/pages/about.astro",
        "title": "Barkly Labs — About",
        "description": (
            "Learn about Barkly Labs, a human-centered technology laboratory "
            "building software, hardware, AI systems, documentation, and "
            "creative technology for people."
        ),
    },

    "/architecture/": {
        "file": "src/pages/architecture.astro",
        "title": "Barkly Labs — Architecture",
        "description": (
            "Explore the technical architecture behind Barkly Labs and its "
            "human-centered computing systems, software, hardware, and interfaces."
        ),
    },

    "/capabilities/": {
        "file": "src/pages/capabilities.astro",
        "title": "Barkly Labs — Capabilities",
        "description": (
            "Explore Barkly Labs capabilities across AI, software engineering, "
            "hardware, robotics, computer vision, documentation, and creative technology."
        ),
    },

    "/charter/": {
        "file": "src/pages/charter.astro",
        "title": "Barkly Labs — Charter",
        "description": (
            "Read the Barkly Labs charter describing the laboratory's mission, "
            "values, human-centered approach, and commitment to accessible technology."
        ),
    },

    "/contact/": {
        "file": "src/pages/contact.astro",
        "title": "Barkly Labs — Contact",
        "description": (
            "Contact Barkly Labs about projects, research, collaboration, "
            "technology, documentation, and human-centered computing."
        ),
    },

    "/core/": {
        "file": "src/pages/core.astro",
        "title": "Barkly Labs — Core Systems",
        "description": (
            "Explore the core systems and technologies developed by Barkly Labs "
            "for human-centered computing."
        ),
    },

    "/cyn-x/": {
        "file": "src/pages/cyn-x.astro",
        "title": "Barkly Labs — CYN-X",
        "description": (
            "CYN-X is Barkly Labs' local AI and human-centered computing system "
            "for building understandable, accessible, and useful AI interfaces."
        ),
    },

    "/docs/": {
        "file": "src/pages/docs.astro",
        "title": "Barkly Labs — Barkly Docs",
        "description": (
            "Barkly Docs is an automated documentation system designed to make "
            "software architecture and project structure easier for humans to understand."
        ),
    },

    "/financial/": {
        "file": "src/pages/financial.astro",
        "title": "Barkly Labs — Financial Information",
        "description": (
            "Financial information and transparency documentation for Barkly Labs "
            "and its technology laboratory projects."
        ),
    },

    "/funding/": {
        "file": "src/pages/funding.astro",
        "title": "Barkly Labs — Funding",
        "description": (
            "Learn about Barkly Labs funding, support, sustainability, and resources "
            "for developing human-centered technology."
        ),
    },

    "/laas/": {
        "file": "src/pages/laas.astro",
        "title": "Barkly Labs — Labs as a Service",
        "description": (
            "Explore Labs as a Service from Barkly Labs for collaborative technology "
            "research, experimentation, software, hardware, and human-centered design."
        ),
    },

    "/principles/": {
        "file": "src/pages/principles.astro",
        "title": "Barkly Labs — Principles",
        "description": (
            "Explore the principles guiding Barkly Labs' approach to human-centered "
            "technology, accessibility, transparency, autonomy, and responsible innovation."
        ),
    },

    "/projects/": {
        "file": "src/pages/projects.astro",
        "title": "Barkly Labs — Projects",
        "description": (
            "Explore Barkly Labs projects spanning AI, software, hardware, robotics, "
            "computer vision, documentation, and human-centered computing."
        ),
    },

    "/standard/": {
        "file": "src/pages/standard.astro",
        "title": "Barkly Standard — Human-Centered Technology",
        "description": (
            "The Barkly Standard is a proposed human-centered technology standard "
            "focused on accessibility, autonomy, privacy, transparency, safety, and evidence."
        ),
    },

    "/standardfull/": {
        "file": "src/pages/standardfull.astro",
        "title": "Barkly Standard — Full Standard",
        "description": (
            "Read the full Barkly Standard for human-centered technology engineering, "
            "including accessibility, cognitive load, privacy, autonomy, safety, and transparency."
        ),
    },
}


def escape_html(value: str) -> str:
    """Escape text for safe HTML attribute/content insertion."""
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def normalize_url(route: str) -> str:
    if route == "/":
        return SITE + "/"
    return SITE + route


def replace_or_insert_tag(
    head: str,
    pattern: str,
    replacement: str,
    *,
    flags: int = re.IGNORECASE,
) -> str:
    """
    Replace an existing metadata tag.
    If none exists, insert it immediately after <head...>.
    """

    updated, count = re.subn(
        pattern,
        replacement,
        head,
        count=1,
        flags=flags,
    )

    if count:
        return updated

    match = re.search(r"<head\b[^>]*>", updated, flags=re.IGNORECASE)

    if not match:
        raise RuntimeError("Could not find <head>.")

    return (
        updated[: match.end()]
        + "\n"
        + replacement
        + updated[match.end() :]
    )


def update_metadata(html: str, route: str, title: str, description: str) -> str:
    """Update metadata inside the document <head>."""

    canonical = normalize_url(route)

    title_html = f"<title>{escape_html(title)}</title>"

    description_html = (
        '<meta name="description" '
        f'content="{escape_html(description)}">'
    )

    robots_html = '<meta name="robots" content="index, follow">'

    canonical_html = (
        f'<link rel="canonical" href="{escape_html(canonical)}">'
    )

    og_tags = "\n".join(
        [
            '<meta property="og:type" content="website">',
            f'<meta property="og:title" content="{escape_html(title)}">',
            (
                '<meta property="og:description" '
                f'content="{escape_html(description)}">'
            ),
            f'<meta property="og:url" content="{escape_html(canonical)}">',
            '<meta property="og:site_name" content="Barkly Labs">',
        ]
    )

    twitter_tags = "\n".join(
        [
            '<meta name="twitter:card" content="summary_large_image">',
            f'<meta name="twitter:title" content="{escape_html(title)}">',
            (
                '<meta name="twitter:description" '
                f'content="{escape_html(description)}">'
            ),
        ]
    )

    # <title>
    html = replace_or_insert_tag(
        html,
        r"<title\b[^>]*>.*?</title>",
        title_html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Description
    html = replace_or_insert_tag(
        html,
        r'<meta\s+name=["\']description["\'][^>]*>',
        description_html,
    )

    # Robots
    html = replace_or_insert_tag(
        html,
        r'<meta\s+name=["\']robots["\'][^>]*>',
        robots_html,
    )

    # Canonical
    html = replace_or_insert_tag(
        html,
        r'<link\s+rel=["\']canonical["\'][^>]*>',
        canonical_html,
    )

    # Open Graph
    for tag in og_tags.splitlines():
        if 'property="og:type"' in tag:
            pattern = r'<meta\s+property=["\']og:type["\'][^>]*>'
        elif 'property="og:title"' in tag:
            pattern = r'<meta\s+property=["\']og:title["\'][^>]*>'
        elif 'property="og:description"' in tag:
            pattern = r'<meta\s+property=["\']og:description["\'][^>]*>'
        elif 'property="og:url"' in tag:
            pattern = r'<meta\s+property=["\']og:url["\'][^>]*>'
        elif 'property="og:site_name"' in tag:
            pattern = r'<meta\s+property=["\']og:site_name["\'][^>]*>'
        else:
            continue

        html = replace_or_insert_tag(
            html,
            pattern,
            tag,
        )

    # Twitter
    for tag in twitter_tags.splitlines():
        if 'name="twitter:card"' in tag:
            pattern = r'<meta\s+name=["\']twitter:card["\'][^>]*>'
        elif 'name="twitter:title"' in tag:
            pattern = r'<meta\s+name=["\']twitter:title["\'][^>]*>'
        elif 'name="twitter:description"' in tag:
            pattern = r'<meta\s+name=["\']twitter:description["\'][^>]*>'
        else:
            continue

        html = replace_or_insert_tag(
            html,
            pattern,
            tag,
        )

    return html


def organization_schema() -> str:
    """Return the homepage Organization JSON-LD."""

    data = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "@id": f"{SITE}/#organization",
        "name": "Barkly Labs",
        "url": f"{SITE}/",
        "description": (
            "Barkly Labs is a Detroit-based human-centered technology "
            "laboratory exploring AI, software, hardware, robotics, "
            "computer vision, creative technology, education, and community."
        ),
        "slogan": "Technology should adapt to humans.",
        "sameAs": [
            GITHUB_ORG,
        ],
        "address": {
            "@type": "PostalAddress",
            "addressLocality": "Detroit",
            "addressRegion": "Michigan",
            "addressCountry": "United States",
        },
        "knowsAbout": [
            "human-centered technology",
            "artificial intelligence",
            "local AI",
            "software engineering",
            "hardware",
            "embedded systems",
            "robotics",
            "computer vision",
            "creative technology",
            "accessibility",
            "documentation",
            "education",
            "community technology",
        ],
    }

    serialized = json.dumps(data, indent=2, ensure_ascii=False)

    return (
        '<script type="application/ld+json">\n'
        f"{serialized}\n"
        "</script>"
    )


def add_homepage_schema(html: str) -> str:
    """Add or replace the homepage Organization schema."""

    schema = organization_schema()

    pattern = (
        r'<script\s+type=["\']application/ld\+json["\']>'
        r'.*?'
        r'</script>'
    )

    # If there is already JSON-LD, leave existing structured data alone.
    # This avoids destroying project-specific schemas.
    if re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL):
        return html

    return replace_or_insert_tag(
        html,
        r"(?!)",  # Never matches; forces insertion after <head>.
        schema,
    )


def process_page(root: Path, route: str, config: dict) -> bool:
    """Process one Astro page."""

    path = root / config["file"]

    if not path.exists():
        print(f"  [MISSING] {route}: {path}")
        return False

    original = path.read_text(encoding="utf-8")

    updated = update_metadata(
        original,
        route,
        config["title"],
        config["description"],
    )

    if route == "/":
        updated = add_homepage_schema(updated)

    if updated == original:
        print(f"  [OK]      {route}: already up to date")
        return True

    backup = path.with_suffix(path.suffix + ".bak")

    if not backup.exists():
        shutil.copy2(path, backup)

    path.write_text(updated, encoding="utf-8")

    print(f"  [UPDATED] {route}: {path}")
    return True


def verify_page(root: Path, route: str, config: dict) -> list[str]:
    """Perform basic source-level SEO verification."""

    path = root / config["file"]

    if not path.exists():
        return [f"{route}: file missing"]

    html = path.read_text(encoding="utf-8")

    problems = []

    if not re.search(r"<title\b[^>]*>.*?</title>", html, re.I | re.S):
        problems.append("missing title")

    if config["title"] not in html:
        problems.append("expected title not found")

    if not re.search(
        r'<meta\s+name=["\']description["\']',
        html,
        re.I,
    ):
        problems.append("missing description")

    if not re.search(
        r'<meta\s+name=["\']robots["\']',
        html,
        re.I,
    ):
        problems.append("missing robots metadata")

    if not re.search(
        r'<link\s+rel=["\']canonical["\']',
        html,
        re.I,
    ):
        problems.append("missing canonical")

    if not re.search(
        r'property=["\']og:title["\']',
        html,
        re.I,
    ):
        problems.append("missing og:title")

    if not re.search(
        r'name=["\']twitter:title["\']',
        html,
        re.I,
    ):
        problems.append("missing twitter:title")

    return problems


def main() -> int:
    root = Path.cwd()

    print()
    print("🐾 Barkly Labs SEO Automator")
    print("============================")
    print(f"Project: {root}")
    print(f"Site:    {SITE}")
    print()

    package_json = root / "package.json"

    if not package_json.exists():
        print("[ERROR] package.json was not found.")
        print("Run this script from the Astro project root.")
        return 1

    print("Updating SEO metadata...")
    print()

    success = 0

    for route, config in PAGES.items():
        if process_page(root, route, config):
            success += 1

    print()
    print(f"Processed {success}/{len(PAGES)} pages.")
    print()

    print("Verifying...")
    print()

    failures = 0

    for route, config in PAGES.items():
        problems = verify_page(root, route, config)

        if problems:
            failures += 1
            print(f"  [CHECK] {route}")
            for problem in problems:
                print(f"          - {problem}")
        else:
            print(f"  [PASS]  {route}")

    print()

    if failures:
        print(f"⚠️  {failures} page(s) need attention.")
    else:
        print("✅ All configured pages passed source-level SEO checks.")

    print()
    print("Important:")
    print("  • robots.txt was not modified.")
    print("  • Astro sitemap configuration was not modified.")
    print("  • .bak files were created for changed pages.")
    print()
    print("Next:")
    print("  npm run build")
    print()

    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
