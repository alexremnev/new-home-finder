from __future__ import annotations

import pathlib

import pytest

from worker.contracts.extraction import ExtractionSchema, FieldRule
from worker.contracts.listing import RawListing
from worker.contracts.source import FetchResult, FetchTask
from worker.extract.engine import apply
from worker.extract.fingerprint import fingerprint
from worker.extract.health import evaluate
from worker.sources.openrent import DETAIL_SCHEMA, OpenRent

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "openrent" / "detail.html"
URL = (
    "https://www.openrent.co.uk/property-to-rent/london/"
    "1-bed-flat-craven-street-wc2n/2981347"
)


@pytest.fixture(scope="module")
def page() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def extracted(page: str) -> dict[str, object]:
    rows = apply(DETAIL_SCHEMA, page, base={"external_id": "2981347", "url": URL})
    assert len(rows) == 1
    return rows[0]


# ── extraction against the real page ──────────────────────────────────────


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("title", "1 Bed Flat, Craven Street, WC2N"),
        # From the table row, not the page header: the header shows the monthly and
        # weekly figures side by side, and a price read without its period is how a
        # rent ends up wrong by a factor of four.
        ("price_raw", "£2,580.00"),
        ("deposit_raw", "£2,580.00"),
        ("available_from_raw", "15 September, 2026"),
        ("min_tenancy_raw", "6 Months"),
        ("furnished_raw", "Furnished"),
        ("epc_rating", "C"),
        ("bedrooms_raw", "1 Bed"),
        ("property_type_raw", "Flat"),
        ("postcode_raw", "WC2N"),
        ("is_landlord_direct", True),
    ],
)
def test_fields_from_the_real_page(
    extracted: dict[str, object], field: str, expected: object
) -> None:
    assert extracted[field] == expected


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("bills_included", True),      # green tick
        ("pets_allowed", False),       # red cross
        ("smokers_allowed", False),
        ("student_friendly", True),
        ("garden", False),
        ("parking", False),
    ],
)
def test_icon_fields(extracted: dict[str, object], field: str, expected: bool) -> None:
    """Yes/no answers are icons, not words. Reading them is the whole point of
    keeping icons in the fixture."""
    assert extracted[field] is expected


def test_description_is_present(extracted: dict[str, object]) -> None:
    description = extracted["description"]
    assert isinstance(description, str)
    assert description.startswith("Charming 1-Bedroom Flat")


def test_label_matching_tolerates_a_tooltip_in_the_label_cell() -> None:
    """A label cell often carries a help button and hidden text beside the label.

    An exact text comparison would miss those rows, so matching is on the start of
    the cell's text.
    """
    html = """<table><tr>
      <td class="fw-medium">Pets Allowed<button>?</button><div>Long help text here</div></td>
      <td><svg class="text-danger"></svg></td>
    </tr></table>"""
    schema = ExtractionSchema(
        strategy="dom", item="table",
        fields={"pets": FieldRule(label="Pets Allowed", true_if=".text-success",
                                  false_if=".text-danger")},
    )
    assert apply(schema, html)[0]["pets"] is False


def test_absent_icon_means_not_stated_not_no() -> None:
    """The third state must survive: an empty value cell is unknown, not False."""
    html = '<table><tr><td class="fw-medium">Pets Allowed</td><td></td></tr></table>'
    schema = ExtractionSchema(
        strategy="dom", item="table",
        fields={"pets": FieldRule(label="Pets Allowed", true_if=".text-success",
                                  false_if=".text-danger")},
    )
    assert apply(schema, html)[0]["pets"] is None


def test_value_is_the_last_cell_of_the_row() -> None:
    """Some rows carry a spacer cell between the label and the value."""
    html = """<table><tr>
      <td class="fw-medium">Rent PCM</td><td></td><td>£1,950.00</td>
    </tr></table>"""
    schema = ExtractionSchema(
        strategy="dom", item="table", fields={"price": FieldRule(label="Rent PCM")}
    )
    assert apply(schema, html)[0]["price"] == "£1,950.00"


def test_missing_label_yields_none_rather_than_raising() -> None:
    html = '<table><tr><td class="fw-medium">Deposit</td><td>£100</td></tr></table>'
    schema = ExtractionSchema(
        strategy="dom", item="table",
        fields={"price": FieldRule(label="Rent PCM"), "deposit": FieldRule(label="Deposit")},
    )
    row = apply(schema, html)[0]
    assert row["price"] is None
    assert row["deposit"] == "£100"


def test_base_values_are_not_overwritten_by_a_missing_field() -> None:
    """An id taken from the URL must survive a page that does not carry it."""
    html = "<table><tr><td>x</td></tr></table>"
    schema = ExtractionSchema(
        strategy="dom", item="table", fields={"external_id": FieldRule(sel=".nope")}
    )
    assert apply(schema, html, base={"external_id": "123"})[0]["external_id"] == "123"


def test_json_strategy() -> None:
    body = '{"props":{"listings":[{"id":7,"price":"£1,950 pcm"},{"id":8,"price":"£900 pcm"}]}}'
    schema = ExtractionSchema(
        strategy="api_json", root="$.props.listings[*]",
        fields={"external_id": FieldRule(path="$.id"), "price_raw": FieldRule(path="$.price")},
    )
    rows = apply(schema, body)
    assert [r["external_id"] for r in rows] == [7, 8]
    assert rows[0]["price_raw"] == "£1,950 pcm"


