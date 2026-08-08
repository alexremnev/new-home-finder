"""Rightmove.

Discovery differs from OpenRent's in a way worth stating, because it decides the
request budget. Rightmove publishes no sitemap of listings, and its JSON search
endpoint is under `/api/`, which `robots.txt` disallows — so discovery reads the
ordinary search page a person would use:

    /property-to-rent/SE16.html?sortType=6&index=0

`sortType=6` is newest first, which is what makes a hot run cheap: the new
listings are on the first page, and there is no need to walk deeper. `index`
pages in twenties for a sweep. The outward code is the URL slug itself, so no
identifier lookup is needed and the district filter costs nothing.

These pages are large — around 2.5 MB — so one per district per run is the whole
discovery budget, and conditional requests do the rest.

A listing URL is `/properties/<id>`, which carries no district. Scope is therefore
applied to the address on the card, before any listing page is requested.

Two fields the listing page does not carry, and their consequences:

  * minimum tenancy sits behind a modal, so it stays unknown. A subscription that
    sets `min_tenancy_max_months` will not match Rightmove listings — correct
    under the matching rule that a set criterion requires a known value, and
    better than guessing twelve months;
  * everything here is agent-listed, so `is_landlord_direct` is a constant false
    rather than an absent value. Someone filtering for landlord-direct should get
    nothing from this source, which is the truth about it.
"""

from __future__ import annotations

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
from worker.normalize.geo import normalize_outward, outward_from_address, split_postcode

BASE = "https://www.rightmove.co.uk"
SEARCH = f"{BASE}/property-to-rent/{{code}}.html?sortType=6&index={{index}}"

# Pages of results to read. A hot run wants only what is new, and the sort order
# puts that on the first page; a sweep walks further to notice anything missed.
HOT_PAGES = 1
SWEEP_PAGES = 4
PAGE_SIZE = 24

# The card wrapper. `propertyCard-vrt-N` is one per result; the inner
# `propertyCard-N` would match a second time and double every row.
CARD = 'div[data-testid^="propertyCard-vrt-"]'

# The detail page's own heading, marked up as a postal address.
ADDRESS = 'h1[itemprop="streetAddress"]'


FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(name="external_id", required=True, kind="str",
              hint="listing id; from the card anchor or the URL, not from the page body"),
    FieldSpec(name="url", required=True, kind="str", hint="canonical listing URL"),
    FieldSpec(name="title", required=True, kind="str",
              hint="address heading, e.g. 'Fulham Road, London, SW10'"),
    FieldSpec(name="price_raw", required=True, kind="str",
              hint="headline rent with its period, e.g. '£2,795 pcm'"),
    FieldSpec(name="bedrooms_raw", required=True, kind="str",
              hint="bedroom count from the info reel; a studio shows no count"),
    FieldSpec(name="bathrooms_raw", required=False, kind="str", hint="bathroom count"),
    FieldSpec(name="property_type_raw", required=False, kind="str",
              hint="apartment, flat, house, maisonette, studio"),
    FieldSpec(name="postcode_raw", required=False, kind="str",
              hint="outward code at the end of the address heading"),
    FieldSpec(name="deposit_raw", required=False, kind="str",
              hint="deposit as displayed; the cell also holds a tooltip"),
    FieldSpec(name="available_from_raw", required=False, kind="str",
              hint="'Let available date', e.g. 'Now' or '01/09/2026'"),
    FieldSpec(name="furnished_raw", required=False, kind="str",
              hint="'Furnish type', e.g. 'Part furnished'"),
    FieldSpec(name="epc_rating", required=False, kind="str",
              hint="EPC band, when the agent states it among the key features"),
    FieldSpec(name="is_landlord_direct", required=False, kind="bool",
              hint="always false: every listing here comes through an agent"),
)


# Discovery reads only what scoping needs. Price and bedrooms are on the card too,
# but taking them from two places means two schemas to keep true at once, and the
# listing page is the one that also carries the deposit and the availability date.
SEARCH_SCHEMA = ExtractionSchema(
    strategy="dom",
    item=CARD,
    fields={
        "external_id": FieldRule(sel="a[id^=prop]", attr="id", regex=r"prop(\d+)"),
        "address_raw": FieldRule(sel="address"),
    },
)

