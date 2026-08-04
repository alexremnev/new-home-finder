"""Contracts shared by every pipeline stage.

This package is the single source of truth for the data that moves between
stages. It imports nothing else from the project, which is what keeps source
adapters and notifiers additive: they depend on these definitions, and nothing
depends on them.
"""

from worker.contracts.extraction import ExtractionSchema, FieldRule, Health, Strategy, Verdict
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
    register_notifier,
)
from worker.contracts.source import (
    SOURCES,
    DiscoveryKind,
    FetchResult,
    FetchTask,
    FieldSpec,
    Mode,
    PageKind,
    Source,
    SourceLocation,
    register,
)

__all__ = [
    "NOTIFIERS",
    "SOURCES",
    "Action",
    "Alert",
    "AlertKind",
    "DiscoveryKind",
    "ExtractionSchema",
    "FetchResult",
    "FetchTask",
    "FieldRule",
    "FieldSpec",
    "Furnished",
    "Health",
    "Listing",
    "ListingView",
    "Mode",
    "Notifier",
    "PageKind",
    "RawListing",
    "RawValue",
    "Recipient",
    "SendResult",
    "Source",
    "SourceLocation",
    "Strategy",
    "Verdict",
    "register",
    "register_notifier",
]
