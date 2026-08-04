"""Source adapters: how to reach a site, and what to ask of it.

A source adapter answers three questions — which URLs to request, what fields the
site can supply, and how to convert those fields to canonical units. It contains
no selectors and no JSONPaths: those live in the database as data, because they
are rewritten automatically when a site changes its markup.

Adding a source is therefore one module plus rows in `sources` and
`source_locations`. The extraction schema for it is inferred on first run.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from worker.contracts.extraction import Strategy
from worker.contracts.listing import Listing, RawListing

PageKind = Literal["search_list", "detail"]
Mode = Literal["hot", "sweep"]
DiscoveryKind = Literal["sitemap_diff", "search_api", "browser"]


class SourceLocation(BaseModel):
    """How one source names one place."""

    source_key: str
    location_id: int
    code: str = Field(description="our code, e.g. 'E14'")
    external_id: str = Field(description="the site's own identifier for the same place")


class FieldSpec(BaseModel):
    """One field the extractor must produce.

    The list of these is the specification of the extraction task, read both by
    a human writing a schema by hand and by the model inferring one. `hint` is
    written for that audience: it should describe the value as it appears on the
    page, including the shapes it can take.
    """

    name: str
    required: bool
    kind: Literal["str", "int", "float", "bool", "date"]
    hint: str


class FetchTask(BaseModel):
    """A single request the fetcher should make."""

    source_key: str
    page_kind: PageKind
    url: str
    location_id: int | None = None
    external_id: str | None = Field(
        default=None, description="set for detail pages, so results need no re-parsing of the URL"
    )
    method: Literal["GET", "POST"] = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None
    page: int = 1


class FetchResult(BaseModel):
    """A response body plus what we can tell about it without parsing."""

    task: FetchTask
    status: int
    body: bytes
    fingerprint: str = Field(
        description="structural hash of the markup; changes with layout, not with content"
    )
    from_cache: bool = False
    elapsed_ms: int = 0


@runtime_checkable
class Source(Protocol):
    """Contract every source adapter implements."""

    key: str
    display_name: str
    preferred: tuple[Strategy, ...]
    """Strategies to try, in order. Reflects where the data actually lives."""

    fields: tuple[FieldSpec, ...]
    """The extraction specification; see FieldSpec."""

    def discover(
        self, locations: list[SourceLocation], mode: Mode, known_ids: set[str]
    ) -> Iterator[FetchTask]:
        """Yield the requests for one run.

        `known_ids` lets an adapter skip listings already stored, which is what
        keeps a run to a handful of requests rather than a full crawl.
        """
        ...

    def listing_url(self, external_id: str) -> str: ...

    def normalize(self, raw: RawListing) -> Listing:
        """Convert extracted values to canonical units.

        This is the only place unit and format knowledge lives, and the reason an
        inferred schema cannot introduce a semantic error unnoticed: a weekly
        price misread as monthly fails the plausibility check downstream.
        """
        ...


SOURCES: dict[str, Source] = {}


def register(cls: type) -> type:
    """Class decorator that adds a source adapter to the registry."""
    instance = cls()
    SOURCES[instance.key] = instance
    return cls
