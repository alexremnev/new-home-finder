"""London rent alerts worker.

The worker is deliberately unaware of where it runs. Configuration comes from
environment variables and the database only, and there is a single entry point,
so moving between GitHub Actions, a VPS, or a container is a hosting change
rather than a code change.
"""

__version__ = "0.1.0"
