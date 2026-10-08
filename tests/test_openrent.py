"""The OpenRent reader, against a page the real site served.

`tests/fixtures/openrent_e14_search.html` is trimmed from a genuine E14 search
page taken on 26 September 2026: the first two rendered cards, and the
`PROPERTYIDS` array that is the whole point of reading this page — it lists
every id the search matched, not only the twenty shown.

The second half of the file is the page parser, which has no fixture: the
shapes it reads were confirmed against live listing pages and are written out
here as the smallest markup that still carries them.

Nothing here touches the network.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, date, datetime

from worker.sources.fetch import Reply
from worker.sources.openrent import (
    Found,
    OpenRent,
    address_in,
    as_listing,
    as_text,
    how_many,
    ids_in,
    images_in,
    paths_in,
    place_in_slug,
    read_slug,
    search_url,
    short_url,
    slug_from_path,
    slugs_in,
    when,
)
from worker.sources.sweep import Memory

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "openrent_e14_search.html"
PAGE = FIXTURE.read_text(encoding="utf-8")


# ── the id list, which is why this reader exists ─────────────────────────

def test_every_id_in_the_district_comes_from_one_page() -> None:
    # The sitemap reader this replaced downloaded the whole country to answer
    # this — about 950MB a day. This page answers it in 92KB, and for the
    # twenty rendered cards it throws in the rent and a photograph as well.
    ids = ids_in(PAGE)
    assert len(ids) == 8
    assert all(one.isdigit() for one in ids)


def test_the_page_states_its_own_count_to_check_against() -> None:
    # `NUMBEROFPROPERTIES` beside the array. If the two ever disagree the page
    # shape has moved, and the reader says so rather than trusting it.
    assert how_many(PAGE) == len(ids_in(PAGE))


def test_a_page_without_the_array_yields_nothing_rather_than_raising() -> None:
    assert ids_in("<html><body>maintenance</body></html>") == []
    assert how_many("<html></html>") is None


# ── the cards ────────────────────────────────────────────────────────────

def test_the_rendered_cards_give_their_slugs() -> None:
    slugs = slugs_in(PAGE)
    assert len(slugs) == 2
    for listing_id, slug in slugs.items():
        assert listing_id.isdigit()
        # The slug ends with the outward code, which is what makes the
        # district readable without fetching anything.
        assert slug.lower().endswith(("e14", "e1", "e3"))


def test_a_card_picture_belongs_to_its_own_listing() -> None:
    # The first version of this matched forwards from `data-listing-id` to the
    # next `src`, which crossed card boundaries: listing 3030969 came back
    # holding listing 284912's photograph, and the alert would have shown the
    # wrong flat. The url carries its own id, so there is nothing to correlate.
    pictures = images_in(PAGE)
    assert pictures
    for listing_id, url in pictures.items():
        assert f"/listings/{listing_id}/" in url
        # Protocol-relative in the markup, which no messenger would follow.
        assert url.startswith("https://imagescdn.openrent.co.uk/")


def test_a_listing_with_no_card_picture_is_simply_absent() -> None:
    # Most of a district is never rendered, and the ones that are can be lazy
    # loaded. Absent is right; a placeholder would not be.
    pictures = images_in(PAGE)
    missing = [one for one in ids_in(PAGE) if one not in pictures]
    assert missing, "the fixture should include ids with no rendered card"


def test_the_card_paths_are_full_listing_paths() -> None:
    for listing_id, path in paths_in(PAGE).items():
        assert path.startswith("/property-to-rent/")
        assert path.endswith(f"/{listing_id}")


# ── the urls ─────────────────────────────────────────────────────────────

def test_the_search_url_is_the_district_page() -> None:
    assert search_url("E14") == "https://www.openrent.co.uk/properties-to-rent/e14"
    assert search_url("se16").endswith("/se16")


def test_a_bare_id_url_is_what_reveals_the_slug() -> None:
    # It answers 301 with the full slug path in `Location`, about 3KB of
    # headers against a 300KB page. See the module note.
    assert short_url("2937375") == "https://www.openrent.co.uk/2937375"


def test_a_slug_is_read_out_of_a_redirect_location() -> None:
    where = "/property-to-rent/london/room-in-a-shared-flat-willis-house-e14/2937375"
    assert slug_from_path(where) == "room-in-a-shared-flat-willis-house-e14"
    assert slug_from_path("/nonsense") is None


# ── the sweep ────────────────────────────────────────────────────────────

# Enough of a listing page for `as_listing`: it needs a rent stated per month,
# and reads everything else as optional.
DETAIL = (
    "<html><body><h1>2 Bed Flat, Hale Street, E14</h1>"
    "<p>Rent &#xA3;2,100.00 per month</p><p>1 bathrooms</p>"
    "<p>Deposit / Bond is &#xA3;2,100.00</p><p>Unfurnished</p>"
    "<a href='?postCode=E14%200BX'>broadband</a></body></html>"
)


class Fake:
    """A fetcher that serves the fixture and canned redirects."""

    def __init__(
        self,
        page: str,
        *,
        redirects: dict[str, str] | None = None,
        detail: str = DETAIL,
        refuses: bool = False,
    ) -> None:
        self.page = page
        self.detail = detail
        self.redirects = redirects or {}
        self.refuses = refuses
        self.got: list[str] = []
        self.headed: list[str] = []

    def get(self, url: str, **_: object) -> Reply:
        self.got.append(url)
        # A listing page and a search page are different documents, and a test
        # must not be able to pass by parsing one as the other.
        body = self.detail if "/property-to-rent/" in url else self.page
        return Reply(status=200, body=body, wire=1000, impersonated="chrome124")

    def head(self, url: str) -> tuple[int, str | None]:
        self.headed.append(url)
        if self.refuses:
            # 405 is what OpenRent actually answers this server — see the note
            # on `Fetcher.head`. It means "we could not ask", not "there is no
            # slug", and the two have to behave differently.
            return (405, None)
        where = self.redirects.get(url.rsplit("/", 1)[-1])
        return (301, where) if where else (404, None)


class Quiet:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.said: list[str] = []

    def log(self, level: str, message: str, **_: object) -> None:
        self.said.append(f"{level}: {message}")

    def count(self, name: str, delta: int = 1) -> None:
        self.counts[name] = self.counts.get(name, 0) + delta

    def set(self, *_: object, **__: object) -> None: ...
    def degrade(self, *_: object, **__: object) -> None: ...


def remember(
    stored: object = (), resolved: dict[str, str | None] | None = None
) -> tuple[Memory, dict[str, str | None]]:
    """A Memory over fixed answers, plus the dict it writes what it learned to."""

    written: dict[str, str | None] = {}
    return (
        Memory(
            stored=lambda ids: set(stored),  # type: ignore[arg-type]
            resolved=lambda ids: dict(resolved or {}),
            remember=written.update,
        ),
        written,
    )


# `since` is the district's settled_at for an undated portal, so a real value
# means "already read through" and None means "never".
SETTLED = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def test_nothing_is_fetched_for_ids_we_already_have() -> None:
    # The steady state, and the saving that matters: a district is hundreds of
    # ids and almost all of them are already stored. Resolving them again would
    # be the most expensive way to learn nothing.
    ids = ids_in(PAGE)
    fetch = Fake(PAGE)
    stage = Quiet()
    mem, _ = remember(stored=ids)
    harvest = OpenRent().harvest("E14", fetch, stage, SETTLED, mem)  # type: ignore[arg-type]

    assert harvest.caught == []
    assert harvest.complete is True
    # One page for the district, and not a single request beyond it.
    assert len(fetch.got) == 1
    assert fetch.headed == []
    assert stage.counts["already_known"] == len(ids)


def test_an_id_already_known_to_be_elsewhere_costs_nothing() -> None:
    # The whole point of 0053. OpenRent's district page is a two-kilometre
    # radius, so about a third of it belongs to a neighbouring district. Those
    # are correctly never stored — and before this they were therefore looked
    # up again on every single run, for ever.
    ids = ids_in(PAGE)
    fetch = Fake(PAGE)
    stage = Quiet()
    mem, _ = remember(resolved={one: "SE16" for one in ids})
    harvest = OpenRent().harvest("E14", fetch, stage, SETTLED, mem)  # type: ignore[arg-type]

    assert harvest.caught == []
    assert harvest.complete is True
    assert fetch.headed == [], "a resolved id must not be resolved again"
    assert stage.counts["already_resolved"] == len(ids)


def test_what_a_pass_resolved_is_written_down() -> None:
    ids = ids_in(PAGE)
    carded = set(slugs_in(PAGE))
    elsewhere = next(one for one in ids if one not in carded)
    fetch = Fake(
        PAGE,
        redirects={
            elsewhere: f"/property-to-rent/london/2-bed-flat-somewhere-se16/{elsewhere}"
        },
    )
    mem, written = remember(stored={one for one in ids if one != elsewhere})
    OpenRent().harvest("E14", fetch, Quiet(), SETTLED, mem)  # type: ignore[arg-type]

    # Its real district, not the one we searched — that is what makes it
    # skippable next time.
    assert written == {elsewhere: "SE16"}


def test_a_district_never_read_through_costs_no_listing_pages() -> None:
    # Nothing found in an unsettled district will be announced whatever we do,
    # so a 300KB listing page would be spent on a listing nobody is ever told
    # about. Resolving the id is enough to stop it looking new tomorrow.
    #
    # This is what makes the first pass affordable, and what was wrong before:
    # the district could not be read through within one run's budget, so it
    # never settled, so it never announced anything at all.
    ids = ids_in(PAGE)
    fetch = Fake(
        PAGE,
        redirects={
            one: f"/property-to-rent/london/1-bed-flat-somewhere-e14/{one}"
            for one in ids
        },
    )
    stage = Quiet()
    mem, written = remember()
    harvest = OpenRent().harvest("E14", fetch, stage, None, mem)  # type: ignore[arg-type]

    assert harvest.complete is True, "so that the engine can settle it"
    assert harvest.caught == [], "nothing stored, because nothing would be sent"
    # One request, the search page. Not a single redirect either: resolving an
    # id in a district that cannot announce anything buys nothing, and asking
    # about 330 of them is what exhausted the run's budget and left the
    # district unsettled for ever. The ids are written down instead, which is
    # all the next run needs from this one.
    assert [one for one in fetch.got if "/property-to-rent/" in one] == []
    assert fetch.headed == []
    assert stage.counts["backfilled"] == len(ids)
    assert set(written) == set(ids)


def test_a_listing_outside_the_district_is_not_stored_under_it() -> None:
    # `var SEARCHRADIUS = 2` — the district page is a two-kilometre radius, so
    # about a third of what it returns is somewhere else. The slug decides,
    # not the search.
    #
    # The id has to be one the page did not render: a rendered card already
    # carries its slug, so no redirect is involved at all.
    ids = ids_in(PAGE)
    carded = set(slugs_in(PAGE))
    elsewhere = next(one for one in ids if one not in carded)
    fetch = Fake(
        PAGE,
        redirects={
            elsewhere: f"/property-to-rent/london/2-bed-flat-somewhere-se16/{elsewhere}"
        },
    )
    stage = Quiet()
    mem, _ = remember(stored={one for one in ids if one != elsewhere})
    harvest = OpenRent().harvest("E14", fetch, stage, SETTLED, mem)  # type: ignore[arg-type]

    assert harvest.caught == []
    assert stage.counts.get("outside_district") == 1
    # The redirect was asked, the 300KB listing page was not.
    assert len(fetch.headed) == 1
    assert len(fetch.got) == 1


def test_an_id_with_no_redirect_is_written_off_rather_than_re_asked() -> None:
    # OpenRent answered and there is no slug behind that id, so there is
    # nothing to come back for: the district still counts as read through, and
    # the id is remembered so it is not asked about on every run for ever.
    # Not remembering it is most of why this reader never settled a district.
    ids = ids_in(PAGE)
    carded = set(slugs_in(PAGE))
    unrendered = next(one for one in ids if one not in carded)
    fetch = Fake(PAGE)  # every redirect answers 404
    stage = Quiet()
    mem, written = remember(stored={one for one in ids if one != unrendered})
    harvest = OpenRent().harvest("E14", fetch, stage, SETTLED, mem)  # type: ignore[arg-type]

    assert harvest.complete is True
    assert stage.counts.get("no_slug", 0) >= 1
    assert written.get(unrendered) is None and unrendered in written


def test_a_refused_lookup_leaves_the_district_incomplete() -> None:
    # The other half of the same branch, and the one that matters on this
    # server: 405 means we could not ask. Nothing is learned, the district is
    # not settled on the strength of it, and the id comes round again.
    ids = ids_in(PAGE)
    carded = set(slugs_in(PAGE))
    unrendered = next(one for one in ids if one not in carded)
    fetch = Fake(PAGE, refuses=True)
    stage = Quiet()
    mem, written = remember(stored={one for one in ids if one != unrendered})
    harvest = OpenRent().harvest("E14", fetch, stage, SETTLED, mem)  # type: ignore[arg-type]

    assert harvest.complete is False
    assert stage.counts.get("slug_refused", 0) >= 1
    assert unrendered not in written, "a refusal teaches us nothing"


def test_pages_counts_pages_and_not_requests() -> None:
    # It counted the search page, every redirect and every listing page, so a
    # run that spent its slug budget reported 439 "pages" and the admin's
    # pages-per-run chart meant nothing.
    ids = ids_in(PAGE)
    carded = set(slugs_in(PAGE))
    unrendered = next(one for one in ids if one not in carded)
    fetch = Fake(
        PAGE,
        redirects={
            unrendered: f"/property-to-rent/london/1-bed-flat-somewhere-e14/{unrendered}"
        },
    )
    stage = Quiet()
    mem, _ = remember(stored={one for one in ids if one != unrendered})
    harvest = OpenRent().harvest("E14", fetch, stage, SETTLED, mem)  # type: ignore[arg-type]

    assert harvest.pages == 1
    assert stage.counts.get("slug_lookups") == 1
    assert stage.counts.get("detail_pages") == 1


def test_the_detail_budget_stops_a_run_rather_than_the_district() -> None:
    # A settled district that produces more new listings than one run's budget
    # takes a slice and reports itself incomplete, so the rest is read next
    # time rather than skipped.
    ids = ids_in(PAGE)
    fetch = Fake(
        PAGE,
        redirects={
            one: f"/property-to-rent/london/1-bed-flat-somewhere-e14/{one}"
            for one in ids
        },
    )
    stage = Quiet()
    mem, _ = remember()
    harvest = OpenRent(detail_budget=0).harvest(
        "E14", fetch, stage, SETTLED, mem  # type: ignore[arg-type]
    )

    assert harvest.complete is False
    assert harvest.caught == []
    # No listing page was fetched: the budget was spent before the first one.
    assert [one for one in fetch.got if "/property-to-rent/" in one] == []


def test_a_stored_listing_carries_this_readers_own_source_key() -> None:
    # The reader's key is passed to the parser rather than left to the
    # parser's own default. The two agree today, so this is asked of a reader
    # built with a different one — which is the only way the question has an
    # answer, and the question is worth keeping: a reader that stored under
    # one key while asking "have I seen this id?" under another found every id
    # new on every run, re-fetched the same pages every twenty minutes,
    # exhausted its budget, settled no district and sent nobody anything. One
    # wrong constant, and that was the whole of it. See 0053.
    ids = ids_in(PAGE)
    one = ids[0]
    redirects = {one: f"/property-to-rent/london/1-bed-flat-hale-street-e14/{one}"}
    stored = {other for other in ids if other != one}

    fetch = Fake(PAGE, redirects=redirects)
    mem, _ = remember(stored=stored)
    harvest = OpenRent().harvest("E14", fetch, Quiet(), SETTLED, mem)  # type: ignore[arg-type]
    assert len(harvest.caught) == 1
    assert harvest.caught[0].listing.source_key == "openrent"

    fetch = Fake(PAGE, redirects=redirects)
    mem, _ = remember(stored=stored)
    mine = OpenRent(key="openrent_shadow")
    harvest = mine.harvest("E14", fetch, Quiet(), SETTLED, mem)  # type: ignore[arg-type]
    assert len(harvest.caught) == 1
    assert harvest.caught[0].listing.source_key == "openrent_shadow"


def test_an_empty_district_is_complete_so_that_it_can_settle() -> None:
    # A district with nothing in it still has to be marked as read through, or
    # it would never settle and so would never announce its first real listing.
    fetch = Fake("<html><body>no properties</body></html>")
    stage = Quiet()
    mem, _ = remember()
    harvest = OpenRent().harvest("E14", fetch, stage, SETTLED, mem)  # type: ignore[arg-type]

    assert harvest.caught == []
    assert harvest.complete is True
    assert stage.counts.get("no_ids") == 1


def test_the_budget_is_shared_across_districts_in_one_run() -> None:
    # One busy district must not consume the whole run, so the budget lives on
    # the portal instance rather than resetting per district.
    ids = ids_in(PAGE)
    redirects = {
        one: f"/property-to-rent/london/1-bed-flat-somewhere-e14/{one}" for one in ids
    }
    portal = OpenRent(detail_budget=2)
    fetch = Fake(PAGE, redirects=redirects)
    mem, _ = remember()

    portal.harvest("E14", fetch, Quiet(), SETTLED, mem)  # type: ignore[arg-type]
    portal.harvest("SE16", fetch, Quiet(), SETTLED, mem)  # type: ignore[arg-type]

    # Two listing pages in total across both districts, not two each.
    listing_pages = [one for one in fetch.got if "/property-to-rent/" in one]
    assert len(listing_pages) == 2


def test_this_reader_is_undated() -> None:
    # OpenRent publishes no listing date anywhere this reader can see it: the
    # "New" sort in the dropdown does not take as a query parameter, and the
    # cards say only "Last updated around 2 weeks ago". So it uses the engine's
    # read-through rule instead of a watermark.
    assert OpenRent().dated is False


# ══════════════════════════ the page parser ═══════════════════════════════
#
# `as_listing` and the slug readers, which the sweep above reaches only through
# one fixture listing. Exercised directly here because they carry the shapes
# measured on live pages, and a shape that moves is the way this reader breaks.

# The page shapes confirmed on a live listing: the rent stated twice, the
# deposit in a sentence, the postcode only inside a link, and the details in
# blocks whose markup is not worth anchoring on.
PAGE_SE16 = """<html><head><title>x</title>
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

