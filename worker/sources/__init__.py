"""Source adapters. Importing this package registers every adapter."""

from worker.contracts.source import SOURCES
from worker.sources import openrent  # noqa: F401 - imported for its registration

__all__ = ["SOURCES", "openrent"]
