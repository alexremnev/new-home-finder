"""Matching and delivery.

The two stages are in one module because they are two halves of one guarantee:
a listing reaches each recipient exactly once. `match` decides and writes a row;
`notify` reads that row and sends. Nothing is sent without a row first, so a
crash at any point costs a retry rather than a lost or duplicated message.

Everything that decides *what* the recipient sees is a pure function here, and
everything that touches the database is in `worker.store`. That is what lets the
wording, the retry policy, and the daily cap be tested without a database and
without a bot token.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import psycopg

from worker import store
from worker.contracts.notify import Alert, AlertKind, ListingView, Recipient, SendResult
from worker.notify import build_notifier
from worker.notify.plans import notice_for, withheld_notice
from worker.obs import Run
from worker.pipeline.match import is_eligible, matches

Row = dict[str, Any]
Conn = psycopg.Connection[Row]

# How many delivery attempts a message gets before it is given up on. Telegram
# failures worth retrying are transient (429, 5xx); five spread across runs is
# far more time than any of those last.
MAX_ATTEMPTS = 5

# How many messages one drain may send.
#
# Sized against the queue, not against Telegram. Telegram accepts roughly 30
# messages a second to different chats, so this batch is about eight seconds of its
# time; what it has to keep up with is subscribers × matches. At 50 and a tick every
# ten minutes the ceiling was 300 messages an hour, which 500 subscribers can exceed
# in a quiet hour — and the backlog does not announce itself, it just delivers
# yesterday's listings today, which for this product is indistinguishable from being
# broken.
BATCH = 250

# `notifications.kind` names the event; `Alert.kind` names the message shape.
# They are not the same vocabulary, and mapping them here keeps a new event kind
# — a price drop, say — from needing a new channel.
ALERT_KINDS: dict[str, AlertKind] = {
    "new_listing": "listing",
    "price_drop": "listing",
    "welcome": "welcome",
    "stopped": "stopped",
}


def in_share(user_id: int, listing_id: int, share: int) -> bool:
    """Whether this listing is one of the share this subscriber receives.

    Deterministic, and that is the whole point. A random draw would mean the same
    listing was withheld on one run and delivered on the next, so "why did I not get
    this one" would have no answer and the behaviour could not be tested. Hashing
    the pair gives a stable, evenly spread yes or no.

    The pair, not the listing alone: keyed on the listing only, every free
    subscriber would receive the same tenth of the market, and a filter matching
    only listings outside that tenth would deliver nothing at all.
    """
    if share >= 100:
        return True
    if share <= 0:
        return False
    digest = hashlib.sha256(f"{user_id}:{listing_id}".encode()).digest()
    # First two bytes are plenty for a percentage and avoid the modulo bias that
    # one byte would give against 100.
    return (int.from_bytes(digest[:2], "big") % 100) < share


# ── match ─────────────────────────────────────────────────────────────────


def queue_matches(conn: Conn, run: Run, *, source_key: str, listing_ids: list[int]) -> None:
    """Queue an alert for every subscription each new listing satisfies."""
    with run.stage("match", source_key=source_key) as stage:
        stage.set("candidates", len(listing_ids))
        if not listing_ids:
            return

        subscriptions = store.active_subscriptions(conn)
        stage.set("subscriptions", len(subscriptions))
        if not subscriptions:
            stage.log("info", "no active subscriptions; nothing to queue")
            return

        listings = store.listings_for_matching(conn, listing_ids)
        pending: list[dict[str, Any]] = []

        for subscription in subscriptions:
            user_id = int(subscription["user_id"])
            criteria = subscription["criteria"] or {}

            for listing in listings:
                verdict = matches(criteria, listing)
                if not verdict:
                    stage.count("not_matched")
                    continue

                eligible = is_eligible(listing, backfill_from=subscription["backfill_from"])
                if not eligible:
                    stage.count("not_eligible")
                    continue

                share = int(subscription.get("delivery_share") or 100)
                withheld = not in_share(user_id, int(listing["id"]), share)
                # Collected, not written: one round trip for the batch instead of
                # one per match. At 500 subscribers a busy hour is thousands of
                # matches, and each insert was 20ms of network for a millisecond of
                # work — minutes per tick spent waiting.
                pending.append({
                    "user_id": user_id,
                    "subscription_id": int(subscription["id"]),
                    "listing_id": int(listing["id"]),
                    "channel": str(subscription["channel"]),
                    "kind": "new_listing",
                    # Recorded rather than merely not done: it is what the daily
                    # "N more matched" count is read from, and what gives "why did I
                    # not get this one" an answer.
                    "status": "skipped" if withheld else "queued",
                    "error": "share" if withheld else None,
                })

        written = store.queue_notifications(conn, pending)
        for row in pending:
            key = (row["user_id"], row["listing_id"])
            if key not in written:
                # Already sent to this user, most likely by another subscription of
                # theirs. The unique constraint is what makes overlapping filters
                # harmless.
                stage.count("already_notified")
            elif row["status"] == "skipped":
                stage.count("withheld_by_share")
            else:
                stage.count("queued")


# How many listings a new subscription is given, and how far back to look.
#
# Five is enough to show what an alert looks like and to be useful, and few enough
# that nobody mistakes it for a backlog being dumped on them. Three days because
# rental listings go stale in days — offering a fortnight-old flat as a first
# impression is worse than offering nothing.
SEED_COUNT = 5
SEED_DAYS = 3

# How many recent listings to consider per run. A ceiling so that a busy feed does
# not make seeding the most expensive thing a tick does; the newest are the ones
# worth offering anyway.
SEED_POOL = 400


def seed_new_subscriptions(conn: Conn, run: Run, *, dry_run: bool = False) -> None:
    """Give each new subscription its first few matches.

    Here rather than in the webhook because the matcher lives here. Deciding what
    suits a filter in TypeScript as well would be a second definition of the word
    "matches", and the two would drift — which is the one thing this project has
    consistently refused.

    `is_eligible` is deliberately not consulted. It exists to enforce
    `backfill_from`, and stepping over that once, under a cap, is the entire point:
    everything after this batch obeys it as before.
    """
    with run.stage("seed") as stage:
        owed = store.unseeded_subscriptions(conn)
        stage.set("subscriptions", len(owed))
        if not owed:
            return
        if dry_run:
            stage.set("suppressed", True)
            return

        # One query for the pool, however many subscriptions are owed a batch: the
        # candidates are the same for all of them.
        pool = store.recent_listings(conn, days=SEED_DAYS, limit=SEED_POOL)
        stage.set("pool", len(pool))

        rows: list[dict[str, Any]] = []
        for subscription in owed:
            criteria = subscription["criteria"] or {}
            chosen = 0
            for listing in pool:            # newest first
                if chosen >= SEED_COUNT:
                    break
                if not matches(criteria, listing):
                    continue
                rows.append({
                    "user_id": int(subscription["user_id"]),
                    "subscription_id": int(subscription["id"]),
                    "listing_id": int(listing["id"]),
                    "channel": str(subscription["channel"]),
                    "kind": "new_listing",
                    "status": "queued",
                    "error": None,
                })
                chosen += 1
            # Stamped either way. Retrying an empty batch for ever would mean a
            # quiet district is re-examined until something appears, and then sent
            # five at once days later as though they were new.
            store.mark_seeded(conn, int(subscription["id"]))
            stage.count("seeded")
            stage.count("offered", chosen)

        written = store.queue_notifications(conn, rows)
        stage.set("queued", len(written))


# ── notify ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Outcome:
    """What to do with a message after one attempt."""

    status: str  # sent | failed | queued
    error: str | None = None
    stop_user: bool = False
    provider_msg_id: str | None = None
    cost_micros: int = 0


def outcome_for(result: SendResult, *, attempts: int, max_attempts: int = MAX_ATTEMPTS) -> Outcome:
    """Turn one send result into a decision about the row.

    Three distinctions matter, and conflating any two of them is a real fault:
    a transient failure must be retried, a permanent one must not, and an
    unreachable recipient must also stop the subscription — repeatedly messaging
    someone who blocked the bot counts against the bot's standing with Telegram.
    """
    if result.ok:
        return Outcome(
            "sent", provider_msg_id=result.provider_msg_id, cost_micros=result.cost_micros
        )
    error = result.error or "unknown error"
    if result.recipient_gone:
        return Outcome("failed", error, stop_user=True)
    if not result.retryable:
        return Outcome("failed", error)
    if attempts >= max_attempts:
        return Outcome("failed", f"{error} (gave up after {attempts} attempts)")
    return Outcome("queued", error)


def listing_view(row: Row) -> ListingView:
    """The subset of a listing a message may show.

    Narrower than the stored row on purpose: facts and a link, never the site's
    own photographs or description.
    """
    return ListingView(
        price_pcm=int(row["price_pcm"]),
        bedrooms=int(row["bedrooms"]),
        property_type=row["property_type"],
        postcode=row.get("postcode"),
        district=row["postcode_district"],
        zone=row["tfl_zone"],
        available_from=row["available_from"],
        furnished=row["furnished"] or "unknown",
        pets_allowed=row["pets_allowed"],
        bills_included=row["bills_included"],
        min_tenancy_months=row["min_tenancy_months"],
        source_display=row["source_display"],
        is_landlord_direct=row["is_landlord_direct"],
        url=row["url"],
        bathrooms=row.get("bathrooms"),
        deposit_pcm=row.get("deposit_pcm"),
        # From `raw` because these have no column: a source that supplies them is
        # not a reason to widen `listings` for one that never will. `.get` on both
        # levels so a row written before the parser existed renders unchanged.
        area=(row.get("raw") or {}).get("location"),
        address=(row.get("raw") or {}).get("address"),
        size_text=(row.get("raw") or {}).get("size"),
    )


def alert_for(row: Row) -> Alert | None:
    """The message for one queued row, or None if there is nothing to send.

    Welcome and stop confirmations are answers to something the person just did,
    so the web app sends them in the moment rather than queueing them. One
    arriving here means a bad row, and it must fail visibly instead of going out
    as an empty message.
    """
    kind = ALERT_KINDS.get(str(row["kind"]))
    if kind != "listing":
        return None
    return Alert(kind=kind, listing=listing_view(row))


def interleave_by_user(batch: list[Row]) -> list[Row]:
    """Reorder a batch so consecutive messages go to different people.

    Telegram's two limits are different in kind: about 30 messages a second overall,
    but only about one a second *into a single chat*. A batch claimed in id order
    puts all four of one person's matches back to back, which is exactly the shape
    that earns a 429 — and the retry then delays everybody behind them.

    Round-robin across users fixes it without a single sleep: four people with four
    matches each are delivered as ABCD ABCD ABCD ABCD rather than AAAA BBBB. Only
    when one person has far more queued than anyone else do their messages end up
    adjacent, and by then there is nothing else to send instead.

    Order within a user is preserved, so listings still arrive oldest first.
    """
    by_user: dict[int, list[Row]] = {}
    for row in batch:
        by_user.setdefault(int(row["user_id"]), []).append(row)
    if len(by_user) < 2:
        return batch

    out: list[Row] = []
    queues = list(by_user.values())
    while queues:
        # `[:]` because the list is rebuilt each pass; mutating while iterating
        # would skip a queue every time one empties.
        for queue in queues[:]:
            out.append(queue.pop(0))
            if not queue:
                queues.remove(queue)
    return out


def drain(
    conn: Conn,
    run: Run,
    *,
    source_key: str | None = None,
    suppress: bool = False,
    dry_run: bool = False,
    limit: int = BATCH,
) -> str:
    """Send what is queued. Returns the stage status."""
    with run.stage("notify", source_key=source_key) as stage:
        if suppress or dry_run:
            waiting = store.queued_count(conn)
            stage.set("suppressed", True)
            stage.set("waiting", waiting)
            reason = "dry run" if dry_run else "quiet hours"
            stage.log("info", f"{waiting} message(s) held: {reason}")
            return "ok"

        batch = store.claim_queued(conn, limit=limit, max_attempts=MAX_ATTEMPTS)
        stage.set("claimed", len(batch))
        if not batch:
            return "ok"

        # One notifier per channel per batch: building it reads configuration, and
        # a channel that cannot be built should say so once, not per message.
        notifiers: dict[str, Any] = {}
        # Recipients found unreachable while working through this batch. Without
        # this, a user who has blocked the bot is messaged once per queued row
        # before anything stops it — which is the precise harm the unreachable
        # path exists to prevent.
        gone: set[int] = set()
        status = "ok"

        for row in interleave_by_user(batch):
            channel = str(row["channel"])
            user_id = int(row["user_id"])
            if user_id in gone:
                store.mark_skipped(conn, int(row["id"]), "recipient unreachable")
                stage.count("skipped")
                continue
            if channel not in notifiers:
                notifiers[channel] = build_notifier(channel)
            notifier = notifiers[channel]

            if notifier is None:
                store.mark_failed(conn, int(row["id"]), f"no notifier for channel {channel}")
                stage.count("failed")
                stage.degrade(f"channel {channel} has no implementation")
                status = "degraded"
                continue
            if not row["address"]:
                store.mark_failed(conn, int(row["id"]), f"no {channel} address for the user")
                stage.count("failed")
                status = "degraded"
                continue

            alert = alert_for(row)
            if alert is None:
                store.mark_failed(
                    conn, int(row["id"]), f"nothing to render for kind {row['kind']}"
                )
                stage.count("failed")
                status = "degraded"
                continue
            if not notifier.supports(alert.kind):
                store.mark_failed(
                    conn, int(row["id"]), f"{channel} does not support {alert.kind}"
                )
                stage.count("failed")
                status = "degraded"
                continue

            result = notifier.send(
                Recipient(channel=channel, address=str(row["address"])), alert
            )
            decision = outcome_for(result, attempts=int(row["attempts"]))
            if decision.stop_user:
                gone.add(user_id)
            _apply(conn, stage, int(row["id"]), user_id, decision)
            if decision.status != "sent":
                status = "degraded"

    return status


def _apply(conn: Conn, stage: Any, notification_id: int, user_id: int, decision: Outcome) -> None:
    if decision.status == "sent":
        stage.count("sent")
        store.mark_sent(
            conn, notification_id,
            provider_msg_id=decision.provider_msg_id, cost_micros=decision.cost_micros,
        )
        return

    assert decision.error is not None
    if decision.stop_user:
        # No address or provider text in the log: the run log is public.
        stage.log("warn", f"user {user_id} is unreachable; subscriptions deactivated",
                  user_id=user_id)
        store.stop_user(conn, user_id, reason="recipient unreachable")
        store.mark_failed(conn, notification_id, decision.error)
        stage.count("recipient_gone")
        return

    if decision.status == "failed":
        stage.log("warn", f"delivery failed for user {user_id}: {decision.error}",
                  user_id=user_id)
        store.mark_failed(conn, notification_id, decision.error)
        stage.count("failed")
        return

    stage.log("info", f"delivery deferred for user {user_id}: {decision.error}", user_id=user_id)
    store.leave_queued(conn, notification_id, decision.error)
    stage.count("retry_later")


# ── plan expiry ───────────────────────────────────────────────────────────


def notify_plan_changes(conn: Conn, run: Run, *, dry_run: bool = False) -> None:
    """Warn people a day and an hour before a plan ends, and once after.

    Sent directly rather than through the outbox, because the outbox is keyed to a
    listing and these messages are about none. It is also why a failure here is only
    logged: a courtesy message is not worth a retry queue of its own.

    The claim happens in the database — see `store.claim_plan_notices` — so a send
    that fails is not retried. That is the deliberate trade: repeating an
    "about to end" message hourly is worse than missing one, and the next stage will
    reach them anyway.

    Their filter is left in place at every stage. Someone who pays a week later
    should find it as they left it, and deleting it here would make an expiry
    indistinguishable from a `/stop`.
    """
    with run.stage("expire") as stage:
        if dry_run:
            stage.set("suppressed", True)
            return
        for row in store.withheld_digests(conn):
            notifier = build_notifier(str(row["channel"]))
            if notifier is None or not row["address"]:
                stage.count("digest_unreachable")
                continue
            result = notifier.send(
                Recipient(channel=str(row["channel"]), address=str(row["address"])),
                Alert(kind="expiring", text=withheld_notice(int(row["withheld"]))),
            )
            stage.count("digest_sent" if result.ok else "digest_failed")

        due = store.claim_plan_notices(conn)
        stage.set("notices", len(due))
        for row in due:
            notice_stage = str(row["stage"])
            notifier = build_notifier(str(row["channel"]))
            if notifier is None or not row["address"]:
                stage.count("unreachable")
                continue
            result = notifier.send(
                Recipient(channel=str(row["channel"]), address=str(row["address"])),
                Alert(
                    kind="expired" if notice_stage == "expired" else "expiring",
                    text=notice_for(str(row["plan"]), row["plan_until"], notice_stage),
                ),
            )
            if result.ok:
                stage.count(f"sent_{notice_stage}")
            else:
                stage.count("send_failed")
                stage.log(
                    "warn",
                    f"could not send the {notice_stage} plan notice to user "
                    f"{row['user_id']}: {result.error}",
                    user_id=int(row["user_id"]),
                )


__all__ = [
    "alert_for", "drain", "in_share", "interleave_by_user", "listing_view",
    "notify_plan_changes",
    "outcome_for", "queue_matches",
]