SE16_URL = (
    "https://www.openrent.co.uk/property-to-rent/london/"
    "2-bed-flat-rotherhithe-street-se16/1234567"
)


def only(district: str = "SE16") -> Found:
    """What the slug gave up, as the reader hands it to `as_listing`."""

    return Found(
        external_id="1234567",
        url=SE16_URL,
        district=district,
        bedrooms=2,
        property_type="flat",
    )


# ── the address, which exists only in the heading ────────────────────────
#
# The four heading shapes, from live pages on 2 October 2026 — one for each
# form the slug can take. The alert prints the bedroom count, the type and the
# outcode on lines of their own, so what is wanted is the middle.
LIVE_HEADINGS = {
    "2 Bed Flat, Discovery Dock, E14": "Discovery Dock",
    "4 Bed Maisonette, Smythe St, E14": "Smythe St",
    "Room in a Shared Flat, Willis House, E14": "Willis House",
    # No building name was given, so OpenRent writes the city. "London" under
    # a line already reading "E14" is not worth a line.
    "Studio Flat, London, E14": None,
}


def test_the_address_is_the_middle_of_the_heading() -> None:
    for heading, expected in LIVE_HEADINGS.items():
        assert address_in(f"<h1>{heading}</h1>") == expected, heading


def test_a_flat_number_stays_with_its_building() -> None:
    # More than three parts: everything between the type and the outcode is
    # the address, because dropping any of it would move the flat.
    assert address_in("<h1>1 Bed Flat, Flat 2, St Cuthbert House, SE16</h1>") == (
        "Flat 2, St Cuthbert House"
    )


