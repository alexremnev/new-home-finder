from __future__ import annotations

from datetime import date
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

AlertKind = Literal["listing", "welcome", "stopped", "expiring", "expired", "ops"]

class Recipient(BaseModel):
    channel: str
    address: str = Field(description="chat id, E.164 phone, or email; never logged")

class Action(BaseModel):
    label: str
    url: str

class ListingView(BaseModel):

    price_pcm: int
    bedrooms: int
    property_type: str | None = None
    district: str | None = None

    postcode: str | None = None
    zone: int | None = None
    available_from: date | None = None
    furnished: str = "unknown"
    pets_allowed: bool | None = None
    bills_included: bool | None = None
    min_tenancy_months: int | None = None
    source_display: str
    is_landlord_direct: bool | None = None
    url: str

    bathrooms: int | None = None
    area: str | None = None
    address: str | None = None
    size_text: str | None = None
    deposit_pcm: float | None = None

    share: int | None = None

class Alert(BaseModel):
    kind: AlertKind
    listing: ListingView | None = None
    text: str | None = None
    actions: list[Action] = Field(default_factory=list)

class SendResult(BaseModel):
    ok: bool
    provider_msg_id: str | None = None
    error: str | None = None
    retryable: bool = False

    recipient_gone: bool = False
    cost_micros: int = 0

@runtime_checkable
class Notifier(Protocol):
    key: str

    def supports(self, kind: AlertKind) -> bool: ...

    def send(self, to: Recipient, alert: Alert) -> SendResult: ...

NOTIFIERS: dict[str, type] = {}

def register_notifier(cls: type) -> type:
    NOTIFIERS[cls.key] = cls
    return cls

def build_notifier(channel: str) -> Notifier | None:

    cls = NOTIFIERS.get(channel)
    return cls.from_env() if cls is not None else None
