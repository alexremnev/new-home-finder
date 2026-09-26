"""A line to whoever runs this, not to a subscriber.

`scripts/report.py` has had one of these since the hourly report existed; the
worker had none, so anything it noticed could only reach a log nobody watches.

Deliberately plain: one function, no notifier registry, no retries. An alert
that fails to send is worth a line on stderr and nothing more — it must never
be the reason a run fails, because the run is the thing that matters and the
alert is only the commentary.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def ops_chat() -> tuple[str, str] | None:
    """The token and chat to alert on, or None when none is configured."""

    # The same pair as the report, and the same order of preference: a separate
    # alerting bot if there is one, otherwise the bot that serves subscribers.
    token = os.environ.get("TELEGRAM_OPS_TOKEN") or os.environ.get("TELEGRAM_TOKEN")
    chat = os.environ.get("TELEGRAM_OPS_CHAT")
    return (token, chat) if token and chat else None


def tell_ops(message: str, *, timeout: float = 20.0) -> bool:
    """Send one line to the ops chat. False when it could not be sent."""

    where = ops_chat()
    if where is None:
        print(
            "ops: nothing sent — set TELEGRAM_OPS_CHAT, and either "
            "TELEGRAM_OPS_TOKEN for a separate alerting bot or TELEGRAM_TOKEN "
            "to reuse the one that serves subscribers.",
            file=sys.stderr,
        )
        return False

    token, chat = where
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps(
            {"chat_id": chat, "text": message, "disable_web_page_preview": True}
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status >= 300:
                print(f"ops: alert rejected with {response.status}", file=sys.stderr)
                return False
    except (urllib.error.URLError, OSError) as error:
        print(f"ops: could not alert — {type(error).__name__}: {error}", file=sys.stderr)
        return False
    return True


__all__ = ["ops_chat", "tell_ops"]
