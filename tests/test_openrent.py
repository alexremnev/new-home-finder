from __future__ import annotations

from datetime import date

from worker.sources.openrent import (
    as_listing,
    as_text,
    listings_in,
    read_slug,
    when,
)

# The sitemap shape confirmed on 21 September 2026: one loc per listing, no
# lastmod, and the whole country in one file.
SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset>
 <url><loc>https://www.openrent.co.uk/property-to-rent/london/2-bed-flat-rotherhithe-street-se16/1234567</loc></url>
 <url><loc>https://www.openrent.co.uk/property-to-rent/doncaster/room-in-a-shared-house-sunningdale-drive-dn12/65053</loc></url>
 <url><loc>https://www.openrent.co.uk/property-to-rent/london/studio-craven-street-wc2n/777</loc></url>
 <url><loc>https://www.openrent.co.uk/about/how-it-works</loc></url>
</urlset>"""

# The page shapes confirmed on a live listing: the rent stated twice, the
# deposit in a sentence, the postcode only inside a link, and the details in
# blocks whose markup is not worth anchoring on.
PAGE = """<html><head><title>x</title>
<script>var noise = "£9,999.00 per month";</script>
<style>.a{content:"£1.00 p/m"}</style></head>
<body>
 <h1>2 Bed Flat, Rotherhithe Street, SE16</h1>
 <div class="pt-3"><span>£2,100.00 p/m</span></div>
 <a href="/comparebroadband?postCode=SE16%204TH">Compare broadband</a>
 <dl><dt>Rent</dt><dd>£2,100.00 per month (£484.62 per week)</dd></dl>
 <p>Deposit / Bond is £2,423.00</p>
 <ul><li>2 bedrooms</li><li>1 bathrooms</li></ul>
 <div>Furnishing <span>Unfurnished</span></div>
 <div>Available From <span>1 October 2026</span></div>
 <div>Minimum Tenancy <span>6 Months</span></div>
</body></html>"""

INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex>
 <sitemap><loc>https://www.openrent.co.uk/sitemap-static.xml</loc></sitemap>
 <sitemap><loc>https://www.openrent.co.uk/sitemap-listings-1.xml</loc></sitemap>
</sitemapindex>"""

class FakeRun:

    # Enough of a Run for the stage to be entered and counted against.
    class Stage:
        def __init__(self) -> None:
            self.degraded: list[str] = []

        def set(self, *_: object, **__: object) -> None: ...
        def count(self, *_: object, **__: object) -> None: ...
        def log(self, *_: object, **__: object) -> None: ...
        def degrade(self, why: str) -> None:
            self.degraded.append(why)

    def __init__(self) -> None:
        self.stages = self.Stage()

    def stage(self, *_: object, **__: object) -> object:
        run = self

        class Held:
            def __enter__(self) -> object:
                return run.stages

            def __exit__(self, *_: object) -> bool:
                return False

        return Held()

class FakeConn:

    def __init__(self, districts: list[str]) -> None:
        self.districts = districts
        self.inserted: list[object] = []
        self.rows: list[dict[str, object]] = []
        self.next_id = 0

    # store reads and writes through these three, and nothing here touches a
    # database.
    def execute(self, sql: str, params: object = None) -> "FakeConn":
        self.one: dict[str, object] | None = None
        if "source_locations" in sql:
            self.rows = [{"code": code} for code in self.districts]
        elif "INSERT INTO listings" in sql:
            self.next_id += 1
            self.inserted.append(params)
            self.one = {"id": self.next_id}
            self.rows = []
        else:
            self.rows = []
        return self

    def fetchall(self) -> list[dict[str, object]]:
        return self.rows

    def fetchone(self) -> dict[str, object] | None:
        return self.one

def only(district: str = "SE16") -> object:
    return [one for one in listings_in(SITEMAP) if one.district == district][0]

def test_a_script_or_style_never_becomes_text() -> None:

    # The page states a plausible rent inside a script. Stripping tags without
    # dropping their contents would take that as the price.
    text = as_text(PAGE)
    assert "9,999" not in text
    assert "£1.00" not in text
    assert "2,100.00 per month" in text

def test_only_listing_urls_are_taken_from_the_sitemap() -> None:
    found = listings_in(SITEMAP)
    assert [one.external_id for one in found] == ["1234567", "65053", "777"]
    # The marketing page has no id and no outward code, so it is not a listing.
    assert all("how-it-works" not in one.url for one in found)

def test_the_district_comes_from_the_url_not_the_page() -> None:

    # This is what makes a nationwide sitemap affordable: the filter is applied
    # before any page is fetched.
    assert {one.district for one in listings_in(SITEMAP)} == {"SE16", "DN12", "WC2N"}

def test_the_slug_gives_rooms_and_type() -> None:
    assert read_slug("2-bed-flat-rotherhithe-street-se16") == ("SE16", 2, "flat")
    assert read_slug("3-bed-terraced-house-mallard-avenue-cv10") == (
        "CV10", 3, "terraced house",
    )
    # A room in somebody else's flat: the number is not a bedroom count.
    assert read_slug("room-in-a-shared-house-beresford-road-dn12") == ("DN12", 1, "room")
    assert read_slug("studio-craven-street-wc2n") == ("WC2N", 0, "studio")

