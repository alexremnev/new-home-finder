"""The shapes that cross module boundaries.

Two modules remain. The extraction schema and the source protocol went with the
scraper: a Telegram feed needs neither a per-site parser contract nor a registry of
site implementations — it has one parser, and which chat it reads is configuration.
"""

from worker.contracts.listing import Furnished, Listing, RawListing, RawValue
from worker.contracts.notify import (
    NOTIFIERS,
    Action,
    Alert,
    AlertKind,
    ListingView,
    Notifier,
    Recipient,
    SendResult,
    build_notifier,
    register_notifier,
)

__all__ = [
    "NOTIFIERS",
    "Action",
    "Alert",
    "AlertKind",
    "Furnished",
    "Listing",
    "ListingView",
    "Notifier",
    "RawListing",
    "RawValue",
    "Recipient",
    "SendResult",
    "build_notifier",
    "register_notifier",
]
