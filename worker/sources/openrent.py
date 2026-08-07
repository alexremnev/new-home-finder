"""OpenRent.

Discovery uses the site's own sitemap. It is published for automated consumption,
`robots.txt` permits it, and it needs nothing that a browser session provides.
The sitemap carries no `lastmod`, so newness is established by comparing the URL
set against what is already stored.

A listing URL carries its outward code — `.../2-bed-flat-rotherhithe-street-se16/1234567`
— which means the district filter is applied before any listing page is
requested. That is the difference between a handful of requests per run and one
per new listing nationwide.

Extraction reads the listing page, which is served in full HTML. Its details sit
in label-and-value tables, so the schema anchors on the label text rather than on
a path to the value cell: rows get reordered, labels do not. Yes/no answers are
icons rather than words, and are read from the icon's colour class — with absence
meaning "not stated", which is a third answer and not a no.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from worker.contracts.extraction import ExtractionSchema, FieldRule
from worker.contracts.listing import Listing, RawListing
from worker.contracts.source import (
    FetchResult,
    FetchTask,
    FieldSpec,
    Mode,
    SourceLocation,
    register,
)
from worker.normalize import dates, money, text
from worker.normalize.geo import in_scope, normalize_outward, split_postcode

BASE = "https://www.openrent.co.uk"
SITEMAP_INDEX = f"{BASE}/sitemap.xml"
SITEMAP_LISTINGS = (f"{BASE}/sitemap-listings-1.xml", f"{BASE}/sitemap-listings-2.xml")

LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
LISTING_PATH = re.compile(r"/property-to-rent/[^/]+/[^/]+/(\d+)/?$")

# The heading holds what the tables do not: bedroom count, property type, street
# and outward code. "1 Bed Flat, Craven Street, WC2N"
HEADING = "h1"

TICK = ".text-success"
CROSS = ".text-danger"


FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(name="external_id", required=True, kind="str",
              hint="listing id; taken from the URL, absent from the page"),
    FieldSpec(name="url", required=True, kind="str", hint="canonical listing URL"),
    FieldSpec(name="title", required=True, kind="str",
              hint="heading, e.g. '1 Bed Flat, Craven Street, WC2N'"),
    FieldSpec(name="price_raw", required=True, kind="str",
              hint="monthly rent as displayed, e.g. '£2,580.00'"),
    FieldSpec(name="bedrooms_raw", required=True, kind="str",
              hint="bedroom count from the heading; 'Studio' and 'Room' are valid"),
    FieldSpec(name="property_type_raw", required=False, kind="str",
              hint="flat, house, studio, room, maisonette"),
    FieldSpec(name="postcode_raw", required=False, kind="str",
              hint="outward code from the heading, e.g. 'WC2N'"),
    FieldSpec(name="deposit_raw", required=False, kind="str", hint="deposit as displayed"),
    FieldSpec(name="available_from_raw", required=False, kind="str",
              hint="availability date, e.g. '15 September, 2026' or 'Today'"),
    FieldSpec(name="min_tenancy_raw", required=False, kind="str",
              hint="preferred minimum tenancy, e.g. '6 Months'"),
    FieldSpec(name="furnished_raw", required=False, kind="str",
              hint="furnished, unfurnished, or part furnished"),
    FieldSpec(name="bills_included", required=False, kind="bool",
              hint="tick or cross beside 'Bills Included'"),
    FieldSpec(name="pets_allowed", required=False, kind="bool",
              hint="tick or cross beside 'Pets Allowed'"),
    FieldSpec(name="smokers_allowed", required=False, kind="bool", hint="tick or cross"),
    FieldSpec(name="student_friendly", required=False, kind="bool", hint="tick or cross"),
    FieldSpec(name="garden", required=False, kind="bool", hint="tick or cross"),
    FieldSpec(name="parking", required=False, kind="bool", hint="tick or cross"),
    FieldSpec(name="epc_rating", required=False, kind="str", hint="EPC band, a single letter"),
    FieldSpec(name="description", required=False, kind="str", hint="listing description"),
    FieldSpec(name="is_landlord_direct", required=False, kind="bool",
              hint="always true: the site is landlord-direct by design"),
)


def _flag(label: str) -> FieldRule:
    return FieldRule(label=label, true_if=TICK, false_if=CROSS)


DETAIL_SCHEMA = ExtractionSchema(
    strategy="dom",
    item="body",
    fields={
        "title": FieldRule(sel=HEADING),
        # Taken from the table row, not from the page header. The header shows the
        # monthly and weekly figures side by side, and reading a price without its
        # period is how a rent ends up wrong by a factor of four.
        "price_raw": FieldRule(label="Rent PCM"),
        "deposit_raw": FieldRule(label="Deposit"),
        "available_from_raw": FieldRule(label="Available From"),
        "min_tenancy_raw": FieldRule(label="Preferred Minimum Tenancy"),
        "furnished_raw": FieldRule(label="Furnishing"),
        "epc_rating": FieldRule(label="EPC Rating"),
        "bills_included": _flag("Bills Included"),
        "pets_allowed": _flag("Pets Allowed"),
        "smokers_allowed": _flag("Smokers Allowed"),
        "student_friendly": _flag("Student Friendly"),
        "garden": _flag("Garden"),
        "parking": _flag("Parking"),
        "bedrooms_raw": FieldRule(sel=HEADING, regex=r"^\s*(\d+\s*Bed|Studio|Room)"),
        "property_type_raw": FieldRule(sel=HEADING, regex=r"^(?:\d+\s*Bed\s+)?([A-Za-z ]+?)\s*,"),
        "postcode_raw": FieldRule(sel=HEADING, regex=r"([A-Za-z]{1,2}\d{1,2}[A-Za-z]?)\s*$"),
        "description": FieldRule(sel="#descriptionText"),
        "is_landlord_direct": FieldRule(const=True),
    },
)


@register
class OpenRent:
    key = "openrent"
    display_name = "OpenRent"
    preferred: tuple[str, ...] = ("dom",)
    fields = FIELDS

    # ── discovery ─────────────────────────────────────────────────────────

    def discover(self, locations: list[SourceLocation], mode: Mode) -> Iterator[FetchTask]:
        """Request the sitemaps. The index is enough for a hot run.

        A hot run reads the index only, which is small and changes when listings
        are added. A sweep reads the full children, which is what reconciliation
        needs to tell a removed listing from one the hot run simply did not see.
        """
        yield FetchTask(source_key=self.key, page_kind="search_list", url=SITEMAP_INDEX)
        if mode == "sweep":
            for url in SITEMAP_LISTINGS:
                yield FetchTask(source_key=self.key, page_kind="search_list", url=url)

    def expand(
        self, result: FetchResult, *, scope: frozenset[str], known_ids: set[str]
    ) -> Iterator[FetchTask]:
        body = result.body.decode("utf-8", errors="replace")
        for url in LOC.findall(body):
            if url.endswith(".xml"):
                # The index points at child sitemaps; follow them in a sweep only.
                if "listings" in url:
                    yield FetchTask(source_key=self.key, page_kind="search_list", url=url)
                continue
            match = LISTING_PATH.search(url)
            if not match:
                continue
            external_id = match.group(1)
            if external_id in known_ids:
                continue
            if scope and not in_scope(url, scope)[0]:
                continue
            yield FetchTask(
                source_key=self.key,
                page_kind="detail",
                url=url,
                external_id=external_id,
            )

    def listing_url(self, external_id: str) -> str:
        # The slug is part of the canonical URL and is not derivable from the id,
        # so a listing URL is only ever taken from the sitemap.
        raise NotImplementedError("OpenRent listing URLs come from the sitemap")

    # ── normalisation ─────────────────────────────────────────────────────

    def normalize(self, raw: RawListing) -> Listing:
        get = raw.get
        title = text.clean(str(get("title") or "")) or ""

        outward = normalize_outward(str(get("postcode_raw") or "")) or split_postcode(title)[0]
        bedrooms = text.bedrooms(str(get("bedrooms_raw") or "")) or text.bedrooms(title)

        return Listing(
            source_key=self.key,
            external_id=str(get("external_id")),
            url=str(get("url")),
            # Monthly by convention: the field is the row labelled "Rent PCM".
            price_pcm=money.to_pcm(str(get("price_raw")), default_period="month"),
            bedrooms=bedrooms if bedrooms is not None else 0,
            property_type=text.property_type(str(get("property_type_raw") or "")) or
            text.property_type(title),
            furnished=text.furnished(str(get("furnished_raw") or "")),
            pets_allowed=_as_bool(get("pets_allowed")),
            bills_included=_as_bool(get("bills_included")),
            available_from=dates.parse_available_from(str(get("available_from_raw") or "")),
            min_tenancy_months=text.min_tenancy_months(str(get("min_tenancy_raw") or "")),
            deposit_pcm=money.deposit_to_pounds(str(get("deposit_raw") or "")),
            postcode_district=outward,
            title=title or None,
            description=text.clean(str(get("description") or ""), limit=4000),
            is_landlord_direct=True,
            raw=dict(raw.fields),
        )


def _as_bool(value: object) -> bool | None:
    """Keep "not stated" distinct from "no"."""
    return value if isinstance(value, bool) else None