def test_a_heading_of_another_shape_yields_no_address() -> None:
    # Two parts could be either way round, and guessing would put a property
    # type on the "where" line. No line is the answer this had before.
    assert address_in("<h1>2 Bed Flat, E14</h1>") is None
    assert address_in("<h1>Something else entirely</h1>") is None
    assert address_in("<p>no heading at all</p>") is None
    # The last part has to be an outcode, or the shape is not the measured one.
    assert address_in("<h1>2 Bed Flat, Discovery Dock, London</h1>") is None


def test_the_alert_can_say_where_an_openrent_listing_is() -> None:
    # The bug this exists to stop coming back: `listing_view` reads the "where"
    # line out of `raw.address`, the key Rightmove and Zoopla both write, and
    # OpenRent wrote only a slug — so its alerts showed a postcode, a price and
    # no address at all.
    listing = as_listing(only(), PAGE_SE16)

    assert listing is not None
    assert listing.raw["address"] == "Rotherhithe Street"
    assert listing.title == "Rotherhithe Street"


def test_a_page_with_no_usable_heading_still_becomes_a_listing() -> None:
    # The address is worth having and worth nothing next to the listing: a
    # heading nobody can read must not cost the alert itself.
    page = PAGE_SE16.replace("<h1>2 Bed Flat, Rotherhithe Street, SE16</h1>", "")
    listing = as_listing(only(), page)

    assert listing is not None
    assert listing.raw["address"] == ""
    assert listing.title is None


