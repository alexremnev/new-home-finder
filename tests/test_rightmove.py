"""Rightmove, against pages saved from the live site.

Both fixtures are real: a listing page and a search page for SE16. That matters
more here than for OpenRent, because every selector on this site is either a
`data-testid` or a hashed CSS-module class, and guessing at either produces code
that looks right and extracts nothing.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest

from worker.contracts.listing import RawListing
from worker.contracts.source import FetchResult, FetchTask, SourceLocation
from worker.extract.engine import apply
from worker.extract.health import evaluate
from worker.normalize.geo import outward_from_address
from worker.normalize.money import PriceError, to_pcm
from worker.sources.rightmove import DETAIL_SCHEMA, SEARCH_SCHEMA, Rightmove

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "rightmove"
DETAIL_URL = "https://www.rightmove.co.uk/properties/167381327"


@pytest.fixture(scope="module")
def detail_page() -> str:
    return (FIXTURES / "detail.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def search_page() -> bytes:
    return (FIXTURES / "search.html").read_bytes()


@pytest.fixture(scope="module")
def extracted(detail_page: str) -> dict[str, Any]:
    rows = apply(
        DETAIL_SCHEMA, detail_page, base={"external_id": "167381327", "url": DETAIL_URL}
    )
    assert len(rows) == 1
    return rows[0]


# ── the listing page ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("title", "Fulham Road, London, SW10"),
        ("price_raw", "£2,795 pcm"),
        ("available_from_raw", "Now"),
        ("furnished_raw", "Part furnished"),
        ("property_type_raw", "Apartment"),
        ("bedrooms_raw", "1"),
        ("bathrooms_raw", "1"),
        ("postcode_raw", "SW10"),
        ("epc_rating", "C"),
        ("is_landlord_direct", False),
    ],
)
def test_fields_from_the_real_listing_page(
    extracted: dict[str, Any], field: str, expected: object
) -> None:
    assert extracted[field] == expected


def test_the_deposit_comes_out_without_the_tooltip_prose() -> None:
    """The value cell holds the amount and then a paragraph explaining what a
    deposit is. Without the anchor on the money, the field is an essay."""
    assert "deposit" not in str(DETAIL_SCHEMA.fields["deposit_raw"].regex or "").lower()


def test_the_deposit_is_a_sum_and_nothing_else(extracted: dict[str, Any]) -> None:
    deposit = str(extracted["deposit_raw"])
    assert deposit.startswith("£")
    assert "provides security" not in deposit


def test_the_headline_price_is_the_monthly_one(extracted: dict[str, Any]) -> None:
    """The weekly figure sits directly beneath the monthly one on this page, and a
    selector that drifted onto it would be wrong by a factor of four."""
    assert "pcm" in str(extracted["price_raw"])
    assert "pw" not in str(extracted["price_raw"])


def test_health_gates_pass_on_the_real_page(extracted: dict[str, Any]) -> None:
    health = evaluate([extracted], Rightmove().fields, min_items=1)
    assert health.verdict == "ok", health.notes


# ── normalisation ─────────────────────────────────────────────────────────


def test_normalise_the_real_page(extracted: dict[str, Any]) -> None:
    listing = Rightmove().normalize(RawListing(source_key="rightmove", fields=extracted))
    assert listing.price_pcm == 2795
    assert listing.bedrooms == 1
    assert listing.bathrooms == 1
    assert listing.property_type == "flat"      # "Apartment" is a flat
    assert listing.furnished == "part"
    assert listing.postcode_district == "SW10"
    assert listing.deposit_pcm == 0.0           # this agent asks for none
    assert listing.is_landlord_direct is False
    # Behind a modal on this site, so it stays unknown rather than being guessed.
    assert listing.min_tenancy_months is None


def test_available_now_is_a_date_and_not_a_blank(extracted: dict[str, Any]) -> None:
    """"Now" is an answer. Dropping it would fail an "available before" filter that
    this listing plainly satisfies."""
    listing = Rightmove().normalize(RawListing(source_key="rightmove", fields=extracted))
    assert listing.available_from is not None


def test_a_studio_without_a_bedroom_count_is_zero_not_missing() -> None:
    """The info reel shows a number, and for a studio it shows none."""
    raw = RawListing(
        source_key="rightmove",
        fields={"external_id": "1", "url": DETAIL_URL, "title": "Old Street, London, EC1V",
                "price_raw": "£1,700 pcm", "property_type_raw": "Studio", "postcode_raw": "EC1V"},
    )
    listing = Rightmove().normalize(raw)
    assert listing.bedrooms == 0
    assert listing.property_type == "studio"


def test_a_price_without_a_period_is_refused_rather_than_assumed() -> None:
    """This source always prints the period. A price without one is a markup
    change, and reading it as monthly would be silently wrong for weekly lets."""
    with pytest.raises(PriceError):
        to_pcm("£645", default_period="unknown")
    assert to_pcm("£645 pw", default_period="unknown") == 2795
    # A source that does display bare prices still says so explicitly.
    assert to_pcm("£645", default_period="month") == 645


# ── discovery ─────────────────────────────────────────────────────────────


def _result(body: bytes) -> FetchResult:
    return FetchResult(
        task=FetchTask(source_key="rightmove", page_kind="search_list", url="s.html"),
        status=200, body=body, fingerprint="x",
    )


def test_discovery_asks_for_one_page_per_district_newest_first() -> None:
    source = Rightmove()
    locations = [
        SourceLocation(source_key="rightmove", location_id=1, code="SE16", external_id="se16"),
        SourceLocation(source_key="rightmove", location_id=2, code="E14", external_id="e14"),
    ]
    hot = list(source.discover(locations, "hot"))
    assert len(hot) == 2
    assert "SE16.html" in hot[0].url
    # Newest first is what makes one page enough for a hot run.
    assert "sortType=6" in hot[0].url
    assert "index=0" in hot[0].url

    sweep = list(source.discover(locations, "sweep"))
    assert len(sweep) > len(hot)
    assert any("index=24" in task.url for task in sweep)


def test_every_card_on_the_real_search_page_yields_a_listing(search_page: bytes) -> None:
    tasks = list(
        Rightmove().expand(_result(search_page), scope=frozenset({"SE16"}), known_ids=set())
    )
    assert len(tasks) >= 20
    assert all(task.page_kind == "detail" for task in tasks)
    assert all(task.external_id and task.external_id.isdigit() for task in tasks)
    assert all(task.url == f"https://www.rightmove.co.uk/properties/{task.external_id}"
               for task in tasks)


def test_the_card_selector_matches_each_result_once(search_page: bytes) -> None:
    """The wrapper and the inner container share a testid prefix. Matching both
    would double every row and read as a broken schema two stages later."""
    rows = apply(SEARCH_SCHEMA, search_page)
    ids = [r["external_id"] for r in rows if r.get("external_id")]
    assert len(ids) == len(rows)
    # The page does repeat a listing across cards; that is the site's doing, and
    # the pipeline drops it before spending a request.
    assert len(set(ids)) >= 20


def test_a_district_outside_the_scope_costs_no_request(search_page: bytes) -> None:
    """Every card on this page names SE16 or names no district at all, so a scope
    of E14 leaves only the nameless ones."""
    rows = apply(SEARCH_SCHEMA, search_page)
    nameless = [r for r in rows if outward_from_address(str(r.get("address_raw") or "")) is None]
    tasks = list(
        Rightmove().expand(_result(search_page), scope=frozenset({"E14"}), known_ids=set())
    )
    assert len(tasks) == len(nameless) < 5


def test_an_address_without_a_district_is_unknown_and_not_out_of_scope(
    search_page: bytes,
) -> None:
    """Three cards on this real page give only a street — the agent wrote no
    district. Treating that as "elsewhere" would silently drop listings that are
    in fact in the district the search asked for; the listing page settles it.
    """
    rows = apply(SEARCH_SCHEMA, search_page)
    addresses = [str(r.get("address_raw") or "") for r in rows]
    assert any(outward_from_address(a) is None for a in addresses)

    tasks = list(
        Rightmove().expand(_result(search_page), scope=frozenset({"SE16"}), known_ids=set())
    )
    assert len(tasks) == len(rows)


def test_known_listings_are_not_fetched_again(search_page: bytes) -> None:
    source = Rightmove()
    everything = list(source.expand(_result(search_page), scope=frozenset(), known_ids=set()))
    known = {task.external_id for task in everything[:5] if task.external_id}
    remaining = list(source.expand(_result(search_page), scope=frozenset(), known_ids=known))
    assert known.isdisjoint({task.external_id for task in remaining})


def test_a_listing_url_is_derivable_from_the_id() -> None:
    """Unlike OpenRent, where the slug is unguessable and the URL must come from
    the sitemap."""
    assert Rightmove().listing_url("123").endswith("/properties/123")


def test_the_adapter_registered_itself() -> None:
    from worker.sources import SOURCES

    assert "rightmove" in SOURCES
    assert SOURCES["rightmove"].display_name == "Rightmove"
