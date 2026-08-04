"""One-shot reachability probe for a source site.

Answers the question that gates the whole POC: does the target site respond to
this machine's IP, and which client profile does it accept?

Runs three client profiles against four URL kinds and prints a matrix. Designed
to run both locally and on a GitHub Actions runner so the two can be compared —
a local 200 and a runner 403 means the runner IP is the problem, not the client.

Usage:
    python scripts/probe.py                 # OpenRent
    python scripts/probe.py --url <url>     # single URL, all profiles
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

HONEST_UA = "LondonRentAlerts/0.1 (+https://github.com/alexremnev/new-home-finder)"
CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Markers that tell us whether listing data is actually in the HTML, as opposed
# to a 200 that returns a JS shell.
DETAIL_MARKERS = ("per month", "Deposit", "Bedrooms")
SEARCH_MARKERS = ("pl_title", "propertyCard", "listing-")

TARGETS = [
    ("robots", "https://www.openrent.co.uk/robots.txt", ()),
    ("sitemap", "https://www.openrent.co.uk/sitemap.xml", ()),
    (
        "detail",
        "https://www.openrent.co.uk/property-to-rent/hayes/"
        "room-in-a-shared-house-mildred-avenue-ub3/63102",
        DETAIL_MARKERS,
    ),
    (
        "search",
        "https://www.openrent.co.uk/properties-to-rent/london?term=London",
        SEARCH_MARKERS,
    ),
]


@dataclass
class Profile:
    name: str
    impersonate: str | None
    user_agent: str


PROFILES = [
    # Plain TLS fingerprint + honest UA. The most transparent client we can be;
    # if this passes, we need nothing else.
    Profile("plain+honest", None, HONEST_UA),
    # Chrome TLS/JA3 fingerprint, honest UA. Distinguishes "UA rejected" from
    # "TLS fingerprint rejected" — the two failure modes look identical in a 403.
    Profile("chrome-tls+honest", "chrome124", HONEST_UA),
    # Full Chrome profile. The fallback the `auto` mode escalates to once.
    Profile("chrome-tls+chrome-ua", "chrome124", CHROME_UA),
]


def fetch(profile: Profile, url: str, timeout: int = 25):
    """Return (status, body_len, snippet, error)."""
    headers = {
        "User-Agent": profile.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
    }
    try:
        if profile.impersonate:
            from curl_cffi import requests as creq

            r = creq.get(
                url, headers=headers, impersonate=profile.impersonate, timeout=timeout
            )
        else:
            import requests

            r = requests.get(url, headers=headers, timeout=timeout)
        body = r.text or ""
        return r.status_code, len(body), body[:400].replace("\n", " "), None
    except Exception as exc:  # noqa: BLE001 - probe reports every failure kind
        return None, 0, "", f"{type(exc).__name__}: {exc}"


def verdict(status, body_len, snippet, markers) -> str:
    if status is None:
        return "ERROR"
    if status == 403:
        return "BLOCKED(403)"
    if status == 429:
        return "RATE-LIMITED(429)"
    if status in (503, 500):
        return f"SERVER({status})"
    if status != 200:
        return f"HTTP({status})"
    low = snippet.lower()
    if any(w in low for w in ("captcha", "cf-challenge", "just a moment", "attention required")):
        return "CHALLENGE"
    if markers and not any(m.lower() in low for m in markers):
        # 200 but no data markers in the first 400 bytes: likely a JS shell.
        # Not conclusive on a snippet alone — flagged, not failed.
        return "200/NO-MARKERS?"
    if body_len < 500:
        return "200/EMPTY?"
    return "200/OK"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="probe a single URL instead of the built-in set")
    args = ap.parse_args()

    targets = [("custom", args.url, ())] if args.url else TARGETS

    print(f"{'target':10} {'profile':22} {'status':6} {'bytes':>8}  verdict")
    print("-" * 72)

    worst = 0
    for kind, url, markers in targets:
        for profile in PROFILES:
            status, body_len, snippet, error = fetch(profile, url)
            v = verdict(status, body_len, snippet, markers)
            shown = str(status) if status is not None else "-"
            print(f"{kind:10} {profile.name:22} {shown:6} {body_len:>8}  {v}")
            if error:
                print(f"{'':40}{error}")
            if v.startswith(("BLOCKED", "CHALLENGE", "ERROR")):
                worst = max(worst, 2)
            elif v.startswith(("RATE", "SERVER", "HTTP", "200/NO", "200/EMPTY")):
                worst = max(worst, 1)

    print("-" * 72)
    print(
        "Read the `detail` row first: it is the page the extractor parses.\n"
        "If `detail` is 200/OK on any profile, the POC is unblocked — pick the\n"
        "most transparent profile that works. If every profile is BLOCKED here\n"
        "but passes locally, the runner IP is the problem: see spec section on\n"
        "execution portability."
    )
    return worst


if __name__ == "__main__":
    sys.exit(main())