def test_the_slug_names_the_place_when_there_is_no_page_to_read() -> None:
    # The feed has a url and nothing else. Every shape the slug takes, with the
    # type stripped off the front and the outcode off the back.
    cases = {
        "2-bed-flat-discovery-dock-e14": "Discovery Dock",
        "4-bed-maisonette-smythe-st-e14": "Smythe St",
        "room-in-a-shared-flat-willis-house-e14": "Willis House",
        "1-bed-flat-st-cuthbert-house-se16": "St Cuthbert House",
        # The longest type wins, or "house" would be left on the front of the
        # address — the same rule `read_slug` needs for the same reason.
        "2-bed-terraced-house-rotherhithe-street-se16": "Rotherhithe Street",
        "studio-craven-street-wc2n": "Craven Street",
        # No building name given, so there is nothing worth a line.
        "studio-flat-london-e14": None,
        "nonsense": None,
    }
    for slug, expected in cases.items():
        assert place_in_slug(slug) == expected, slug


# ── the page, read as text ───────────────────────────────────────────────

def test_a_script_or_style_never_becomes_text() -> None:

    # The page states a plausible rent inside a script. Stripping tags without
    # dropping their contents would take that as the price.
    text = as_text(PAGE_SE16)
    assert "9,999" not in text
    assert "£1.00" not in text
    assert "2,100.00 per month" in text


