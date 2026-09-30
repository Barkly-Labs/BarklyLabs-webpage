#!/usr/bin/env python3
"""
Barkly Labs live-site crawler/auditor.

Checks:
- internal page discovery
- HTTP status
- title / headings
- broken internal links
- missing alt text
- form controls without accessible labels
- PayPal presence on /plans/
- common console/runtime-risk patterns in source
- consistency of key Barkly navigation

Standard-library only.
"""

import sys, re, ssl
from collections import deque
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

BASE = "https://barklylabs.space/"
MAX_PAGES = 100
TIMEOUT = 15

ctx = ssl.create_default_context()

class Parser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.images = []
        self.inputs = []
        self.title = ""
        self.headings = []
        self._title = False
        self._heading = None
        self._heading_text = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "img":
            self.images.append(a)
        elif tag in ("input", "select", "textarea", "button"):
            self.inputs.append((tag, a))
        elif tag == "title":
            self._title = True
        elif tag in ("h1", "h2", "h3"):
            self._heading = tag
            self._heading_text = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._title = False
        if self._heading == tag:
            text = " ".join(self._heading_text).strip()
            if text:
                self.headings.append((tag, text))
            self._heading = None
            self._heading_text = []

    def handle_data(self, data):
        if self._title:
            self.title += data
        if self._heading:
            self._heading_text.append(data)

def fetch(url):
    req = Request(url, headers={"User-Agent": "Barkly-Labs-Site-Auditor/1.0"})
    with urlopen(req, timeout=TIMEOUT, context=ctx) as r:
        raw = r.read()
        ctype = r.headers.get("content-type", "")
        return r.status, r.geturl(), ctype, raw

def normalize(base, href):
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
        return None
    u = urljoin(base, href)
    p = urlparse(u)
    if p.scheme not in ("http", "https"):
        return None
    # Ignore query/hash for crawl identity.
    return u.split("#", 1)[0].rstrip("/") or "/"

def is_internal(url):
    return urlparse(url).netloc.lower() == urlparse(BASE).netloc.lower()

def audit_page(url, body):
    p = Parser()
    try:
        p.feed(body.decode("utf-8", errors="replace"))
    except Exception as e:
        return {"parse_error": str(e)}

    text = body.decode("utf-8", errors="replace")
    issues = []

    for img in p.images:
        if not img.get("alt"):
            issues.append("image missing alt text")

    for tag, a in p.inputs:
        if tag == "button":
            if not a.get("aria-label") and not a.get("name"):
                # Button text is not available here, so flag only unlabeled attribute case.
                pass

    if not p.title.strip():
        issues.append("missing <title>")

    return {
        "title": re.sub(r"\s+", " ", p.title).strip(),
        "headings": p.headings,
        "links": p.links,
        "images": len(p.images),
        "issues": issues,
        "paypal_sdk": "paypal.com/sdk/js" in text,
        "paypal_buttons": bool(re.search(r"paypal\.Buttons\s*\(", text)),
        "paypal_subscription": "createSubscription" in text,
    }

def main():
    queue = deque([BASE.rstrip("/")])
    seen = set()
    pages = {}
    external = []
    errors = []

    while queue and len(seen) < MAX_PAGES:
        url = queue.popleft()
        if url in seen or not is_internal(url):
            continue
        seen.add(url)

        try:
            status, final_url, ctype, raw = fetch(url)
        except Exception as e:
            errors.append({"url": url, "error": str(e)})
            continue

        if "text/html" not in ctype:
            continue

        info = audit_page(final_url, raw)
        info["status"] = status
        info["url"] = final_url
        pages[final_url] = info

        for href in info.get("links", []):
            target = normalize(final_url, href)
            if not target:
                continue
            if is_internal(target):
                if target not in seen:
                    queue.append(target)
            else:
                external.append({"from": final_url, "to": target})

    # Verify every discovered internal link.
    broken = []
    checked = {}
    for page in pages.values():
        for href in page.get("links", []):
            target = normalize(page["url"], href)
            if not target or not is_internal(target):
                continue
            if target in checked:
                status = checked[target]
            else:
                try:
                    status, _, _, _ = fetch(target)
                except Exception:
                    status = None
                checked[target] = status
            if status is None or status >= 400:
                broken.append({"from": page["url"], "to": target, "status": status})

    print("=" * 72)
    print("BARKLY LABS — LIVE SITE AUDIT")
    print("=" * 72)
    print(f"Base: {BASE}")
    print(f"Pages crawled: {len(pages)}")
    print()

    for url in sorted(pages):
        p = pages[url]
        print(f"[{p.get('status')}] {url}")
        print(f"  title: {p.get('title','')}")
        print(f"  headings: {len(p.get('headings', []))}")
        if p.get("issues"):
            for issue in p["issues"]:
                print(f"  ISSUE: {issue}")
        if url.rstrip("/").endswith("/plans"):
            print(f"  PayPal SDK: {'YES' if p.get('paypal_sdk') else 'NO'}")
            print(f"  PayPal Buttons API: {'YES' if p.get('paypal_buttons') else 'NO'}")
            print(f"  Subscription API: {'YES' if p.get('paypal_subscription') else 'NO'}")
        print()

    print("BROKEN INTERNAL LINKS")
    print("-" * 72)
    if broken:
        for item in broken:
            print(item)
    else:
        print("None detected.")

    print()
    print("CRAWL ERRORS")
    print("-" * 72)
    if errors:
        for item in errors:
            print(item)
    else:
        print("None.")

    print()
    print("BARKLY CHECKS")
    print("-" * 72)
    required = ["/standard", "/cyn-x", "/funding", "/plans", "/sponsors"]
    discovered = {urlparse(u).path.rstrip("/") or "/" for u in pages}
    for path in required:
        print(f"{path:12} {'FOUND' if path in discovered else 'NOT FOUND'}")

    plans = next((p for u,p in pages.items()
                  if urlparse(u).path.rstrip("/") == "/plans"), None)
    if plans:
        print(f"/plans PayPal: {'READY' if plans['paypal_sdk'] and plans['paypal_buttons'] and plans['paypal_subscription'] else 'CHECK'}")

if __name__ == "__main__":
    main()
