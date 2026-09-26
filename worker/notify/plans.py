from __future__ import annotations

import os
from datetime import datetime

SITE = os.environ.get("SITE_URL", "https://londonhomefinder.co.uk").rstrip("/")

def bot_username() -> str:

    return (os.environ.get("TELEGRAM_BOT_USERNAME") or "").strip().lstrip("@")

def upgrade_link() -> str | None:

    # Telegram only. Tapping it sends /pay to the bot, which issues a fresh
    # checkout link — one tap, and never a stale token.
    name = bot_username()
    return f"https://t.me/{name}?start=pay" if name else None

def checkout_link(token: str) -> str:

    # Everywhere else. A t.me link in WhatsApp sends the person to a Telegram
    # bot they may not even use, so the button has to carry the checkout page
    # itself, and that page needs a token to know whose plan is being bought.
    return f"{SITE}/upgrade?t={token}"

KEPT = "Your filter is kept — paying turns the alerts back on with nothing to set up again."

def _when(plan_until: datetime | None) -> str:
    if plan_until is None:
        return ""

    return plan_until.strftime("%d %b at %H:%M")

def _falls_back_to(share: int | None) -> str:

    # The share is read from `plans.delivery_share` on the lapsed tier, never
    # written here: one row decides what is delivered and what is promised.
    if share is None or share >= 100:
        return "After that the alerts stop until you renew."
    return f"After that you receive {share}% of what matches, until you renew."

def expiring_notice(
    plan: str, plan_until: datetime | None, stage: str, share: int | None = None,
    link: str | None = None,
) -> str:

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
            f"⏳ {opening}",
            _falls_back_to(share),
            KEPT,
            f"Full access: {link}" if link else "/pay — full access",
        ]
    )

def expiry_notice(plan: str, share: int | None = None, link: str | None = None) -> str:
    """What somebody is told the moment their trial or their plan runs out.

    Says what happened, then what to do about it, in that order. The last thing
    anybody needs at this point is a price list: they need the one tap that
    turns the alerts back on, and somewhere to read about it if they would
    rather not decide inside a chat window.
    """

    what = "free trial" if plan == "trial" else "subscription"
    if share is None or share >= 100:
        opening = f"Your {what} has ended, so alerts have stopped."
    else:
        opening = (
            f"Your {what} has ended. You now receive {share}% of what matches "
            "your filter."
        )
    return "\n".join(
        [
            f"🔔 {opening}",
            KEPT,
            f"Pay here and the alerts resume at once: {link}" if link
            else "/pay — a payment link, and the alerts resume at once",
            "/update — change my search",
            "/stop — delete my filter",
        ]
    )

def notice_for(
    plan: str, plan_until: datetime | None, stage: str, share: int | None = None,
    link: str | None = None,
) -> str:

    if stage == "expired":
        return expiry_notice(plan, share, link)
    return expiring_notice(plan, plan_until, stage, share, link)

def checkin_notice() -> str:
    """Asked half an hour before WhatsApp's 24-hour window shuts.

    Short on purpose: it is a question with two buttons, and every extra line
    is a line between the question and the answer. Tapping either button is an
    inbound message, which is what reopens the window — so even "I found a
    place" leaves us able to reply.
    """

    return "\n".join(
        [
            "👋 How is the search going?",
            "WhatsApp only lets me write for 24 hours after your last message, "
            "and that is nearly up.",
            "Tap below and the alerts carry on.",
        ]
    )

def carrying_on(criteria_card: str) -> str:
    """The answer to "keep searching". States what is being searched for."""

    return "\n".join(
        [
            "✅ Alerts are back on — anything held while it was quiet is on its way.",
            "",
            criteria_card,
            "",
            "/current — show this again",
            "/update — change my search",
            "/stop — delete my filter",
        ]
    )

def digest_notice(matched: int, share: int, *, paid: bool = False) -> str:
    """The evening summary. Short on purpose: it is read at a glance."""

    listings = "listing" if matched == 1 else "listings"

    if matched == 0:
        # A quiet day is worth saying out loud: silence is indistinguishable
        # from a broken bot, and the usual cause is a filter that is too tight.
        return "\n".join(
            [
                "🔔 Nothing matched your filter today.",
                "Quiet days happen — /update to widen the rent range or add an area.",
            ]
        )

    if paid or share >= 100:
        return f"🔔 {matched} new {listings} matched your filter today."

    return (
        f"🔒 {matched} new {listings} today — you are seeing {share}%. "
        "Upgrade to get every one of them."
    )
