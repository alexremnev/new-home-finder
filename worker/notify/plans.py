from __future__ import annotations

import os
from datetime import datetime

SITE = os.environ.get("SITE_URL", "https://londonhomefinder.co.uk").rstrip("/")

def checkout_link(token: str) -> str:

    # Every channel. The page needs a token to know whose plan is being bought.
    #
    # Telegram used to get a t.me deep link here instead — tapping it sent
    # /start pay to the bot, which replied with the price list and a link to
    # this page. One tap became three and the prices were read twice, so the
    # deep link and the bot username it needed are both gone.
    return f"{SITE}/upgrade?t={token}"

KEPT = "Your filter is kept — paying turns the alerts back on with nothing to set up again."

def _when(plan_until: datetime | None) -> str:
    if plan_until is None:
        return ""

    return plan_until.strftime("%d %b at %H:%M")

def _falls_back_to(share: int | None) -> str:

    # The share is read from `user_entitlement`, never written here: one query
    # decides what is delivered and what is promised.
    #
    # Nought is "they stop", not "you receive 0%". It is the real answer on
    # WhatsApp, where a finished plan falls back to nothing because every
    # message is billed — and a sentence offering nought per cent of anything
    # reads as a fault rather than as a limit.
    if not share or share >= 100:
        return "After that the alerts stop until you renew."
    return f"After that you will only see {share}% of what matches, until you renew."

def expiring_notice(
    plan: str, plan_until: datetime | None, share: int | None = None,
    link: str | None = None,
) -> str:
    """The one warning before a plan ends, an hour out.

    There was a second a day out, with its own wording — "ends tomorrow". It is
    gone, and so is the `stage` argument that chose between the two: a day's
    notice is a day too early to act on, and the hour is the one that arrives
    while the decision is in front of the person. See `claim_plan_notices`,
    which no longer produces the stage.
    """

    what = "free trial" if plan == "trial" else "subscription"
    when = _when(plan_until)

    opening = f"Your {what} ends in about an hour"
    opening += f" — {when}." if when else "."

    # The link is a button rather than a line of text — see `plan_actions` in
    # the outbox — so the body does not repeat it. A url in the body of a
    # message is a url somebody has to select and copy.
    lines = [f"⏳ {opening}", _falls_back_to(share), KEPT]
    if not link:
        lines.append("/pay — full access")
    return "\n".join(lines)

def expiry_notice(plan: str, share: int | None = None, link: str | None = None) -> str:
    """What somebody is told the moment their trial or their plan runs out.

    Says what happened, then what to do about it, in that order. The last thing
    anybody needs at this point is a price list: they need the one tap that
    turns the alerts back on, and somewhere to read about it if they would
    rather not decide inside a chat window.
    """

    what = "free trial" if plan == "trial" else "subscription"
    if not share or share >= 100:
        opening = f"Your {what} has ended, so alerts have stopped."
    else:
        opening = (
            f"Your {what} has ended. You now receive {share}% of what matches "
            "your filter."
        )
    # The button carries the link; KEPT above already says that paying turns
    # the alerts back on with nothing to set up again, which is what the line
    # of url here used to say in more characters.
    lines = [f"🔔 {opening}", KEPT]
    if not link:
        lines.append("/pay — a payment link, and the alerts resume at once")
    lines += ["/update — change my search", "/stop — delete my filter"]
    return "\n".join(lines)

def spent_notice(
    allowance: int | None, share: int | None = None, link: str | None = None
) -> str:
    """A month that ended on its allowance rather than on its calendar.

    Deliberately the same shape as `expiry_notice`: what happened, what it
    means, and the one tap that undoes it. The period has ended — that is the
    whole point of an allowance, as against a cap that withholds quietly — so
    saying it in different words would only make it read as a different and
    worse thing.

    What differs is the first line, which has to say that the days are not the
    reason. Somebody looking at a subscription with three weeks left on it and
    no alerts arriving will otherwise conclude the service is broken, and they
    would be right to.

    Three lines and no commands. Unused days and unused alerts do not carry
    over, which was said here and is now not: it answers a question nobody has
    yet asked at the moment they are deciding whether to pay again.
    """

    how_many = f"all {allowance}" if allowance else "all"
    if not share or share >= 100:
        opening = (
            f"You have had {how_many} alerts included in this month, so alerts "
            "have stopped."
        )
    else:
        opening = (
            f"You have had {how_many} alerts included in this month. You now "
            f"receive {share}% of what matches your filter."
        )

    # Three lines, and the commands are not among them. What happened, that
    # nothing has to be set up again, and the one tap that undoes it — anything
    # else here sits between the person and the button, and /update and /stop
    # are not what somebody reading this came to do.
    lines = [f"🔔 {opening}", KEPT]
    if not link:
        lines.append("/pay — a payment link, and the alerts resume at once")
    return "\n".join(lines)

def notice_for(
    plan: str, plan_until: datetime | None, stage: str, share: int | None = None,
    link: str | None = None, allowance: int | None = None,
) -> str:

    if stage == "expired":
        return expiry_notice(plan, share, link)
    if stage == "spent":
        return spent_notice(allowance, share, link)
    return expiring_notice(plan, plan_until, share, link)

def checkin_notice() -> str:
    """Asked five minutes before WhatsApp's 24-hour window shuts.

    Short on purpose: it is a question with two buttons, and every extra line
    is a line between the question and the answer. Tapping either button is an
    inbound message, which is what reopens the window — so even "I found a
    place" leaves us able to reply.

    Asked that late, and with alerts held from the moment it goes out, so that
    it is the last message in the conversation rather than one somewhere in it.
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

def digest_notice(matched: int, sent: int, share: int, *, paid: bool = False) -> str:
    """The evening summary. Short on purpose: it is read at a glance.

    `sent` is how many of the day's matches actually reached them, counted from
    `notifications` by the same query that counts the matches — not worked out
    here from the share. A number this message derived could disagree with what
    was delivered, and on the one line that says what somebody is missing, a
    figure that is nearly right is worse than a percentage.
    """

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

    # Nothing is being withheld by the plan, so there is nothing to sell.
    if paid or share >= 100:
        return f"🔔 {matched} new {listings} matched your filter today."

    # The share held nothing back today — a small day can round that way. Saying
    # "you are seeing only 3 of them" about 3 listings reads as a fault in the
    # bot, so the day is reported as what it was. The upgrade button is on the
    # message either way; `notify_plan_changes` puts it there.
    if sent >= matched:
        return f"🔔 {matched} new {listings} matched your filter today."

    # One listing, withheld, is "it". "None of them" and "every one of them" are
    # both about a plural that is not there.
    if matched == 1:
        return "🔒 1 new listing today, and you are not seeing it. Upgrade to get it."

    seen = "none of them" if sent == 0 else f"only {sent} of them"
    return (
        f"🔒 {matched} new {listings} today — you are seeing {seen}. "
        "Upgrade to get every one of them."
    )