DETAIL_SCHEMA = ExtractionSchema(
    strategy="dom",
    item="body",
    fields={
        "title": FieldRule(sel=ADDRESS),
        # The weekly figure is nested *inside* the monthly one, along with a
        # tooltip paragraph, so the container's text reads
        # "£2,795 pcm £645 pw The amount per month or week…". Taking that text
        # whole makes the period detector see "pw" and quadruple the rent — the
        # exact silent four-fold error this field is most prone to. The regex takes
        # the first price that states its own period, and a markup change that
        # removes the period leaves the field empty, which the health gate reports.
        "price_raw": FieldRule(sel='[data-testid="primaryPrice"]',
                               regex=r"(£\s*[\d,]+(?:\.\d{2})?\s*p(?:cm|w))"),
        "available_from_raw": FieldRule(label="Let available date"),
        # The value cell holds the amount and then a tooltip paragraph about what
        # a deposit is. Anchoring on the money keeps the prose out.
        "deposit_raw": FieldRule(label="Deposit", regex=r"(£\s*[\d,]+)"),
        "furnished_raw": FieldRule(label="Furnish type"),
        "property_type_raw": FieldRule(sel='[data-testid="info-reel-PROPERTY_TYPE-text"]'),
        "bedrooms_raw": FieldRule(sel='[data-testid="info-reel-BEDROOMS-text"]'),
        "bathrooms_raw": FieldRule(sel='[data-testid="info-reel-BATHROOMS-text"]'),
        "postcode_raw": FieldRule(sel=ADDRESS, regex=r"([A-Za-z]{1,2}\d{1,2}[A-Za-z]?)\s*$"),
        # Stated as free text among the key features when it is stated at all.
        "epc_rating": FieldRule(sel='[data-testid="keyFeatures"]',
                                regex=r"EPC(?:\s+Rating)?\s*[=:]?\s*([A-G])\b"),
        "is_landlord_direct": FieldRule(const=False),
    },
)


@register
class Rightmove:
    key = "rightmove"
    display_name = "Rightmove"
    preferred: tuple[str, ...] = ("dom",)
    fields = FIELDS
    detail_schema = DETAIL_SCHEMA
    search_schema = SEARCH_SCHEMA

    # ── discovery ─────────────────────────────────────────────────────────

    def discover(self, locations: list[SourceLocation], mode: Mode) -> Iterator[FetchTask]:
        """One search page per district, newest first.

        The district is in the URL, so nothing out of scope is ever requested and
        the cost of a run is set by how many districts are enabled rather than by
        how much of the country is on the market.
        """
        pages = SWEEP_PAGES if mode == "sweep" else HOT_PAGES
        for location in locations:
            for page in range(pages):
                yield FetchTask(
                    source_key=self.key,
                    page_kind="search_list",
                    url=SEARCH.format(code=location.code.upper(), index=page * PAGE_SIZE),
                )

    def expand(
        self, result: FetchResult, *, scope: frozenset[str], known_ids: set[str]
    ) -> Iterator[FetchTask]:
        """Read the cards and queue the listings worth a request.

        Applying the scope here rather than after fetching is the point: the
        address on the card is the only place the district appears before the
        listing page is loaded.
        """
        from worker.extract.engine import apply  # local: contracts must not import pipeline

        for card in apply(SEARCH_SCHEMA, result.body):
            external_id = card.get("external_id")
            if not external_id:
                continue
            external_id = str(external_id)
            if external_id in known_ids:
                continue
            if scope:
                outward = outward_from_address(str(card.get("address_raw") or ""))
                # An address that names no district counts as unknown, not as out
                # of scope, so a change in how addresses are written costs extra
                # fetches rather than silently dropping every listing. The search
                # URL is already district-scoped, so those extras stay few.
                if outward is not None and outward not in scope:
                    continue
            yield FetchTask(
                source_key=self.key,
                page_kind="detail",
                url=self.listing_url(external_id),
                external_id=external_id,
            )

    def listing_url(self, external_id: str) -> str:
        """Unlike OpenRent's, a Rightmove URL is derivable from the id alone."""
        return f"{BASE}/properties/{external_id}"

    # ── normalisation ─────────────────────────────────────────────────────

    def normalize(self, raw: RawListing) -> Listing:
        get = raw.get
        title = text.clean(str(get("title") or "")) or ""

        outward = normalize_outward(str(get("postcode_raw") or "")) or split_postcode(title)[0]
        # The info reel shows a number, not "2 Bed", and a studio shows no number
        # at all — so an absent count falls back to the property type naming one.
        bedrooms = text.bedrooms(str(get("bedrooms_raw") or ""))
        property_type = text.property_type(str(get("property_type_raw") or ""))
        if bedrooms is None:
            bedrooms = 0 if property_type == "studio" else None

        return Listing(
            source_key=self.key,
            external_id=str(get("external_id")),
            url=str(get("url")),
            # No default period: the displayed price states it, and guessing here
            # is how a weekly rent becomes a monthly one.
            price_pcm=money.to_pcm(str(get("price_raw")), default_period="unknown"),
            bedrooms=bedrooms if bedrooms is not None else 0,
            bathrooms=text.bedrooms(str(get("bathrooms_raw") or "")),
            property_type=property_type,
            furnished=text.furnished(str(get("furnished_raw") or "")),
            available_from=dates.parse_available_from(str(get("available_from_raw") or "")),
            deposit_pcm=money.deposit_to_pounds(str(get("deposit_raw") or "")),
            postcode_district=outward,
            title=title or None,
            # Not "unknown": the source is agents only, and saying so is the truth
            # about it rather than an absent field.
            is_landlord_direct=False,
            raw=dict(raw.fields),
        )
