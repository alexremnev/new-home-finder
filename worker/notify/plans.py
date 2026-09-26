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

KEPT = "Your filter is kept exactly as it is — paying turns the alerts back on with nothing to set up again."

def _when(plan_until: datetime | None) -> str:
    if plan_until is None:
        return ""

    return plan_until.strftime("%d %b at %H:%M")

def _falls_back_to(share: int | None) -> str:

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
            opening,
            "",
            _falls_back_to(share),
            "",
            KEPT,
            "",
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
            opening,
            "",
            KEPT,
            "",
            "To carry on:",
            # Numbered, because it is a two-step instruction and "tap the link"
            # on its own left people asking what happens after they pay.
            f"1. Open {link} and pay by card." if link
            else "1. Send /pay for a payment link.",
            "2. The alerts start again straight away — nothing to set up.",
            "",
            f"More about the service: {SITE}",
            "",
            "/update — change what you are looking for",
            "/stop — delete my filter for good",
        ]
    )

def notice_for(
    plan: str, plan_until: datetime | None, stage: str, share: int | None = None,
    link: str | None = None,
) -> str:

    if stage == "expired":
        return expiry_notice(plan, share, link)
    return expiring_notice(plan, plan_until, stage, share, link)

def digest_notice(
    matched: int,
    share: int,
    *,
    avg_price: int | None = None,
    paid: bool = False,
    rooms_only: bool = False,
) -> str:

    listings = "listing" if matched == 1 else "listings"
    # Says which average it is, because the two are not comparable and the old
    # wording — "in what matched" — was untrue as soon as anything was left out.
    # A filter for rooms alone averages rooms; every other filter averages the
    # homes and leaves rooms out.
    price = (
        f"💷 Average room rent: £{avg_price:,}/month"
        if avg_price and rooms_only
        else f"💷 Average rent, rooms aside: £{avg_price:,}/month"
        if avg_price
        else None
    )

    if matched == 0:
        # A quiet day is worth saying out loud: silence is indistinguishable
        # from a broken bot, and the usual cause is a filter that is too tight.
        return "\n".join(
            [
                "🔔 Nothing matched your filter today.",
                "",
                "Quiet days happen. If it stays quiet, a wider rent range or one "
                "more area usually helps — /update to change it.",
            ]
        )

    if paid or share >= 100:
        return "\n".join(
            [f"🔔 {matched} new {listings} matched your filter today."]
            + ([price] if price else [])
        )

    return "\n".join(
        [
            f"🔒 {matched} new {listings} today — you're missing {100 - share}%! "
            "Upgrade now to unlock instant notifications.",
        ]
        + ([""] + [price] if price else [])
    )
