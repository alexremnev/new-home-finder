"""Reachability probe for a source site.

Answers the question that gates the whole POC: does the target site respond to
this machine, and which parts of it respond. Runs three client profiles against
static and dynamic paths and prints a matrix.

Two properties matter for reading the result:

  * Static paths (robots.txt, sitemaps) and dynamic paths (listing and search
    pages) are probed separately. Many edges let static files through and apply
    bot mitigation only to dynamic paths, so the split localises a block.
  * The listing URL is taken from the site's own sitemap rather than hardcoded,
    so a stale example cannot be mistaken for a block.

Run it in both places and compare. Identical failures locally and on a runner
point at the client; a local success with a runner failure points at the IP.

Usage:
    python scripts/probe.py
    python scripts/probe.py --url <url>     # one URL, all profiles
    python scripts/probe.py --strict        # non-zero exit when something fails
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field

HONEST_UA = "LondonRentAlerts/0.1 (+https://github.com/alexremnev/new-home-finder)"
CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

SITEMAP_INDEX = "https://www.openrent.co.uk/sitemap.xml"
SITEMAP_LISTINGS = "https://www.openrent.co.uk/sitemap-listings-1.xml"
SEARCH_URL = "https://www.openrent.co.uk/properties-to-rent/london?term=London"
FALLBACK_DETAIL = (
    "https://www.openrent.co.uk/property-to-rent/hayes/"
    "room-in-a-shared-house-mildred-avenue-ub3/63102"
)

# Headers worth seeing: Allow explains a 405 directly, and the rest identify the
# edge that answered, which is what distinguishes a mitigation page from the app.
DIAGNOSTIC_HEADERS = (
    "allow",
    "server",
    "content-type",
    "cf-ray",
    "cf-mitigated",
    "x-iinfo",
    "x-cdn",
    "x-cache",
    "x-served-by",
    "via",
    "x-sucuri-id",
    "x-amz-cf-id",
    "set-cookie",
    "location",
)

# Content that indicates listing data is present rather than a shell or a
# mitigation page.
DETAIL_MARKERS = ("per month", "deposit", "bedrooms")
SEARCH_MARKERS = ("pl_title", "propertycard", "listing-", "per month")

# Substrings that identify a mitigation page regardless of status code.
BLOCK_MARKERS = (
    "captcha",
    "cf-challenge",
    "just a moment",
    "attention required",
    "incapsula",
    "incident id",
    "request unsuccessful",
    "access denied",
    "unusual traffic",
    "are you a robot",
    "cloudfront",
    "akamai",
    "reference #",
)


@dataclass
class Profile:
    name: str
    impersonate: str | None
    user_agent: str


PROFILES = [
    # Plain TLS fingerprint, honest UA. The most transparent client available; if
    # this passes, nothing further is needed.
    Profile("plain+honest", None, HONEST_UA),
    # Chrome TLS/JA3 fingerprint, honest UA. Separates "UA rejected" from "TLS
    # fingerprint rejected" — indistinguishable from the status code alone.
    Profile("chrome-tls+honest", "chrome124", HONEST_UA),
    # Full Chrome profile, the one the auto mode escalates to once.
    Profile("chrome-tls+chrome-ua", "chrome124", CHROME_UA),
]


@dataclass
class Target:
    name: str
    url: str
    markers: tuple[str, ...] = ()
    min_bytes: int = 200
    dynamic: bool = False


@dataclass
class Result:
    target: str
    profile: str
    status: int | None
    body_len: int
    verdict: str
    headers: dict[str, str] = field(default_factory=dict)
    snippet: str = ""
    error: str | None = None


def fetch(profile: Profile, url: str, timeout: int = 25):
    """Return (status, text, headers, error)."""
    headers = {
        "User-Agent": profile.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    try:
        if profile.impersonate:
            from curl_cffi import requests as creq

            r = creq.get(url, headers=headers, impersonate=profile.impersonate, timeout=timeout)
        else:
            import requests

            r = requests.get(url, headers=headers, timeout=timeout)
        got = {k.lower(): v for k, v in r.headers.items() if k.lower() in DIAGNOSTIC_HEADERS}
        return r.status_code, (r.text or ""), got, None
    except Exception as exc:  # noqa: BLE001 - a probe reports every failure kind
        return None, "", {}, f"{type(exc).__name__}: {exc}"


def classify(status: int | None, body: str, target: Target) -> str:
    if status is None:
        return "ERROR"

    low = body[:4000].lower()
    blocked_by_content = any(m in low for m in BLOCK_MARKERS)

    if status in (403, 405, 406, 429) or blocked_by_content:
        label = "BLOCKED" if status != 429 else "RATE-LIMITED"
        return f"{label}({status}){'/page' if blocked_by_content else ''}"
    if status in (301, 302, 303, 307, 308):
        return f"REDIRECT({status})"
    if status >= 500:
        return f"SERVER({status})"
    if status != 200:
        return f"HTTP({status})"
    if len(body) < target.min_bytes:
        return "200/SHORT"
    if target.markers and not any(m in low for m in target.markers):
        return "200/NO-DATA"
    return "200/OK"


def discover_detail_url(profile: Profile) -> tuple[str, str]:
    """Take a real listing URL from the sitemap, so no example can go stale."""
    status, body, _, err = fetch(profile, SITEMAP_LISTINGS)
    if status == 200 and body:
        found = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", body)
        for url in found:
            if "/property-to-rent/" in url:
                return url, f"from sitemap ({len(found)} urls)"
    reason = f"sitemap unavailable (status={status}{', ' + err if err else ''})"
    return FALLBACK_DETAIL, reason


def build_targets() -> tuple[list[Target], str]:
    detail_url, how = discover_detail_url(PROFILES[0])
    targets = [
        Target("robots", "https://www.openrent.co.uk/robots.txt", min_bytes=100),
        # A sitemap index listing four children is legitimately a few hundred
        # bytes; a generic size floor produces a false alarm here.
        Target("sitemap", SITEMAP_INDEX, markers=("<sitemap",), min_bytes=150),
        Target("sitemap-listings", SITEMAP_LISTINGS, markers=("<loc>",), min_bytes=1000),
        Target("detail", detail_url, markers=DETAIL_MARKERS, min_bytes=2000, dynamic=True),
        Target("search", SEARCH_URL, markers=SEARCH_MARKERS, min_bytes=2000, dynamic=True),
    ]
    return targets, how


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="probe a single URL instead of the built-in set")
    ap.add_argument("--strict", action="store_true", help="non-zero exit when something fails")
    args = ap.parse_args()

    if args.url:
        targets = [Target("custom", args.url, min_bytes=1, dynamic=True)]
        print(f"probing {args.url}\n")
    else:
        targets, how = build_targets()
        print(f"listing url {how}\n  {targets[3].url}\n")

    results: list[Result] = []
    header = f"{'target':17} {'profile':22} {'status':>6} {'bytes':>9}  verdict"
    print(header)
    print("-" * len(header))

    for target in targets:
        for profile in PROFILES:
            status, body, headers, error = fetch(profile, target.url)
            verdict = "ERROR" if error else classify(status, body, target)
            results.append(
                Result(
                    target=target.name,
                    profile=profile.name,
                    status=status,
                    body_len=len(body),
                    verdict=verdict,
                    headers=headers,
                    snippet=_snippet(body),
                    error=error,
                )
            )
            shown = str(status) if status is not None else "-"
            print(f"{target.name:17} {profile.name:22} {shown:>6} {len(body):>9}  {verdict}")

    print("-" * len(header))
    _report_details(results)
    _report_verdict(results, targets)
    return _exit_code(results) if args.strict else 0


def _snippet(body: str, limit: int = 500) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body[:6000], flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _report_details(results: list[Result]) -> None:
    """Show headers and body for anything that did not plainly succeed.

    Without the body a mitigation page and an application error look identical,
    and the two call for different responses.
    """
    interesting = [r for r in results if not r.verdict.startswith("200/OK")]
    if not interesting:
        return
    seen: set[tuple[str, int | None, int]] = set()
    print("\ndetail on non-successful responses")
    for r in interesting:
        key = (r.target, r.status, r.body_len)
        if key in seen:  # identical response across profiles: report once
            continue
        seen.add(key)
        print(f"\n  {r.target} · {r.profile} · status={r.status} · {r.body_len} bytes")
        if r.error:
            print(f"    error: {r.error}")
        for name in DIAGNOSTIC_HEADERS:
            if name in r.headers:
                print(f"    {name}: {r.headers[name][:160]}")
        if r.snippet:
            print(f"    body: {r.snippet}")


def _report_verdict(results: list[Result], targets: list[Target]) -> None:
    dynamic_names = {t.name for t in targets if t.dynamic}
    static_ok = any(
        r.verdict.startswith("200/OK") for r in results if r.target not in dynamic_names
    )
    dynamic_ok = [r for r in results if r.target in dynamic_names and r.verdict.startswith("200/")]
    dynamic_blocked = [
        r for r in results if r.target in dynamic_names and r.verdict.startswith("BLOCKED")
    ]

    print("\nVERDICT")
    if dynamic_ok:
        best = min(dynamic_ok, key=lambda r: PROFILES.index(_profile_by_name(r.profile)))
        print(f"  dynamic pages reachable. Most transparent profile that works: {best.profile}")
        print("  Proceed with the pipeline using that profile.")
    elif dynamic_blocked and static_ok:
        print("  Static files pass, dynamic pages are blocked. The client profile is not the")
        print("  discriminator, since the same client fetched the static paths. Compare with a")
        print("  local run: if dynamic pages pass locally, this host's IP is being rejected and")
        print("  the worker needs a different host or an outbound proxy.")
        print("  Sitemap-based discovery may still work from here; check the sitemap rows above.")
    elif dynamic_blocked:
        print("  Everything is blocked, including static files. Check network egress first.")
    else:
        print("  No dynamic page returned data. Read the detail section above before deciding.")


def _profile_by_name(name: str) -> Profile:
    return next(p for p in PROFILES if p.name == name)


def _exit_code(results: list[Result]) -> int:
    if any(r.verdict.startswith(("BLOCKED", "ERROR")) for r in results):
        return 2
    if any(not r.verdict.startswith("200/OK") for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
