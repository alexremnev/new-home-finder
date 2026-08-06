"""Process configuration.

Everything the worker needs to start comes from the environment; everything that
governs its behaviour comes from the database. That split is what makes the host
interchangeable — moving between CI, a VPS, and a container changes nothing here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from worker.env import load_env


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    database_url: str
    telegram_token: str | None
    telegram_ops_chat: str | None
    anthropic_api_key: str | None
    run_url: str | None
    proxy_url: str | None
    dry_run: bool

    @classmethod
    def load(cls, *, dry_run: bool = False) -> Config:
        load_env()
        database_url = os.environ.get("DATABASE_URL", "").strip()
        if not database_url:
            raise ConfigError(
                "DATABASE_URL is not set. Copy .env.example to .env for local runs, "
                "or add the secret to the repository for CI."
            )
        return cls(
            database_url=database_url,
            telegram_token=_opt("TELEGRAM_TOKEN"),
            telegram_ops_chat=_opt("TELEGRAM_OPS_CHAT"),
            anthropic_api_key=_opt("ANTHROPIC_API_KEY"),
            run_url=_opt("RUN_URL"),
            proxy_url=_opt("PROXY_URL"),
            dry_run=dry_run,
        )

    def require_anthropic(self) -> str:
        if not self.anthropic_api_key:
            raise ConfigError("ANTHROPIC_API_KEY is required to infer an extraction schema")
        return self.anthropic_api_key

    def require_telegram(self) -> str:
        if not self.telegram_token:
            raise ConfigError("TELEGRAM_TOKEN is required to deliver alerts")
        return self.telegram_token


def _opt(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None
