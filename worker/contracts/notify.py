"""Delivery channels.

An `Alert` carries facts, not a rendered string. Rendering belongs to the
channel, because the constraints differ sharply between them: Telegram needs
MarkdownV2 escaping within a 4096-character limit, WhatsApp needs an approved
template with a fixed number of variables, email needs HTML. If the matcher
produced text, each new channel would require its own matcher.

`ListingView` is deliberately narrower than `Listing`: it carries facts and a
link to the original, and neither photographs nor the full description. That is
a decision about content reuse, not an oversight.
"""

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
    """What a notification is allowed to show."""

    price_pcm: int
    bedrooms: int
    property_type: str | None = None
    district: str | None = None
    # The full postcode, not just the outward code. "E11" locates a neighbourhood;
    # "E11 4EG" locates a street, which is what somebody deciding whether to view a
    # flat actually wants — and it is what the source states.
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
    # Added once a feed started supplying them. All optional, and all shown when
    # present: the recipient decides what matters, and a field withheld because it
    # seemed minor is a question they then have to open the listing to answer.
    bathrooms: int | None = None
    area: str | None = None           # the neighbourhood as the source names it
    address: str | None = None
    size_text: str | None = None      # "509 sq ft (47.29 sq m)", kept verbatim
    deposit_pcm: float | None = None
    # The percentage of matches this person's plan actually delivers, or None when
    # it delivers all of them. Carried on the view rather than looked up by the
    # renderer because the renderer has no database and should not grow one.
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
    # "This person is gone" as opposed to "this failed once": a blocked bot, a
    # deleted chat, a dead number. The channel knows how its provider says this;
    # the delivery stage only needs to know that retrying is pointless and the
    # subscription should stop. Without it the pipeline would have to read
    # provider error codes, which is exactly what these contracts exist to avoid.
    recipient_gone: bool = False
    cost_micros: int = 0


@runtime_checkable
class Notifier(Protocol):
    key: str

    def supports(self, kind: AlertKind) -> bool: ...

    def send(self, to: Recipient, alert: Alert) -> SendResult: ...


# Classes, not instances. Credentials are read by each channel in its own
# module, so adding WhatsApp is a new file plus a row in `channels` — never an
# edit to the delivery stage.
NOTIFIERS: dict[str, type] = {}


def register_notifier(cls: type) -> type:
    NOTIFIERS[cls.key] = cls
    return cls


def build_notifier(channel: str) -> Notifier | None:
    """A notifier ready to send, or None if this channel has no implementation.

    A missing implementation is not an error here: `channels` may hold a row for
    a channel that is planned but not built, and a queued message for one of
    those must fail visibly on its own rather than stop the whole run.
    """
    cls = NOTIFIERS.get(channel)
    return cls.from_env() if cls is not None else None
