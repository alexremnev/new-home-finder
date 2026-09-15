from __future__ import annotations

import os
from datetime import datetime

SITE = os.environ.get("SITE_URL", "https://london-rent-alerts.vercel.app").rstrip("/")

def bot_username() -> str:

    return (os.environ.get("TELEGRAM_BOT_USERNAME") or "").strip().lstrip("@")

def upgrade_link() -> str | None:

    name = bot_username()
    return f"https://t.me/{name}?start=pay" if name else None

KEPT = "Your filter is kept exactly as it is — paying turns the alerts back on with nothing to set up again."

def _when(plan_until: datetime | None) -> str:
    if plan_until is None:
        return ""

    return plan_until.strftime("%d %b at %H:%M")

def expiring_notice(plan: str, plan_until: datetime | None, stage: str) -> str:

    what = "free trial" if plan == "trial" else "subscription"
    when = _when(plan_until)

    if stage == "hour":
        opening = f"Your {what} ends in about an hour"
        opening += f" — {when}." if when else "."
    else:
        opening = f"Your {what} ends tomorrow"
        opening += f", {when}." if when else "."

    return "\n".join(
        [
            opening,
            "",
            "After that the alerts stop until you renew.",
            "",
            KEPT,
            "",
            "/pay — 2 more weeks of alerts",
        ]
    )

def expiry_notice(plan: str) -> str:

    opening = (
        "Your free trial has ended, so alerts have stopped."
        if plan == "trial"
        else "Your subscription has ended, so alerts have stopped."
    )
    return "\n".join(
        [
            opening,
            "",
            KEPT,
            "",
            "/pay — 2 weeks of alerts",
            f"{SITE}/upgrade",
            "",
            "/stop — delete my filter for good",
        ]
    )

def notice_for(plan: str, plan_until: datetime | None, stage: str) -> str:

    if stage == "expired":
        return expiry_notice(plan)
    return expiring_notice(plan, plan_until, stage)

def withheld_notice(matched: int, share: int) -> str:

    listings = "listing" if matched == 1 else "listings"
    missing = 100 - share
    return (
        f"🔒 {matched} new {listings} today — you're missing {missing}%! "
        "Upgrade now to unlock instant notifications."
    )
