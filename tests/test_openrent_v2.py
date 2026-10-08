"""The newer OpenRent reader, against a page the real site served.

`tests/fixtures/openrent_e14_search.html` is trimmed from a genuine E14 search
page taken on 26 September 2026: the first two rendered cards, and the
`PROPERTYIDS` array that is the whole point of reading this page — it lists
every id the search matched, not only the twenty shown.

Nothing here touches the network.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime

from worker.sources.fetch import Reply
from worker.sources.openrent_v2 import (
    OpenRentV2,
    how_many,
    ids_in,
    images_in,
    paths_in,
    search_url,
    short_url,
    slug_from_path,
    slugs_in,
)
from worker.sources.sweep import Memory

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "openrent_e14_search.html"
PAGE = FIXTURE.read_text(encoding="utf-8")


# ── the id list, which is why this reader exists ─────────────────────────

def test_every_id_in_the_district_comes_from_one_page() -> None:
    # The original reader downloaded the nationwide sitemap to answer this —
    # about 950MB a day. This page answers it in 92KB, and for the twenty
    # rendered cards it throws in the rent and a photograph as well.
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

# Enough of a listing page for the original reader's parser: it needs a rent
# stated per month, and reads everything else as optional.
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
    harvest = OpenRentV2().harvest("E14", fetch, stage, SETTLED, mem)  # type: ignore[arg-type]

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
    harvest = OpenRentV2().harvest("E14", fetch, stage, SETTLED, mem)  # type: ignore[arg-type]

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
    OpenRentV2().harvest("E14", fetch, Quiet(), SETTLED, mem)  # type: ignore[arg-type]

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
    harvest = OpenRentV2().harvest("E14", fetch, stage, None, mem)  # type: ignore[arg-type]

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
    harvest = OpenRentV2().harvest("E14", fetch, stage, SETTLED, mem)  # type: ignore[arg-type]

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
    harvest = OpenRentV2().harvest("E14", fetch, stage, SETTLED, mem)  # type: ignore[arg-type]

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
    harvest = OpenRentV2().harvest("E14", fetch, stage, SETTLED, mem)  # type: ignore[arg-type]

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
    harvest = OpenRentV2().harvest("E14", fetch, stage, SETTLED, mem)  # type: ignore[arg-type]

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
    harvest = OpenRentV2(detail_budget=0).harvest(
        "E14", fetch, stage, SETTLED, mem  # type: ignore[arg-type]
    )

    assert harvest.complete is False
    assert harvest.caught == []
    # No listing page was fetched: the budget was spent before the first one.
    assert [one for one in fetch.got if "/property-to-rent/" in one] == []


def test_a_stored_listing_carries_this_readers_own_source_key() -> None:
    # It carried `openrent` — the parser module's key — because this reader
    # reuses that parser. Its own "have I seen this id?" check asked about
    # `openrent_v2`, so the answer was always no: every id looked new on every
    # run, the same pages were fetched every twenty minutes, the budget was
    # always exhausted, no district ever settled, and nobody was ever sent
    # anything. One wrong constant, and that was the whole of it. See 0053.
    ids = ids_in(PAGE)
    one = ids[0]
    fetch = Fake(
        PAGE,
        redirects={one: f"/property-to-rent/london/1-bed-flat-hale-street-e14/{one}"},
    )
    mem, _ = remember(stored={other for other in ids if other != one})
    harvest = OpenRentV2().harvest("E14", fetch, Quiet(), SETTLED, mem)  # type: ignore[arg-type]

    assert len(harvest.caught) == 1
    assert harvest.caught[0].listing.source_key == "openrent_v2"


def test_an_empty_district_is_complete_so_that_it_can_settle() -> None:
    # A district with nothing in it still has to be marked as read through, or
    # it would never settle and so would never announce its first real listing.
    fetch = Fake("<html><body>no properties</body></html>")
    stage = Quiet()
    mem, _ = remember()
    harvest = OpenRentV2().harvest("E14", fetch, stage, SETTLED, mem)  # type: ignore[arg-type]

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
    portal = OpenRentV2(detail_budget=2)
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
    assert OpenRentV2().dated is False
