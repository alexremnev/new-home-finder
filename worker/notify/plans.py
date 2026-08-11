"""What the bot says about plans.

Separate from the channel modules because the wording is the same whichever
channel carries it, and separate from the web app because these are sent by the
worker — nobody is present to be answered when a plan runs out.

Prices are not hard-coded here. A message that says £10 while the `plans` table
says something else is worse than one that says nothing, and the point of keeping
limits in the database is that they can change without a deploy.

Three stages, and the ordering matters more than the wording. A warning a day
ahead is the one that can be acted on; an hour ahead is the reminder for someone
who meant to and forgot; the message after the fact exists only so the silence is
explained. Sending just the last of the three — which is what this file used to do
— means every lapse is a surprise.
"""

from __future__ import annotations

import os
from datetime import datetime

SITE = os.environ.get("SITE_URL", "https://london-rent-alerts.vercel.app").rstrip("/")

# What the filter surviving is worth saying, every time. It is the work the person
# put in, and knowing it is still there is the difference between renewing and
# starting over somewhere else.
KEPT = "Your filter is kept exactly as it is — paying turns the alerts back on with nothing to set up again."


def _when(plan_until: datetime | None) -> str:
    if plan_until is None:
        return ""
    # Local-looking and short. The exact minute matters for the one-hour warning
    # and not at all for the day-ahead one, so both get the same format rather
    # than two.
    return plan_until.strftime("%d %b at %H:%M")


def expiring_notice(plan: str, plan_until: datetime | None, stage: str) -> str:
    """A day, or an hour, before the end."""
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
    """Told once, after a plan has run out."""
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
    """The message for a claimed stage. One place that maps stage to wording, so a
    new stage cannot be added to the query and forgotten here."""
    if stage == "expired":
        return expiry_notice(plan)
    return expiring_notice(plan, plan_until, stage)
