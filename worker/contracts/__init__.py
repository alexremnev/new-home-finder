"""The shapes that cross module boundaries.

Only two remain. The extraction schema and the source protocol went with the
scraper: a Telegram feed needs neither a per-site parser contract nor a registry of
site implementations — it has one parser, and which chat it reads is configuration.
"""

from worker.contracts.listing import Furnished, Listing, PropertyType
from worker.contracts.notify import (
    NOTIFIERS,
    Alert,
    AlertKind,
    ListingView,
    Recipient,
    SendResult,
    build_notifier,
    register_notifier,
)

__all__ = [
    "NOTIFIERS",
    "Alert",
    "AlertKind",
    "Furnished",
    "Listing",
    "ListingView",
    "PropertyType",
    "Recipient",
    "SendResult",
    "build_notifier",
    "register_notifier",
]