# ── normalisation of the real page ────────────────────────────────────────


def test_normalise_the_real_page(extracted: dict[str, object]) -> None:
    listing = OpenRent().normalize(RawListing(source_key="openrent", fields=extracted))
    assert listing.price_pcm == 2580
    assert listing.bedrooms == 1
    assert listing.property_type == "flat"
    assert listing.furnished == "furnished"
    assert listing.bills_included is True
    assert listing.pets_allowed is False
    assert listing.available_from is not None
    assert (listing.available_from.year, listing.available_from.month) == (2026, 9)
    assert listing.min_tenancy_months == 6
    assert listing.deposit_pcm == 2580.0
    assert listing.postcode_district == "WC2N"
    assert listing.is_landlord_direct is True


def test_health_gates_pass_on_the_real_page(extracted: dict[str, object]) -> None:
    """The gates must not report a break on a page that extracted cleanly.

    They decide when a schema gets rewritten by a model, so a false alarm costs
    tokens and hides a genuine break behind noise.
    """
    health = evaluate([extracted], OpenRent().fields, min_items=1)
    assert health.verdict == "ok", health.notes
    assert health.fill_rate["price_raw"] == 1.0


# ── fingerprint ───────────────────────────────────────────────────────────


def test_fingerprint_is_stable(page: str) -> None:
    assert fingerprint(page, strategy="dom", item="body") == fingerprint(
        page, strategy="dom", item="body"
    )


def test_fingerprint_ignores_content(page: str) -> None:
    """A different listing must hash the same: otherwise every new listing looks
    like a markup change and triggers a pointless schema repair."""
    other = page.replace("£2,580.00", "£1,100.00").replace("Craven Street", "Brick Lane")
    assert fingerprint(other, strategy="dom", item="body") == fingerprint(
        page, strategy="dom", item="body"
    )


def test_fingerprint_notices_structure() -> None:
    before = '<body><div class="listing"><span class="price">x</span></div></body>'
    after = '<body><div class="listing"><em class="price">x</em></div></body>'
    assert fingerprint(before, strategy="dom", item="body") != fingerprint(
        after, strategy="dom", item="body"
    )


def test_fingerprint_ignores_layout_classes() -> None:
    """Spacing and grid utilities churn without affecting any selector."""
    before = '<body><div class="listing p-3 mt-2 col-md-6"><span>x</span></div></body>'
    after = '<body><div class="listing p-5 mt-0 col-lg-4"><span>x</span></div></body>'
    assert fingerprint(before, strategy="dom", item="body") == fingerprint(
        after, strategy="dom", item="body"
    )


# ── discovery ─────────────────────────────────────────────────────────────

SITEMAP = """<?xml version="1.0"?><urlset>
  <url><loc>https://www.openrent.co.uk/property-to-rent/london/2-bed-flat-street-se16/1</loc></url>
  <url><loc>https://www.openrent.co.uk/property-to-rent/london/studio-deptford-se8/2</loc></url>
  <url><loc>https://www.openrent.co.uk/property-to-rent/london/1-bed-canary-e14/3</loc></url>
  <url><loc>https://www.openrent.co.uk/property-to-rent/london/1-bed-brick-lane-e1/4</loc></url>
  <url><loc>https://www.openrent.co.uk/property-to-rent/reading/room-road-rg30/5</loc></url>
  <url><loc>https://www.openrent.co.uk/about</loc></url>
</urlset>"""


def _result(body: str) -> FetchResult:
    return FetchResult(
        task=FetchTask(source_key="openrent", page_kind="search_list", url="s.xml"),
        status=200, body=body.encode(), fingerprint="x",
    )


def test_expand_applies_the_district_scope_before_any_fetch() -> None:
    """Out-of-scope listings must cost no request. E1 must not admit E14."""
    tasks = list(
        OpenRent().expand(_result(SITEMAP), scope=frozenset({"SE16", "SE8", "E14"}),
                          known_ids=set())
    )
    assert [t.external_id for t in tasks] == ["1", "2", "3"]
    assert all(t.page_kind == "detail" for t in tasks)


def test_expand_skips_listings_already_stored() -> None:
    tasks = list(
        OpenRent().expand(_result(SITEMAP), scope=frozenset({"SE16", "SE8", "E14"}),
                          known_ids={"1", "3"})
    )
    assert [t.external_id for t in tasks] == ["2"]


def test_expand_ignores_non_listing_urls() -> None:
    tasks = list(OpenRent().expand(_result(SITEMAP), scope=frozenset(), known_ids=set()))
    assert all(t.external_id is not None for t in tasks)
    assert "https://www.openrent.co.uk/about" not in [t.url for t in tasks]


def test_discover_reads_the_index_on_a_hot_run_and_children_on_a_sweep() -> None:
    source = OpenRent()
    hot = list(source.discover([], "hot"))
    sweep = list(source.discover([], "sweep"))
    assert len(hot) == 1
    assert len(sweep) > len(hot)
