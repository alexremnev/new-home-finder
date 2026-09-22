from __future__ import annotations

import re
import urllib.error
import urllib.request

# One GET of a public listing page to read one meta tag — the same request every
# chat app makes when it draws a link preview. Bounded on purpose: a short
# timeout so a slow portal cannot stall the ingest job, and only the head of the
# document, because og:image lives there and the rest is megabytes of markup.
TIMEOUT = 6.0
HEAD_BYTES = 120_000

AGENT = (
    "Mozilla/5.0 (compatible; LondonHomeFinder/1.0; "
    "+https://londonhomefinder.co.uk)"
)

# property= and name= both appear in the wild, in either attribute order.
OG_IMAGE = re.compile(
    r"""<meta[^>]+?
        (?:property|name)\s*=\s*["'](?:og:image(?::secure_url|:url)?|twitter:image)["']
        [^>]*?content\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE | re.VERBOSE,
)
OG_IMAGE_REVERSED = re.compile(
    r"""<meta[^>]+?content\s*=\s*["']([^"']+)["'][^>]*?
        (?:property|name)\s*=\s*["'](?:og:image(?::secure_url|:url)?|twitter:image)["']""",
    re.IGNORECASE | re.VERBOSE,
)

# Site furniture dressed as a preview. OpenRent states three og:image tags and
# the first two are its own share graphic, so taking the first match in document
# order would put OpenRent's logo in the alert rather than the flat. These are
# the words a portal uses for the picture it shows when it has no picture.
FURNITURE = re.compile(
    r"share-graphic|/logos?/|placeholder|no[-_]?image|default|sprite|watermark",
    re.IGNORECASE,
)

def image_in(html: str) -> str | None:

    # Every candidate, not the first one: a portal may state its own branding
    # before the photograph, and the photograph is the point.
    found: list[str] = []
    for pattern in (OG_IMAGE, OG_IMAGE_REVERSED):
        for match in pattern.finditer(html):
            url = match.group(1).strip()
            # Only something a phone can fetch: WhatsApp will not follow a
            # relative path or a data uri, and neither will anybody's browser.
            if url.startswith("https://") and url not in found:
                found.append(url[:1000])

    for url in found:
        if not FURNITURE.search(url):
            return url

    # Everything on offer looked like branding. Better the portal's own graphic
    # than no picture at all, since the alert is still about a real flat.
    return found[0] if found else None

def fetch_image(url: str, *, opener: object | None = None) -> str | None:

    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    try:
        open_it = opener or urllib.request.urlopen
        with open_it(request, timeout=TIMEOUT) as response:  # type: ignore[operator]
            kind = (response.headers.get("Content-Type") or "").lower()
            if "html" not in kind:
                return None
            body = response.read(HEAD_BYTES)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        # A portal that refuses, redirects oddly or times out is not an error
        # worth failing a run over: the listing goes out without a picture.
        return None

    return image_in(body.decode("utf-8", "replace"))

__all__ = ["fetch_image", "image_in"]
