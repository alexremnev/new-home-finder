from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

Furnished = Literal["furnished", "unfurnished", "part", "unknown"]

RawValue = str | int | float | bool | None

class RawListing(BaseModel):

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
    # Square feet, always. Metres are converted by worker.units on the way in,
    # and a source that gave no unit leaves this None rather than guessing.
    floor_area_sqft: int | None = Field(default=None, ge=50, le=20_000)

    raw: dict[str, object] = Field(default_factory=dict)
