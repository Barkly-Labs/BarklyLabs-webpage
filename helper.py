#!/usr/bin/env python3

from pathlib import Path
import re

ROOT = Path("src")

# Root-relative links:
#   /about
#   /projects
#   /systems
#
# Become:
#   import.meta.env.BASE_URL + "/about"
#
# Already-fixed links are left alone.

LINK_PATTERN = re.compile(
    r'href="/([^"]*)"'
)

SKIP_PREFIXES = (
    "http://",
    "https://",
    "//",
    "#",
    "mailto:",
    "tel:",
    "javascript:",
)


def fix_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    original = text

    def replace(match: re.Match[str]) -> str:
        target = match.group(1)

        if target.startswith(SKIP_PREFIXES):
            return match.group(0)

        return (
            'href={import.meta.env.BASE_URL.replace(/\\/$/, "")'
            f' + "/{target.lstrip("/")}"}}'
        )

    text = LINK_PATTERN.sub(replace, text)

    if text != original:
        path.write_text(text, encoding="utf-8")
        return True

    return False


def main() -> None:
    changed = 0

    for path in ROOT.rglob("*.astro"):
        if fix_file(path):
            changed += 1
            print(f"fixed: {path}")

    print(f"\nDone. Fixed {changed} file(s).")


if __name__ == "__main__":
    main()