def test_a_slug_without_an_outward_code_is_refused() -> None:
    # Better to skip one listing than to file it under a district it is not in.
    assert read_slug("2-bed-flat-somewhere-unknown") is None

def test_a_page_becomes_a_listing() -> None:
    listing = as_listing(only(), PAGE)
    assert listing is not None
    assert listing.source_key == "openrent"
    assert listing.external_id == "1234567"
    assert listing.price_pcm == 2100
    assert listing.bedrooms == 2
    assert listing.bathrooms == 1
    assert listing.deposit_pcm == 2423.0
    assert listing.furnished == "unfurnished"
    assert listing.available_from == date(2026, 10, 1)
    assert listing.min_tenancy_months == 6
    assert listing.postcode_district == "SE16"
    # Only available inside a link, so it is read before the tags are stripped.
    assert listing.postcode == "SE16 4TH"
    # Every listing on this site is let by its owner.
    assert listing.is_landlord_direct is True

def test_without_a_price_nothing_is_stored() -> None:

    # There is nothing to match on, so a half-formed listing is worse than none.
    assert as_listing(only(), "<html><body>Under offer</body></html>") is None

def test_a_price_outside_the_believable_range_is_refused() -> None:
    # The contract caps these anyway; catching it here keeps the reason legible.
    assert as_listing(only(), "<p>Rent £4.00 per month</p>") is None
    assert as_listing(only(), "<p>Rent £400,000.00 per month</p>") is None

def test_part_furnished_is_not_read_as_furnished() -> None:

    # "furnished" is a substring of both other answers, so order decides.
    page = "<p>Rent £2,100.00 per month</p><div>Part furnished</div>"
    listing = as_listing(only(), page)
    assert listing is not None and listing.furnished == "part"

def test_a_missing_answer_stays_unknown_rather_than_no() -> None:
    listing = as_listing(only(), "<p>Rent £2,100.00 per month</p>")
    assert listing is not None
    assert listing.furnished == "unknown"
    assert listing.bathrooms is None
    assert listing.available_from is None
    # Not stated is a third answer, and the matcher treats it as "matches".
    assert listing.bills_included is None

def test_today_is_a_date() -> None:
    assert when("Today") == date.today()
    assert when("1 October 2026") == date(2026, 10, 1)
    assert when("whenever") is None

def test_a_run_of_refusals_stops_the_run() -> None:
    import urllib.error

    from worker.sources.openrent import REFUSALS_ALLOWED, collect

    # A site answering 405 to everything is declining, not having a bad moment.
    # Spending the whole budget to learn that once every fifteen minutes is both
    # useless and rude.
    asked: list[str] = []

    def refusing(url: str) -> str:
        asked.append(url)
        if url.endswith(".xml"):
            return SITEMAP if "listings" in url else INDEX
        raise urllib.error.HTTPError(url, 405, "Not Allowed", {}, None)  # type: ignore[arg-type]

    conn = FakeConn(districts=["SE16", "WC2N", "DN12"])
    swept = collect(conn, FakeRun(), get=refusing, pause=0)
    assert swept.stored == []

    pages = [u for u in asked if not u.endswith(".xml")]
    assert len(pages) == REFUSALS_ALLOWED, pages
    assert conn.inserted == []

def test_the_pound_sign_arrives_as_an_entity() -> None:

    # Most of this site writes "&#xA3;" rather than "£". Matching the literal
    # character found nothing on pages that plainly showed a price — 28 of 40 in
    # the first real run.
    page = (
        "<title>London - 1 Bed Flat, Courtfield Road, SW7 - To Rent Now for "
        "&#xA3;3,141.67 p/m</title><body>&#xA3;3,141.67 p/m</body>"
    )
    listing = as_listing(only(), page)
    assert listing is not None and listing.price_pcm == 3142

def test_the_monthly_figure_wins_over_a_weekly_one() -> None:

    # A weekly-priced listing states the month in brackets, and that is the
    # number a monthly filter has to compare against.
    page = "<p>&#xA3;2,950pw (&#xA3;12,783 per month)</p>"
    listing = as_listing(only(), page)
    assert listing is not None and listing.price_pcm == 12783

def test_an_escaped_tag_cannot_become_a_tag() -> None:

    # Entities are decoded after the tags are stripped, not before, or this
    # would smuggle a script past the stripping.
    text = as_text("<p>&lt;script&gt;alert(1)&lt;/script&gt; Rent &#xA3;2,100.00 per month</p>")
    assert "<script>" in text  # as visible text, which is harmless
    assert "£2,100.00" in text

def test_a_sweep_that_hit_the_budget_is_not_caught_up() -> None:
    from worker.sources.openrent import PAGE_BUDGET, collect

    # More new listings than one run can read means the standing market is still
    # being worked through, and none of it should be announced as new.
    many = "".join(
        f"<url><loc>https://www.openrent.co.uk/property-to-rent/london/"
        f"2-bed-flat-street-se16/{n}</loc></url>"
        for n in range(PAGE_BUDGET + 5)
    )

    def serving(url: str) -> str:
        if url.endswith(".xml"):
            return INDEX if "sitemap.xml" in url else f"<urlset>{many}</urlset>"
        return "<p>Rent &#xA3;2,100.00 per month</p>"

    swept = collect(FakeConn(districts=["SE16"]), FakeRun(), get=serving, pause=0)
    assert len(swept.stored) == PAGE_BUDGET
    assert swept.caught_up is False
