"""Reading a Telegram feed into `source_messages`.

The first half of ingest. It stores raw messages and nothing else: no parsing, no
matching, no judgement about what a message means. That narrowness is the point —
this is the one component that cannot be run anywhere, cannot be run twice at once
per account, and cannot be re-run over the past, so everything that *can* be
retried belongs in the job after it.

── why this is not tools/tg-mirror ──────────────────────────────────────────

The mirror forwards messages to a chat and keeps its cursor in a file beside itself.
That is right for a personal tool on one machine, and it is what makes it useless as
a source: a file cannot be shared by two readers on two hosts, and a rebuilt host
loses it. This writes to the database, takes its cursor from `ingest_cursors`, and
therefore composes — a second account is a second `TG_READER`, not a second design.

The mirror is left alone rather than converted, because it is being used for
something else and the two have different failure modes worth keeping apart.

── two accounts ─────────────────────────────────────────────────────────────

`TG_READER` names the account. Everything is keyed by it: the cursor, and the
per-reader uniqueness on `source_messages`. What deduplicates *between* readers is
the content hash — two accounts reading one feed see the same listing under
different message ids, so identity has to come from the content.

── the dependency ───────────────────────────────────────────────────────────

Telethon is an optional extra (`uv sync --extra ingest`), not a base dependency.
The scraper has no business requiring an MTProto client to run, and a host that
only scrapes should not install one. Importing this module without it fails with an
instruction rather than a traceback.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any

import psycopg

from worker import store
from worker.obs import Run

Row = dict[str, Any]
Conn = psycopg.Connection[Row]

# How many messages one pass may take. A first run against a busy feed would
# otherwise pull the entire visible history in one transaction; the cursor advances
# regardless, so the rest arrives on the next pass.
BATCH = 300

# Collapses runs of whitespace so that a cosmetic reflow — a line wrapped
# differently, a trailing space removed — does not present the same listing as new
# content. Case is kept: postcodes and prices are case-sensitive in practice.
WHITESPACE = re.compile(r"\s+")

MEDIA_KINDS = (
    "photo", "video", "document", "audio", "voice", "sticker", "gif", "poll",
)

# What the stored body says instead of the source's own brand. Every message the
# feed sends signs itself — "upgrade to X Premium" — and that name would otherwise
# sit in our database, in backups, and in every report drawn from them. It is no use
# to the parser and no business of ours to keep.
REDACTED = "hs"


def redact(body: str | None) -> str:
    """Replace the source's names with a placeholder before storing.

    The words come from `TG_REDACT` in the environment, comma-separated, so the code
    never names them — same reasoning as `TG_WATCH`. Longest first, because a brand
    and its bot handle overlap ("X" inside "XUK_bot") and replacing the short one
    first would leave the tail behind.

    Applied on the way in rather than on the way out: a value scrubbed at read time
    is still in the row, and the row is what gets backed up.
    """
    text = body or ""
    words = sorted(
        (w.strip() for w in (os.environ.get("TG_REDACT") or "").split(",") if w.strip()),
        key=len, reverse=True,
    )
    for word in words:
        text = re.sub(re.escape(word), REDACTED, text, flags=re.IGNORECASE)
    return text


def content_hash(body: str | None, links: list[str]) -> str:
    """The identity of a message's content, for deduplicating across readers.

    Links are sorted because two readers can be given them in a different order,
    and the same listing must hash the same for both. Included at all because a feed
    that repeats a body with a different listing link is two listings, not one.
    """
    normalised = WHITESPACE.sub(" ", (body or "")).strip()
    material = normalised + "\n" + "\n".join(sorted(set(links)))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def urls_of(message: Any) -> list[str]:
    """Every url the message carries, in the order a parser should prefer them.

    All three places, because a listing feed uses the two that `message.text` does
    not reach: a url hidden behind a hyperlinked word, and a url on an inline
    button. Buttons first — that is where the listing itself lives, while the text
    usually holds a map pin.
    """
    found: list[str] = []

    markup = getattr(message, "reply_markup", None)
    for row in getattr(markup, "rows", None) or []:
        for button in getattr(row, "buttons", None) or []:
            url = getattr(button, "url", None)
            if url:
                found.append(str(url))

    for entity in getattr(message, "entities", None) or []:
        url = getattr(entity, "url", None)
        if url:
            found.append(str(url))

    for match in re.finditer(r"https?://\S+", getattr(message, "text", None) or ""):
        found.append(match.group(0).rstrip(").,;"))

    return list(dict.fromkeys(found))


def media_of(message: Any) -> list[str]:
    return [kind for kind in MEDIA_KINDS if getattr(message, kind, None)]


def reader_name() -> str:
    name = (os.environ.get("TG_READER") or "").strip()
    if not name:
        # Refused rather than defaulted: two hosts silently sharing the name
        # "default" would share one cursor, and each would skip what the other read.
        raise RuntimeError(
            "TG_READER is not set. Name this reader — the cursor and the per-reader "
            "uniqueness are keyed by it, so two accounts must not share a name."
        )
    return name


def watched() -> list[str]:
    raw = (os.environ.get("TG_WATCH") or "").strip()
    chats = [chat.strip().lstrip("@") for chat in raw.split(",") if chat.strip()]
    if not chats:
        raise RuntimeError("TG_WATCH is not set. Name the chat to read, without the @.")
    return chats


async def collect(
    conn: Conn, run: Run, *, source_key: str = "tg_feed", limit: int = BATCH,
    dry_run: bool = False,
) -> int:
    """Read what is new into `source_messages`. Returns how many rows were stored."""
    stored = 0
    with run.stage("read", source_key=source_key) as stage:
        # Before the credentials, so that --dry-run exercises the wiring — the job
        # dispatch, the stage, the database connection — on a host that has no
        # session at all. Checking them first made a dry run impossible to use for
        # the one thing it is for.
        if dry_run:
            stage.set("suppressed", True)
            return 0

        api_id = os.environ.get("TG_API_ID", "").strip()
        api_hash = os.environ.get("TG_API_HASH", "").strip()
        session = os.environ.get("TG_SESSION", "").strip()
        missing = [
            name for name, value in (
                ("TG_API_ID", api_id if api_id.isdigit() else ""),
                ("TG_API_HASH", api_hash),
                ("TG_SESSION", session),
                ("TG_WATCH", os.environ.get("TG_WATCH", "").strip()),
                ("TG_READER", os.environ.get("TG_READER", "").strip()),
            ) if not value
        ]
        if missing:
            # All of them at once, in the worker's own .env rather than the
            # mirror's: naming one at a time turns setting this up into five runs.
            raise RuntimeError(
                f"not set in the worker's .env: {', '.join(missing)}. "
                "TG_API_ID/HASH/SESSION come from tools/tg-mirror/.env (the session "
                "is printed once by `mirror.py login`); TG_WATCH is the chat to read "
                "without the @; TG_READER names this reader and must differ between "
                "accounts, because the cursor is keyed by it."
            )

        reader = reader_name()
        stage.set("reader", reader)

        # Imported here, not at module scope, so that neither --dry-run nor merely
        # importing this module requires an MTProto client. A host that only scrapes
        # has no reason to install one.
        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
        except ModuleNotFoundError as absent:  # pragma: no cover - a setup error
            raise RuntimeError(
                "Telethon is not installed. It is an optional extra so that hosts "
                "which only scrape need not have it: uv sync --extra ingest"
            ) from absent

        client = TelegramClient(StringSession(session), int(api_id), api_hash)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                # A revoked session is the failure this component fails at silently,
                # so it is raised rather than logged: the run turns red and
                # scripts/report.py notices the gap either way.
                raise RuntimeError(
                    f"the session for reader {reader!r} is no longer authorised — "
                    "run mirror.py login again"
                )

            for chat in watched():
                try:
                    entity = await client.get_entity(chat)
                except (ValueError, TypeError):
                    stage.degrade(f"no such chat in this account: {chat}")
                    continue

                cursor = store.ingest_cursor(conn, reader=reader, source_key=source_key)
                highest = cursor
                seen = 0

                # `min_id=cursor` with `reverse=True` walks forward from where this
                # reader stopped, oldest first, so the cursor can be advanced as it
                # goes and an interrupted pass loses nothing.
                async for message in client.iter_messages(
                    entity, min_id=cursor, reverse=True, limit=limit
                ):
                    seen += 1
                    highest = max(highest, int(message.id))
                    if message.out:
                        # Our own messages to the feed — a /start, a filter change.
                        # Not listings, and storing them would give the parser
                        # nothing to do but reject them.
                        continue

                    links = urls_of(message)
                    # Redacted before the hash is taken, so two readers agree on the
                    # identity of a message and a change to TG_REDACT does not make
                    # everything look new.
                    body = redact(message.text)
                    if not body and not links:
                        continue

                    if store.store_source_message(
                        conn,
                        source_key=source_key,
                        reader=reader,
                        external_id=str(message.id),
                        received_at=message.date,
                        body=body,
                        links=links,
                        media_kinds=media_of(message),
                        content_hash=content_hash(body, links),
                    ):
                        stored += 1
                    else:
                        # Already known: this reader has seen it, or the other
                        # reader stored the same content first. Both are ordinary.
                        stage.count("already_known")

                if highest > cursor:
                    store.set_ingest_cursor(
                        conn, reader=reader, source_key=source_key, last_external_id=highest
                    )
                stage.count("read", seen)

        finally:
            await client.disconnect()

        stage.set("stored", stored)
    return stored


__all__ = [
    "BATCH", "REDACTED", "collect", "content_hash", "media_of", "reader_name",
    "redact", "urls_of", "watched",
]
