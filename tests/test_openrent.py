from __future__ import annotations

from datetime import date

from worker.sources.openrent import (
    as_listing,
    as_text,
    listings_in,
    read_slug,
    weigh,
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
            # Accumulated the way the real Stage does, because these end up in
            # job_stages.counters and the admin reads them back.
            self.counters: dict[str, int] = {}

        def set(self, *_: object, **__: object) -> None: ...

        def count(self, name: str, by: int = 1) -> None:
            self.counters[name] = self.counters.get(name, 0) + by

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
    """A connection that answers the two queries the scraper makes."""

    def __init__(self, districts: list[str], settled: list[str] | None = None) -> None:
        self.districts = districts
        self.settled = list(settled or [])
        self.inserted: list[object] = []
        self.images: list[object] = []
        self.rows: list[dict[str, object]] = []
        self.next_id = 0

    # store reads and writes through these three, and nothing here touches a
    # database.
    def execute(self, sql: str, params: object = None) -> "FakeConn":
        self.one: dict[str, object] | None = None
        if "FROM subscriptions" in sql:
            # The districts live subscriptions name — what the scraper now
            # follows instead of an operator's list of coverage.
            self.rows = [{"code": code} for code in self.districts]
        elif "FROM source_sweeps" in sql:
            self.rows = [{"district": code} for code in self.settled]
        elif "INSERT INTO source_sweeps" in sql:
            assert isinstance(params, tuple)
            self.settled.append(str(params[1]))
            self.rows = []
        elif "INSERT INTO listings" in sql:
            self.next_id += 1
            self.inserted.append(params)
            self.one = {"id": self.next_id}
            self.rows = []
        elif "UPDATE listings SET image_url" in sql:
            assert isinstance(params, tuple)
            self.images.append(params[0])
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
    # Narrowed to four words: the matcher compares exact strings, so a filter
    # for "house" has to be answered by a terraced one.
    assert read_slug("3-bed-terraced-house-mallard-avenue-cv10") == ("CV10", 3, "house")
    assert read_slug("2-bed-maisonette-high-street-se16") == ("SE16", 2, "flat")
    assert read_slug("4-bed-bungalow-lane-cv10") == ("CV10", 4, "house")
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

def listings_sitemap(ids: range | list[int], district: str = "se16") -> str:
    return "<urlset>" + "".join(
        f"<url><loc>https://www.openrent.co.uk/property-to-rent/london/"
        f"2-bed-flat-street-{district}/{n}</loc></url>"
        for n in ids
    ) + "</urlset>"

def serving(ids: range | list[int], district: str = "se16") -> object:
    def get(url: str) -> str:
        if "sitemap.xml" in url:
            return INDEX
        if url.endswith(".xml"):
            return listings_sitemap(ids, district)
        return "<p>Rent &#xA3;2,100.00 per month</p>"

    return get

def test_a_first_pass_over_a_district_announces_nothing() -> None:
    from worker.sources.openrent import collect

    # The sitemap has no dates, so a listing seen for the first time may have
    # been on the market since June. Announcing a first pass would send a new
    # subscriber the whole standing market — which is the flood this prevents.
    conn = FakeConn(districts=["SE16"])
    swept = collect(conn, FakeRun(), get=serving(range(25)), pause=0)

    assert len(swept.stored) == 25
    assert swept.announce == []

def test_a_first_pass_under_the_budget_is_still_silent() -> None:
    from worker.sources.openrent import PAGE_BUDGET, collect

    # The rule this replaced asked "did we hit the budget", which said yes to a
    # district of twenty-five and sent every one of them.
    conn = FakeConn(districts=["SE16"])
    swept = collect(conn, FakeRun(), get=serving(range(PAGE_BUDGET - 5)), pause=0)
    assert swept.announce == []

def test_a_district_with_nothing_new_becomes_settled() -> None:
    from worker.sources.openrent import collect

    # Nothing new left means everything on the market there is stored, so from
    # now on anything appearing genuinely appeared after we looked.
    conn = FakeConn(districts=["SE16"])
    collect(conn, FakeRun(), get=serving([]), pause=0)
    assert conn.settled == ["SE16"]

def test_once_settled_a_new_listing_is_announced() -> None:
    from worker.sources.openrent import collect

    conn = FakeConn(districts=["SE16"], settled=["SE16"])
    swept = collect(conn, FakeRun(), get=serving([9001]), pause=0)
    assert len(swept.stored) == 1
    assert swept.announce == swept.stored

def test_settling_one_district_does_not_release_another() -> None:
    from worker.sources.openrent import collect

    # E14 is settled and quiet; SE16 is being read for the first time. The run
    # settles E14 and must not let that make SE16's backlog announceable.
    conn = FakeConn(districts=["SE16", "E14"], settled=["E14"])
    swept = collect(conn, FakeRun(), get=serving(range(12)), pause=0)
    assert len(swept.stored) == 12
    assert swept.announce == []

def test_a_body_is_weighed_in_the_bytes_that_crossed_the_wire() -> None:

    # Measured as UTF-8, not as characters: the page is fetched with
    # Accept-Encoding: identity, so this is what the network actually carried.
    assert weigh("abc") == 3
    assert weigh("£2,100") == 7  # the pound sign is two bytes
    assert weigh("") == 0

def test_every_fetch_is_added_to_the_traffic_counter() -> None:
    from worker.sources.openrent import collect

    # What the System tab shows as the period's volume. All three kinds of
    # fetch count: the index, each child sitemap, and each listing page — the
    # sitemap is the larger half, because it carries no lastmod and so is
    # downloaded whole every run.
    run = FakeRun()
    conn = FakeConn(districts=["SE16"])
    collect(conn, run, get=serving(range(3)), pause=0)

    sitemap = listings_sitemap(range(3))
    page = "<p>Rent &#xA3;2,100.00 per month</p>"
    expected = weigh(INDEX) + weigh(sitemap) + 3 * weigh(page)

    assert run.stages.counters["bytes"] == expected

# The real page's meta tags, in the order OpenRent states them: its own share
# graphic twice, then the photograph of the flat.
METAS = """<meta name="twitter:image" content="https://imagescdn.openrent.co.uk/listings/218791/o_1jsl.JPG">
<meta property="og:image" content="https://staticcdn.openrent.co.uk/images/logos/meta/share-graphic-2.jpg"/>
<meta property="og:image" content="https://imagescdn.openrent.co.uk/listings/218791/o_1jsl.JPG"/>"""

def test_the_portals_own_logo_is_not_taken_for_the_flat() -> None:
    from worker.ingest.photo import image_in

    # Three og:image tags, two of them OpenRent's branding. Taking the first in
    # document order would put their logo in the alert instead of the property.
    reordered = """<meta property="og:image" content="https://staticcdn.openrent.co.uk/images/logos/meta/share-graphic-1.jpg"/>
<meta property="og:image" content="https://imagescdn.openrent.co.uk/listings/218791/o_1jsl.JPG"/>"""
    assert image_in(reordered) == "https://imagescdn.openrent.co.uk/listings/218791/o_1jsl.JPG"
    assert image_in(METAS) == "https://imagescdn.openrent.co.uk/listings/218791/o_1jsl.JPG"

def test_branding_is_better_than_no_picture_at_all() -> None:
    from worker.ingest.photo import image_in

    # When branding is all the page offers, the alert is still about a real
    # flat, so it goes out with a picture rather than without one.
    only_furniture = '<meta property="og:image" content="https://staticcdn.openrent.co.uk/images/logos/meta/share-graphic-1.jpg"/>'
    assert image_in(only_furniture) is not None

def test_the_photograph_is_taken_from_the_page_already_fetched() -> None:
    from worker.sources.openrent import collect

    # The images job cannot do this for OpenRent: it runs on the server, and the
    # server is answered 405. The scraper is holding the page anyway, so the
    # picture costs no request at all.
    def get(url: str) -> str:
        if "sitemap.xml" in url:
            return INDEX
        if url.endswith(".xml"):
            return listings_sitemap([4242])
        return "<p>Rent &#xA3;2,100.00 per month</p>" + METAS

    conn = FakeConn(districts=["SE16"], settled=["SE16"])
    collect(conn, FakeRun(), get=get, pause=0)

    assert conn.images == ["https://imagescdn.openrent.co.uk/listings/218791/o_1jsl.JPG"]

def test_a_page_without_a_picture_is_recorded_as_looked_at() -> None:
    from worker.sources.openrent import collect

    # Written as None rather than skipped: "looked and found nothing" has to be
    # distinguishable from "not looked at", or the server retries it forever and
    # is refused every time.
    conn = FakeConn(districts=["SE16"], settled=["SE16"])
    collect(conn, FakeRun(), get=serving([7]), pause=0)
    assert conn.images == [None]
