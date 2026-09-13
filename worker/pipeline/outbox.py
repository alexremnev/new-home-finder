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

MAX_ATTEMPTS = 5

BATCH = 250

ALERT_KINDS: dict[str, AlertKind] = {
    "new_listing": "listing",
    "price_drop": "listing",
    "welcome": "welcome",
    "stopped": "stopped",
}

def in_share(user_id: int, listing_id: int, share: int) -> bool:

    if share >= 100:
        return True
    if share <= 0:
        return False
    digest = hashlib.sha256(f"{user_id}:{listing_id}".encode()).digest()

    return (int.from_bytes(digest[:2], "big") % 100) < share

def queue_matches(conn: Conn, run: Run, *, source_key: str, listing_ids: list[int]) -> None:

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

                pending.append({
                    "user_id": user_id,
                    "subscription_id": int(subscription["id"]),
                    "listing_id": int(listing["id"]),
                    "channel": str(subscription["channel"]),
                    "kind": "new_listing",

                    "status": "skipped" if withheld else "queued",
                    "error": "share" if withheld else None,
                })

        written = store.queue_notifications(conn, pending)
        for row in pending:
            key = (row["user_id"], row["listing_id"])
            if key not in written:

                stage.count("already_notified")
            elif row["status"] == "skipped":
                stage.count("withheld_by_share")
            else:
                stage.count("queued")

SEED_COUNT = 5
SEED_DAYS = 3

SEED_POOL = 400

def seed_new_subscriptions(conn: Conn, run: Run, *, dry_run: bool = False) -> None:

    with run.stage("seed") as stage:
        owed = store.unseeded_subscriptions(conn)
        stage.set("subscriptions", len(owed))
        if not owed:
            return
        if dry_run:
            stage.set("suppressed", True)
            return

        pool = store.recent_listings(conn, days=SEED_DAYS, limit=SEED_POOL)
        stage.set("pool", len(pool))

        rows: list[dict[str, Any]] = []
        for subscription in owed:
            criteria = subscription["criteria"] or {}
            chosen = 0
            for listing in pool:
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

            store.mark_seeded(conn, int(subscription["id"]))
            stage.count("seeded")
            stage.count("offered", chosen)

        written = store.queue_notifications(conn, rows)
        stage.set("queued", len(written))

@dataclass(frozen=True)
class Outcome:

    status: str
    error: str | None = None
    stop_user: bool = False
    provider_msg_id: str | None = None
    cost_micros: int = 0

def outcome_for(result: SendResult, *, attempts: int, max_attempts: int = MAX_ATTEMPTS) -> Outcome:

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

        area=(row.get("raw") or {}).get("location"),
        address=(row.get("raw") or {}).get("address"),
        size_text=(row.get("raw") or {}).get("size"),

        share=(lambda v: int(v) if v is not None and int(v) < 100 else None)(
            row.get("delivery_share")
        ),
    )

def alert_for(row: Row) -> Alert | None:

    kind = ALERT_KINDS.get(str(row["kind"]))
    if kind != "listing":
        return None
    return Alert(kind=kind, listing=listing_view(row))

def interleave_by_user(batch: list[Row]) -> list[Row]:

    by_user: dict[int, list[Row]] = {}
    for row in batch:
        by_user.setdefault(int(row["user_id"]), []).append(row)
    if len(by_user) < 2:
        return batch

    out: list[Row] = []
    queues = list(by_user.values())
    while queues:

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

        notifiers: dict[str, Any] = {}

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

def notify_plan_changes(conn: Conn, run: Run, *, dry_run: bool = False) -> None:

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
