"""A postcode from a coordinate, in bulk, from api.postcodes.io.

Both scraped portals publish coordinates on their search pages and withhold the
full postcode: Zoopla states none at all, Rightmove states one in about a third
of its results. Reading it off the listing's own page costs 47KB or 100KB and is
budgeted at `POSTCODE_BUDGET` a run, so most new listings are stored without
one — which leaves the alert naming a district and `mark_duplicate` skipping the
listing. See 0056.

api.postcodes.io closes that for nothing: no key, no quota published, and it is
already used elsewhere in this repository for district centroids
(`scripts/seed_locations.py`). It is open source and built on the ONS Postcode
Directory, so the same data can be loaded locally later if the dependency ever
becomes one too many.

── what it returns, exactly ─────────────────────────────────────────────────

The NEAREST postcode centroid, not the postcode of that building. Measured
against the live service on 1 October 2026: within 100m of a point in Canary
Wharf there are five distinct unit postcodes — the nearest 36m away, four more
at 81m. So the answer is the most likely of several and nothing here can know
whether it is the right one. 0056 is where the consequences live; in short, it
is good enough to print and not good enough to merge two listings on.

── the district has to agree ────────────────────────────────────────────────

A fifth of random London points have more than one outcode within 250m, and
portals round the coordinates of a rental. So a derived postcode is accepted
only when its outward code matches the district the listing is already filed
under. That is the same refusal `rightmove.postcode_on` makes against its own
detail page, for the same reason: a neighbour's postcode in the alert is worse
than no postcode, because it reads as fact.

── the transport ────────────────────────────────────────────────────────────

Plain urllib, deliberately not the shared `Fetcher`. That one impersonates a
browser and can route through a residential proxy whose traffic is billed, and
neither is wanted here: postcodes.io is a public JSON API that wants neither
disguise nor a proxy, and sending it through one would put the project's own
lookups on the invoice.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

BULK = "https://api.postcodes.io/postcodes"

# Points per request. The bulk endpoint's own documented maximum, and the
# reason this module exists in bulk form at all: a district's new listings go
# in one request rather than one each.
BATCH = 100

# How far the nearest centroid may be before the answer is not about this
# building any more. 250m is the radius at which a fifth of London points pick
# up a second outcode, so anything past it is a guess about a neighbourhood
# rather than an address.
RADIUS_M = 250

OUTCODE = re.compile(r"^[A-Z]{1,2}\d{1,2}[A-Z]?$")

#: Hands the request body over and returns the decoded reply. Injected so the
#: tests never reach the network, and so a caller can supply its own timeout.
Post = Callable[[str, bytes], dict[str, Any]]


@dataclass(frozen=True)
class Ask:
    """One listing's coordinates, and the district it is filed under."""

    #: Whatever the caller wants the answer keyed by; never sent.
    key: str
    lat: float
    lng: float
    #: The outward code the answer has to agree with, or None to accept any.
    district: str | None = None


def _post(url: str, body: bytes) -> dict[str, Any]:
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        loaded = json.load(response)
    return loaded if isinstance(loaded, dict) else {}


def nearest(asks: list[Ask], *, post: Post | None = None) -> dict[str, str]:
    """key -> full postcode, for the asks a postcode could be found for.

    Silent about the ones it could not answer: a missing key means no postcode
    within `RADIUS_M`, or one whose outward code disagreed with the district, or
    a request that failed. All three leave the listing exactly as it was, which
    is the state this is an improvement on — so none of them is worth raising
    into a run that is otherwise fine. The caller counts the shortfall.
    """

    send = post or _post
    found: dict[str, str] = {}

    for start in range(0, len(asks), BATCH):
        batch = asks[start : start + BATCH]
        body = json.dumps({
            "geolocations": [
                {"longitude": one.lng, "latitude": one.lat,
                 "limit": 1, "radius": RADIUS_M}
                for one in batch
            ]
        }).encode()

        try:
            payload = send(BULK, body)
        except (urllib.error.URLError, OSError, ValueError):
            # One batch lost, the rest still worth asking for.
            continue

        # The service answers in the order it was asked, but says so in each
        # entry's `query` as well — so the pairing is read from the reply
        # rather than assumed from the index.
        for entry in payload.get("result") or []:
            if not isinstance(entry, dict):
                continue
            ask = _asked(batch, entry.get("query"))
            postcode = _postcode(entry.get("result"), ask)
            if ask is not None and postcode is not None:
                found[ask.key] = postcode

    return found


def _asked(batch: list[Ask], query: object) -> Ask | None:
    """Which of the batch this entry is the answer to."""

    if not isinstance(query, dict):
        return None
    lat, lng = query.get("latitude"), query.get("longitude")
    for one in batch:
        if one.lat == lat and one.lng == lng:
            return one
    return None


def _postcode(results: object, ask: Ask | None) -> str | None:
    """The one postcode worth taking from an entry's results."""

    if not isinstance(results, list) or not results or ask is None:
        return None
    first = results[0]
    if not isinstance(first, dict):
        return None

    postcode = str(first.get("postcode") or "").strip().upper()
    outcode = str(first.get("outcode") or "").strip().upper()
    if not postcode or not OUTCODE.fullmatch(outcode):
        return None

    # The district has to agree — see the module note.
    if ask.district and outcode != ask.district.strip().upper():
        return None
    return postcode


__all__ = ["BATCH", "BULK", "RADIUS_M", "Ask", "nearest"]