def test_an_escaped_tag_cannot_become_a_tag() -> None:

    # Entities are decoded after the tags are stripped, not before, or this
    # would smuggle a script past the stripping.
    text = as_text("<p>&lt;script&gt;alert(1)&lt;/script&gt; Rent &#xA3;2,100.00 per month</p>")
    assert "<script>" in text  # as visible text, which is harmless
    assert "£2,100.00" in text


# ── the slug, which is read before any page is fetched ───────────────────

def test_the_slug_gives_rooms_and_type() -> None:
    assert read_slug("2-bed-flat-rotherhithe-street-se16") == ("SE16", 2, "flat")
    # Narrowed to four words: the matcher compares exact strings, so a filter
    # for "house" has to be answered by a terraced one.
    assert read_slug("3-bed-terraced-house-mallard-avenue-cv10") == ("CV10", 3, "house")
    assert read_slug("2-bed-maisonette-high-street-se16") == ("SE16", 2, "flat")
    assert read_slug("4-bed-bungalow-lane-cv10") == ("CV10", 4, "house")
    # A room in somebody else's flat: the number is not a bedroom count.
    assert read_slug("room-in-a-shared-house-beresford-road-dn12") == ("DN12", 1, "room")
    # A studio is a flat with nought bedrooms, not a fourth type.
    assert read_slug("studio-craven-street-wc2n") == ("WC2N", 0, "flat")


