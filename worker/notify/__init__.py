"""Delivery channels. Importing this package registers every notifier."""

from worker.contracts.notify import NOTIFIERS, build_notifier
from worker.notify import telegram  # noqa: F401 - imported for its registration

__all__ = ["NOTIFIERS", "build_notifier", "telegram"]
