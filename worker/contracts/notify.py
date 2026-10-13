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
    # Which keyboard row this button belongs on, for a channel that lays buttons
    # out itself. Consecutive actions sharing a number sit side by side; None —
    # the default — takes a row of its own. WhatsApp ignores it: it stacks reply
    # buttons and there is no setting for that.
    row: int | None = None

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
    # Where the portal put its own pin. Every Rightmove and Zoopla listing
    # carries these, and most carry no full postcode — Zoopla publishes none at
    # all on a search page — so this is the only thing that can put a map link
    # on those alerts. See `fields.maps_link`.
    lat: float | None = None
    lng: float | None = None
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
    # Whether this alert may be sent as the paid template when the only other
    # option is not sending it at all. WhatsApp outside its 24-hour window is
    # the only channel that asks: it cannot send prose there, and the template
    # that can is a marketing one at three times the price of a message inside
    # the window.
    #
    # So it is not "may a template be used", it is "is this one worth buying".
    # Off by default, and set by the outbox for exactly one case — a paid plan
    # that has ended. A trial that ends outside the window is told nothing:
    # somebody who never paid, and has not written in for a day, is not worth
    # six cents of a marketing message, and the warning before the end has
    # already gone out inside the window. See `claim_plan_notices`.
    billable_template: bool = False
    # The upgrade token, for a template whose button is a fixed url and one
    # variable. `actions` carries the whole link for every channel that can
    # write one out; a template button is given the suffix alone, so the two
    # cannot be the same field.
    #
    # The token and not the url: what the template declares is
    # `.../upgrade?t={{1}}`, and a parameter containing the host as well would
    # produce a link with the host in it twice.
    checkout_token: str | None = None

class SendResult(BaseModel):
    ok: bool
    provider_msg_id: str | None = None
    error: str | None = None
    retryable: bool = False

    recipient_gone: bool = False
    cost_micros: int = 0

    # Which of the provider's rates the message that actually went out is
    # charged at. Only WhatsApp sets it, and only it has more than one:
    # free-form inside the 24-hour window is a `service` message, and the
    # approved templates that are the only way to reach a shut window are
    # priced by their category — `utility` for the listing template,
    # `marketing` for the plan notice, which carries a payment button and is
    # therefore the dearest thing we send. The notifier is the one thing that
    # knows which payload it sent, so it is the one thing that can say — see
    # 0063, 0066, and `_post`.
    #
    # None means "no rate applies", which is Telegram: nothing to meter where
    # delivery is free.
    billed_as: Literal["service", "utility", "marketing"] | None = None

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