def test_a_slug_without_an_outward_code_is_refused() -> None:
    # The district comes from the slug and nowhere else — the search is a
    # two-kilometre radius — so better to skip one listing than to file it
    # under a district it is not in.
    assert read_slug("2-bed-flat-somewhere-unknown") is None


# ── the listing ──────────────────────────────────────────────────────────

def test_a_page_becomes_a_listing() -> None:
    listing = as_listing(only(), PAGE_SE16)
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


# ── the picture ──────────────────────────────────────────────────────────
#
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


# ── the type comes from its position, not from anywhere in the slug ────────
#
# It used to be any type word found anywhere, which stored a flat in a
# building called "St Cuthbert House" as a house: "house" matched before
# "flat" was tried. Of a hundred live slugs across five districts, four were
# flats recorded as houses — invisible to anyone filtering for a flat and
# wrongly shown to anyone filtering for a house. London is full of buildings
# called "… House".

import pytest  # noqa: E402


@pytest.mark.parametrize(
    ("slug", "kind"),
    [
        # The listing that reported this.
        ("2-bed-flat-st-cuthbert-house-e14", "flat"),
        # And its neighbours, from the same sample.
        ("1-bed-flat-claremont-house-se16", "flat"),
        ("2-bed-flat-vancouver-house-se16", "flat"),
        ("1-bed-flat-durell-house-se16", "flat"),
        ("2-bed-flat-bluebell-house-se16", "flat"),
        # Genuinely houses, and the longer phrase has to win over the shorter.
        ("4-bed-terraced-house-upper-north-st-e14", "house"),
        ("5-bed-end-of-terrace-house-a-se16", "house"),
        ("4-bed-semi-detached-house-x-nw3", "house"),
        ("3-bed-detached-house-y-nw3", "house"),
        # Every other phrase seen in the live sample.
        ("4-bed-maisonette-smythe-st-e14", "flat"),
        ("2-bed-flat-london-e14", "flat"),
        ("5-bed-terraced-a-se16", "house"),
    ],
)
def test_the_type_is_read_from_after_the_bedroom_count(slug: str, kind: str) -> None:
    read = read_slug(slug)
    assert read is not None, slug
    assert read[2] == kind, slug


def test_a_houseboat_is_not_a_house() -> None:
    # Matched as a whole segment, so a word that merely starts with one of
    # ours is not one of ours.
    read = read_slug("3-bed-houseboat-mooring-e14")
    assert read is not None and read[2] is None


def test_a_room_and_a_studio_keep_their_own_rules() -> None:
    # Both are recognised before the bedroom count is even looked for, and a
    # building name full of type words must not disturb either.
    assert read_slug("room-in-a-shared-flat-willis-house-e14") == ("E14", 1, "room")
    assert read_slug("room-in-a-shared-house-royal-court-se16") == ("SE16", 1, "room")
    assert read_slug("studio-flat-london-e14") == ("E14", 0, "flat")
