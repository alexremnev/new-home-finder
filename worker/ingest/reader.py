from __future__ import annotations

import hashlib
import os
import sys
import re
from typing import Any

import psycopg

from worker import store
from worker.obs import Run

Row = dict[str, Any]
Conn = psycopg.Connection[Row]

BATCH = 300

WHITESPACE = re.compile(r"\s+")

MEDIA_KINDS = (
    "photo", "video", "document", "audio", "voice", "sticker", "gif", "poll",
)

REDACTED = "hs"

def redact(body: str | None) -> str:

    text = body or ""
    words = sorted(
        (w.strip() for w in (os.environ.get("TG_REDACT") or "").split(",") if w.strip()),
        key=len, reverse=True,
    )
    for word in words:
        text = re.sub(re.escape(word), REDACTED, text, flags=re.IGNORECASE)
    return text

def content_hash(body: str | None, links: list[str]) -> str:

    normalised = WHITESPACE.sub(" ", (body or "")).strip()
    material = normalised + "\n" + "\n".join(sorted(set(links)))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()

def urls_of(message: Any) -> list[str]:

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

    stored = 0
    with run.stage("read", source_key=source_key) as stage:

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

            raise RuntimeError(
                f"not set in the worker's .env: {', '.join(missing)}. "
                "TG_API_ID/HASH/SESSION come from tools/tg-mirror/.env (the session "
                "is printed once by `mirror.py login`); TG_WATCH is the chat to read "
                "without the @; TG_READER names this reader and must differ between "
                "accounts, because the cursor is keyed by it."
            )

        reader = reader_name()
        stage.set("reader", reader)

        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
        except ModuleNotFoundError as absent:
            raise RuntimeError(
                "Telethon is not installed. It is an optional extra so that hosts "
                "which only scrape need not have it: uv sync --extra ingest"
            ) from absent

        client = TelegramClient(StringSession(session), int(api_id), api_hash)
        await client.connect()
        try:
            if not await client.is_user_authorized():

                raise RuntimeError(
                    f"the session for reader {reader!r} is no longer authorised — "
                    "run mirror.py login again"
                )

            me = await client.get_me()
            if getattr(me, "bot", False):
                raise RuntimeError(
                    f"the session for reader {reader!r} is a BOT session, and Telegram "
                    "does not let bots read history. TG_SESSION has to come from "
                    "logging in as a person: run `mirror.py login` and enter your "
                    "phone number at the prompt, not a bot token. The bot token "
                    "belongs in TELEGRAM_TOKEN, which is a different thing — it sends "
                    "alerts, it does not read the feed."
                )
            stage.set("account", getattr(me, "username", None) or getattr(me, "id", None))

            for chat in watched():
                try:
                    entity = await client.get_entity(chat)
                except (ValueError, TypeError):
                    stage.degrade(f"no such chat in this account: {chat}")
                    continue

                cursor = store.ingest_cursor(conn, reader=reader, source_key=source_key)
                highest = cursor
                seen = 0

                batch: list[dict[str, Any]] = []
                hashes: set[str] = set()

                async for message in client.iter_messages(
                    entity, min_id=cursor, reverse=True, limit=limit
                ):
                    seen += 1
                    highest = max(highest, int(message.id))
                    if message.out:

                        continue

                    links = urls_of(message)

                    body = redact(message.text)
                    if not body and not links:
                        continue

                    digest = content_hash(body, links)
                    if digest in hashes:
                        stage.count("already_known")
                        continue
                    hashes.add(digest)

                    batch.append({
                        "source_key": source_key,
                        "reader": reader,
                        "external_id": str(message.id),
                        "received_at": message.date,
                        "body": body,
                        "links": links,
                        "media_kinds": media_of(message),
                        "content_hash": digest,
                    })

                written = store.store_source_messages(conn, batch)
                stored += written
                stage.count("already_known", len(batch) - written)

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
    "BATCH", "REDACTED", "collect", "content_hash", "login", "media_of",
    "reader_name", "redact", "urls_of", "watched",
]

async def login() -> int:

    api_id = os.environ.get("TG_API_ID", "").strip()
    api_hash = os.environ.get("TG_API_HASH", "").strip()
    if not api_id.isdigit() or not api_hash:
        print("TG_API_ID and TG_API_HASH must be set in .env first.", file=sys.stderr)
        print("Get them from https://my.telegram.org -> API development tools.", file=sys.stderr)
        return 2

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ModuleNotFoundError:
        print("Telethon is not installed: uv sync --extra ingest", file=sys.stderr)
        return 2

    client = TelegramClient(StringSession(), int(api_id), api_hash)
    await client.start()
    me = await client.get_me()
    if getattr(me, "bot", False):

        print(
            "\nThat was a BOT token. A bot cannot read history — enter your phone "
            "number instead. The bot token belongs in TELEGRAM_TOKEN.",
            file=sys.stderr,
        )
        await client.disconnect()
        return 2

    print(f"\nSigned in as {me.first_name} (@{me.username or me.id}).")
    print("\nPut this in .env as TG_SESSION. It is a live login to your account, so")
    print("treat it like a password and keep it out of the repository:\n")
    print(client.session.save())
    await client.disconnect()
    return 0
