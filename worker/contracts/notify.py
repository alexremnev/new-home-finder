from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

AlertKind = Literal["listing", "welcome", "stopped", "expiring", "expired", "ops"]

class Recipient(BaseModel):
    channel: str
    address: str = Field(description="chat id, E.164 phone, or email; never logged")
    # WhatsApp allows free-form text only within 24 hours of the person's last
    # message, and an approved template outside it. Which one a listing becomes
    # depends on this, so it travels with the address rather than being looked up
    # again by a notifier that has no database.
    last_inbound: datetime | None = None

class Action(BaseModel):
    label: str
    # WhatsApp allows 20 characters on a reply button and truncates silently.
    # Telegram has no such limit, so the full label stays the default and this
    # is what tight channels use.
    short: str | None = None
    url: str | None = None
    callback: str | None = None

    def button(self, limit: int) -> str:
        return (self.short or self.label)[:limit]

class ListingView(BaseModel):

    # Needed by any channel that cannot link to three portals directly and has
    # to route through ours. Optional so a view can still be built by hand.
    listing_id: int | None = None
    # The portal's own og:image. A channel that cannot make a good preview sends
    # this as a real image instead; Telegram ignores it and previews the link.
    image_url: str | None = None
    # What WhatsApp calls the photograph from the source message, once it has
    # been handed over. Preferred over image_url: it works for every portal.
    wa_media_id: str | None = None
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
    lapsed: Literal["trial", "plan"] | None = None

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
