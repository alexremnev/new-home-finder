"""Listing records, before and after normalisation.

`RawListing` holds values exactly as they appeared on the page. `Listing` is the
canonical form, and everything downstream of normalisation works only with it.

Three invariants hold across the canonical form:
  * price is always in GBP per calendar month; weekly prices are converted
    during normalisation, not during matching, so a price filter cannot quietly
    disagree with what the user was shown;
  * timestamps are UTC in storage and rendered in Europe/London only for
    display, so the BST transition cannot produce a gap or a duplicate;
  * `pets_allowed` and `bills_included` are tri-state. None means the listing
    did not say, which is a different answer from False and filters differently.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

Furnished = Literal["furnished", "unfurnished", "part", "unknown"]

RawValue = str | int | float | bool | None


class RawListing(BaseModel):
    """Field values as extracted, keyed by the source's field contract."""

    source_key: str
    fields: dict[str, RawValue]
    raw: dict[str, object] = Field(
        default_factory=dict,
        description="original payload; retained so history can be reprocessed "
        "after a parser or prompt change without re-fetching",
    )
    schema_id: int | None = None

    def get(self, name: str) -> RawValue:
        return self.fields.get(name)


class Listing(BaseModel):
    """Canonical listing."""

    source_key: str
    external_id: str
    url: str

    price_pcm: int = Field(ge=100, le=100_000)
    bedrooms: int = Field(ge=0, le=20, description="a studio is 0")
    bathrooms: int | None = Field(default=None, ge=0, le=20)
    property_type: str | None = None
    furnished: Furnished = "unknown"
    pets_allowed: bool | None = None
    bills_included: bool | None = None
    available_from: date | None = None
    min_tenancy_months: int | None = Field(default=None, ge=0, le=120)
    deposit_pcm: float | None = None

    postcode: str | None = None
    postcode_district: str | None = None
    tfl_zone: int | None = Field(default=None, ge=1, le=9)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)

    title: str | None = None
    description: str | None = None
    is_landlord_direct: bool | None = None
    photo_count: int | None = Field(default=None, ge=0)

    raw: dict[str, object] = Field(default_factory=dict)
