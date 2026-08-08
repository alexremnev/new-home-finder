"""Source adapters. Importing this package registers every adapter."""

from worker.contracts.source import SOURCES
from worker.sources import openrent, rightmove  # noqa: F401 - imported for registration

__all__ = ["SOURCES", "openrent", "rightmove"]
