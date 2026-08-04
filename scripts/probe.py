"""Reachability probe for candidate sources.

Answers two separate questions per site, and keeps them separate on purpose:

  reachable   does the site serve this page to this machine, with which client
              profile, and does the page actually contain listing fields
  permitted   does the site's own robots.txt allow an automated client to fetch
              that path

A 200 does not imply permission, and a robots rule does not imply a block. Both
are reported, and a path disallowed by robots is skipped rather than fetched —
the same rule the worker follows.

robots permission is evaluated against our real identity, never against an
impersonated User-Agent: what we claim in a header does not change what we are.

Terms of service are a third axis this script cannot evaluate. For Rightmove and
Zoopla the specification records that their terms prohibit scraping and that they
enforce it; a green row here does not overrule that.

Usage:
    python scripts/probe.py                        # all sites
    python scripts/probe.py --site openrent
    python scripts/probe.py --save tests/fixtures   # keep the bodies
    python scripts/probe.py --url <url>             # one URL, all profiles
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
import time
from dataclasses import dataclass, field

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from worker.fetch.robots import RobotsTxt  # noqa: E402 - path set above for direct runs

HONEST_UA = "LondonRentAlerts/0.1 (+https://github.com/alexremnev/new-home-finder)"
CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_DELAY = 1.5  # seconds between requests, unless robots asks for more


@dataclass
class Profile:
    name: str
    impersonate: str | None
    user_agent: str


PROFILES = [
    # Plain TLS fingerprint, honest UA: the most transparent client available.
    Profile("plain+honest", None, HONEST_UA),
    # Chrome TLS/JA3 with the honest UA separates "UA rejected" from "fingerprint
    # rejected" — indistinguishable from a status code alone.
    Profile("chrome-tls+honest", "chrome124", HONEST_UA),
    Profile("chrome-tls+chrome-ua", "chrome124", CHROME_UA),
]


@dataclass
class Site:
    key: str
    base: str
    search_url: str
    listing_re: str
    sitemaps: tuple[str, ...] = ()
    detail_markers: tuple[str, ...] = ("pcm", "per month", "deposit", "bedroom")
    search_markers: tuple[str, ...] = ("pcm", "per month", "bedroom")
    note: str = ""


SITES = {
    "openrent": Site(
        key="openrent",
        base="https://www.openrent.co.uk",
        search_url="https://www.openrent.co.uk/properties-to-rent/london?term=London",
        listing_re=r"/property-to-rent/[a-z0-9\-/]+/\d+",
        sitemaps=(
            "https://www.openrent.co.uk/sitemap.xml",
            "https://www.openrent.co.uk/sitemap-listings-1.xml",
        ),
        detail_markers=("p/m", "p/w", "deposit", "bedrooms", "available"),
        search_markers=("p/m", "p/w", "/property-to-rent/", "bedrooms"),
        note="Landlord-direct model; robots allows the listing paths. Primary candidate.",
    ),
    "rightmove": Site(
        key="rightmove",
        base="https://www.rightmove.co.uk",
        search_url="https://www.rightmove.co.uk/property-to-rent/London.html",
        listing_re=r"/properties/\d+",
        sitemaps=("https://www.rightmove.co.uk/sitemap.xml",),
        search_markers=("pcm", "per month", "/properties/"),
        note=(
            "Terms of service prohibit scraping and are enforced (spec section 9). "
            "robots.txt names several property aggregators and blocks them outright, "
            "which indicates intent even where our own path is not listed."
        ),
    ),
    "zoopla": Site(
        key="zoopla",
        base="https://www.zoopla.co.uk",
        search_url="https://www.zoopla.co.uk/to-rent/property/london/",
        listing_re=r"/to-rent/details/\d+",
        sitemaps=("https://www.zoopla.co.uk/xmlsitemap/sitemap-index.xml",),
        search_markers=("pcm", "per month", "/to-rent/details/"),
        note=(
            "Terms of service prohibit scraping and are enforced (spec section 9). "
            "robots.txt blocks a list of named crawlers outright."
        ),
    ),
}

DIAGNOSTIC_HEADERS = (
    "allow", "server", "content-type", "cf-ray", "cf-mitigated", "x-iinfo",
    "x-cdn", "x-cache", "x-served-by", "via", "x-amz-cf-id", "location",
)

CHALLENGE_MARKERS = (
    "captcha", "human verification", "verify that you're not a robot",
    "verify you are human", "cf-challenge", "just a moment", "are you a robot",
    "press and hold", "enable javascript and cookies",
)
BLOCK_MARKERS = (
    "attention required", "incapsula", "incident id", "request unsuccessful",
    "access denied", "unusual traffic", "reference #", "pardon our interruption",
)


@dataclass
class Target:
    name: str
    url: str
    markers: tuple[str, ...] = ()
    min_bytes: int = 200
    dynamic: bool = False


@dataclass
class Result:
    site: str
    target: str
    profile: str
    status: int | None
    body_len: int
    verdict: str
    robots: str = "?"
    headers: dict[str, str] = field(default_factory=dict)
    snippet: str = ""
    error: str | None = None
    markers_found: tuple[str, ...] = ()
    listing_links: int = 0


def fetch(profile: Profile, url: str, timeout: int = 30):
    """Return (status, text, headers, error)."""
    headers = {
        "User-Agent": profile.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
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


def classify(status: int | None, body: str, target: Target) -> tuple[str, tuple[str, ...]]:
    if status is None:
        return "ERROR", ()

    head = body[:8000].lower()
    if any(m in head for m in CHALLENGE_MARKERS):
        return f"CHALLENGE({status})", ()
    if any(m in head for m in BLOCK_MARKERS):
        return f"BLOCKED({status})/page", ()
    if status in (403, 405, 406, 429):
        return f"{'RATE-LIMITED' if status == 429 else 'BLOCKED'}({status})", ()
    if status in (301, 302, 303, 307, 308):
        return f"REDIRECT({status})", ()
    if status >= 500:
        return f"SERVER({status})", ()
    if status != 200:
        return f"HTTP({status})", ()
    if len(body) < target.min_bytes:
        return "200/SHORT", ()

    # Markers are matched against the whole body. A listing page runs to a few
    # hundred kilobytes and its fields sit well past any fixed prefix; scanning
    # only the head reports a fully served page as empty.
    low = body.lower()
    found = tuple(m for m in target.markers if m in low)
    if target.markers and not found:
        return "200/NO-DATA", ()
    return "200/OK", found


class Robots:
    """robots.txt for one host, evaluated against our real identity.

    Uses the project's own matcher rather than `urllib.robotparser`, which ignores
    wildcards and would report a path such as `/api/*` as allowed.
    """

    def __init__(self, base: str) -> None:
        self.base = base
        self.url = f"{base}/robots.txt"
        self.rules = RobotsTxt()
        self.crawl_delay: float | None = None
        self.blanket_disallow = False

    def load(self) -> str:
        status, body, _, err = fetch(PROFILES[0], self.url)
        if status != 200 or not body:
            return f"unavailable (status={status}{', ' + err if err else ''})"
        self.rules = RobotsTxt.parse(body)
        self.crawl_delay = self.rules.crawl_delay(HONEST_UA)
        self.blanket_disallow = self.rules.disallows_everything(HONEST_UA)
        bits = [f"{len(body)} bytes"]
        group = self.rules.group_for(HONEST_UA)
        bits.append(f"group={'/'.join(group.tokens) if group else 'none'}")
        bits.append(f"{len(group.rules) if group else 0} rules apply to us")
        if self.crawl_delay:
            bits.append(f"crawl-delay={self.crawl_delay}s")
        if self.blanket_disallow:
            bits.append("DISALLOWS US SITE-WIDE")
        return ", ".join(bits)

    def allows(self, url: str) -> bool:
        return self.rules.can_fetch(HONEST_UA, url)


def find_listing_url(site: Site, body: str) -> str | None:
    match = re.search(site.listing_re, body)
    if not match:
        return None
    path = match.group(0)
    return path if path.startswith("http") else f"{site.base}{path}"


def probe_site(site: Site, *, delay: float, save: str | None) -> list[Result]:
    print(f"\n{'=' * 78}\n{site.key.upper()}  {site.base}")
    if site.note:
        print(f"  note: {site.note}")

    robots = Robots(site.base)
    print(f"  robots.txt: {robots.load()}")

    pace = max(delay, robots.crawl_delay or 0)
    if pace > delay:
        print(f"  pacing raised to {pace}s by crawl-delay")

    # The search page doubles as the source of a real listing URL, so no example
    # can go stale and no extra request is spent finding one.
    detail_url: str | None = None
    if robots.allows(site.search_url):
        status, body, _, _ = fetch(PROFILES[0], site.search_url)
        time.sleep(pace)
        if status == 200:
            detail_url = find_listing_url(site, body)
    if detail_url is None:
        for sitemap in site.sitemaps:
            if not robots.allows(sitemap):
                continue
            status, body, _, _ = fetch(PROFILES[0], sitemap)
            time.sleep(pace)
            if status == 200:
                detail_url = find_listing_url(site, body)
                if detail_url:
                    break
    print(f"  listing url: {detail_url or 'not found — listing page cannot be tested'}")

    targets = [Target("robots", robots.url, min_bytes=50)]
    for i, sitemap in enumerate(site.sitemaps):
        targets.append(
            Target(f"sitemap{i or ''}", sitemap, markers=("<loc>", "<sitemap"), min_bytes=150)
        )
    targets.append(
        Target("search", site.search_url, markers=site.search_markers,
               min_bytes=2000, dynamic=True)
    )
    if detail_url:
        targets.append(
            Target("detail", detail_url, markers=site.detail_markers,
                   min_bytes=2000, dynamic=True)
        )

    results: list[Result] = []
    print(f"\n  {'target':12} {'profile':22} {'robots':9} {'status':>6} {'bytes':>9}  verdict")
    print("  " + "-" * 76)

    for target in targets:
        allowed = robots.allows(target.url)
        for profile in PROFILES:
            if not allowed:
                # The worker respects robots, so the probe does too. Reporting a
                # status obtained by ignoring the rule would be misleading.
                results.append(
                    Result(site.key, target.name, profile.name, None, 0,
                           "SKIPPED(robots)", robots="disallow")
                )
                print(f"  {target.name:12} {profile.name:22} {'disallow':9} "
                      f"{'-':>6} {0:>9}  SKIPPED(robots)")
                continue

            status, body, headers, error = fetch(profile, target.url)
            verdict, found = ("ERROR", ()) if error else classify(status, body, target)
            links = len(re.findall(site.listing_re, body)) if body else 0
            results.append(
                Result(
                    site=site.key, target=target.name, profile=profile.name, status=status,
                    body_len=len(body), verdict=verdict, robots="allow", headers=headers,
                    snippet=_snippet(body), error=error, markers_found=found,
                    listing_links=links,
                )
            )
            if save:
                _save(save, site.key, target.name, profile.name, body)
            extra = ""
            if verdict.startswith("200/OK") and target.dynamic:
                extra = f"  fields={','.join(found)} links={links}"
            print(f"  {target.name:12} {profile.name:22} {'allow':9} "
                  f"{status or '-':>6} {len(body):>9}  {verdict}{extra}")
            time.sleep(pace)

    _report_details(results)
    _report_site_verdict(site, results, robots)
    return results


def _snippet(body: str, limit: int = 400) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body[:8000], flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _save(directory: str, site: str, target: str, profile: str, body: str) -> None:
    path = pathlib.Path(directory) / site
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{target}.{profile.replace('+', '_')}.html").write_text(body, encoding="utf-8")


def _report_details(results: list[Result]) -> None:
    interesting = [
        r for r in results
        if not r.verdict.startswith("200/OK") and r.verdict != "SKIPPED(robots)"
    ]
    if not interesting:
        return
    seen: set[tuple[str, int | None, int]] = set()
    print("\n  detail on non-successful responses")
    for r in interesting:
        key = (r.target, r.status, r.body_len)
        if key in seen:
            continue
        seen.add(key)
        print(f"\n    {r.target} · {r.profile} · status={r.status} · {r.body_len} bytes")
        if r.error:
            print(f"      error: {r.error}")
        for name in DIAGNOSTIC_HEADERS:
            if name in r.headers:
                print(f"      {name}: {r.headers[name][:140]}")
        if r.snippet:
            print(f"      body: {r.snippet}")


def _report_site_verdict(site: Site, results: list[Result], robots: Robots) -> None:
    detail = [r for r in results if r.target == "detail"]
    ok = [r for r in detail if r.verdict.startswith("200/OK")]
    challenged = [r for r in detail if r.verdict.startswith("CHALLENGE")]
    blocked = [r for r in detail if r.verdict.startswith("BLOCKED")]
    skipped = [r for r in detail if r.verdict == "SKIPPED(robots)"]

    print(f"\n  VERDICT for {site.key}")
    if robots.blanket_disallow:
        print("    robots.txt disallows this client site-wide. Nothing further to evaluate.")
        return
    if skipped:
        print("    the listing path is disallowed by robots.txt; not fetched.")
    elif ok:
        best = min(ok, key=lambda r: [p.name for p in PROFILES].index(r.profile))
        print(f"    listing page served with fields present. "
              f"Most transparent profile: {best.profile}")
        print(f"    fields matched: {', '.join(best.markers_found)}")
    elif challenged:
        print("    listing page returns an interactive challenge. Answering it is out of scope;")
        print("    compare with a run from an ordinary connection before drawing conclusions.")
    elif blocked:
        print("    listing page refused without an interactive step.")
    elif not detail:
        print("    no listing url could be found, so the listing page was not tested.")
    else:
        print("    listing page responded but no fields matched; inspect the body above.")
    if site.note:
        print(f"    reachability is not permission — {site.note}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", choices=sorted(SITES), action="append",
                    help="probe one site; repeatable. Default: all")
    ap.add_argument("--url", help="probe a single URL with every profile")
    ap.add_argument("--save", metavar="DIR",
                    help="write response bodies under DIR/<site>/ as test fixtures")
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                    help=f"seconds between requests (default {DEFAULT_DELAY})")
    ap.add_argument("--strict", action="store_true",
                    help="non-zero exit when a listing page is not served")
    args = ap.parse_args()

    if args.url:
        target = Target("custom", args.url, min_bytes=1, dynamic=True)
        print(f"{'profile':22} {'status':>6} {'bytes':>9}  verdict")
        for profile in PROFILES:
            status, body, _, error = fetch(profile, args.url)
            verdict = "ERROR" if error else classify(status, body, target)[0]
            print(f"{profile.name:22} {status or '-':>6} {len(body):>9}  {verdict}")
            if error:
                print(f"  {error}")
            time.sleep(args.delay)
        return 0

    keys = args.site or list(SITES)
    all_results: list[Result] = []
    for key in keys:
        all_results += probe_site(SITES[key], delay=args.delay, save=args.save)

    print(f"\n{'=' * 78}\nSUMMARY")
    for key in keys:
        detail = [r for r in all_results if r.site == key and r.target == "detail"]
        if not detail:
            state = "no listing url found"
        elif detail[0].verdict == "SKIPPED(robots)":
            state = "robots-disallowed"
        elif any(r.verdict.startswith("200/OK") for r in detail):
            state = "listing page served"
        else:
            state = detail[0].verdict
        print(f"  {key:12} {state}")
    print("\n  Reachability is one axis. robots.txt is the second, and terms of service the")
    print("  third — see the legal section of the specification before adopting a source.")

    if args.strict:
        served = all(
            any(r.verdict.startswith("200/OK")
                for r in all_results if r.site == k and r.target == "detail")
            for k in keys
        )
        return 0 if served else 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
