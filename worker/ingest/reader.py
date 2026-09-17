from __future__ import annotations

import hashlib
import os
import pathlib
import sys
import re
from typing import Any

import psycopg

from worker import store
from worker.obs import Run

Row = dict[str, Any]
Conn = psycopg.Connection[Row]

BATCH = 300

# Photographs per run. Each is a download from Telegram and an upload to
# WhatsApp — about 200KB of traffic — and ingest runs every two minutes, so this
# is roughly the rate at which listings arrive.
PHOTO_BATCH = 10

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

def button_url(button: Any) -> str | None:

    for holder in (button, getattr(button, "type", None)):
        url = getattr(holder, "url", None)
        if url:
            return str(url)
    return None

def urls_of(message: Any) -> list[str]:

    found: list[str] = []

    markup = getattr(message, "reply_markup", None)
    for row in getattr(markup, "rows", None) or []:
        for button in getattr(row, "buttons", None) or []:
            url = button_url(button)
            if url:
                found.append(url)

    for entity in getattr(message, "entities", None) or []:
        url = getattr(entity, "url", None)
        if url:
            found.append(str(url))

    for match in re.finditer(r"https?://\S+", getattr(message, "text", None) or ""):
        found.append(match.group(0).rstrip(").,;"))

    return list(dict.fromkeys(found))

async def attach_photos(
    conn: Conn,
    client: Any,
    entity: Any,
    stage: Any,
    source_key: str,
    reader: str,
) -> int:

    # The photograph the feed sent, handed to WhatsApp and remembered by the id
    # it gives back. Done here because this is the one place with an open
    # Telegram client, and driven from the database so that messages stored
    # before any of this existed are picked up too.
    from worker.notify.whatsapp import configured, upload_image

    if not configured():
        stage.count("photos_skipped")
        return 0

    pending = store.messages_missing_photo(
        conn, source_key=source_key, reader=reader, limit=PHOTO_BATCH
    )
    if not pending:
        return 0

    kept = 0
    for row in pending:
        media_id = None
        try:
            message = await client.get_messages(entity, ids=int(row["external_id"]))
            if message is not None and getattr(message, "photo", None):
                blob = await client.download_media(message, file=bytes)
                if blob:
                    media_id = upload_image(blob)
        except Exception as exc:
            # A photograph nobody can fetch is not a reason to stop reading a
            # feed. It is marked as looked at so it is not tried forever.
            stage.log("warn", f"photo {row['external_id']}: {type(exc).__name__}: {exc}")

        store.set_message_photo(conn, int(row["id"]), media_id)
        if media_id:
            kept += 1

    stage.count("photos_kept", kept)
    stage.count("photos_missing", len(pending) - kept)
    return kept

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
                f"not set in the environment: {', '.join(missing)}. "
                "TG_API_ID/TG_API_HASH come from https://my.telegram.org -> API "
                "development tools; TG_SESSION is written by "
                "`python -m worker login --save <env file>`; TG_WATCH is the chat "
                "to read without the @; TG_READER names this reader and must differ "
                "between accounts, because the cursor is keyed by it."
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
                    "run `python -m worker login --save <env file>` again"
                )

            me = await client.get_me()
            if getattr(me, "bot", False):
                raise RuntimeError(
                    f"the session for reader {reader!r} is a BOT session, and Telegram "
                    "does not let bots read history. TG_SESSION has to come from "
                    "logging in as a person: run "
                    "`python -m worker login --save <env file>` and enter your "
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

                await attach_photos(conn, client, entity, stage, source_key, reader)

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
    "reader_name", "redact", "save_session", "urls_of", "watched",
]

def save_session(path: pathlib.Path, session: str) -> None:
    lines = path.read_text(encoding="utf-8").split("\n") if path.is_file() else []
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith("TG_SESSION="):
            lines[i] = f"TG_SESSION={session}"
            replaced = True
            break
    if not replaced:
        lines.append(f"TG_SESSION={session}")

    stat = path.stat() if path.is_file() else None
    mode = stat.st_mode & 0o777 if stat else 0o600
    temp = path.with_suffix(path.suffix + ".new")
    temp.write_text("\n".join(lines), encoding="utf-8")
    os.chmod(temp, mode)
    if stat is not None:
        try:
            os.chown(temp, stat.st_uid, stat.st_gid)
        except PermissionError:
            pass
    os.replace(temp, path)


async def login(save_to: str | None = None) -> int:

    target = pathlib.Path(save_to) if save_to else None
    if target and target.is_file():
        for line in target.read_text(encoding="utf-8").split("\n"):
            name, _, value = line.partition("=")
            if name.strip() in ("TG_API_ID", "TG_API_HASH") and not os.environ.get(name.strip()):
                os.environ[name.strip()] = value.strip()

    api_id = os.environ.get("TG_API_ID", "").strip()
    api_hash = os.environ.get("TG_API_HASH", "").strip()
    if not api_id.isdigit() or not api_hash:
        where = f"in {target}" if target else "in the environment"
        print(f"TG_API_ID and TG_API_HASH are not set {where}.", file=sys.stderr)
        if not target:
            print(
                "This command reads them from the environment unless you name a "
                "file: --save /etc/london-home-finder.env reads TG_API_ID and "
                "TG_API_HASH from it and writes TG_SESSION back into it.",
                file=sys.stderr,
            )
        print(
            "Get them from https://my.telegram.org -> API development tools.",
            file=sys.stderr,
        )
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

    session = client.session.save()
    await client.disconnect()
    print(f"\nSigned in as {me.first_name} (@{me.username or me.id}).")

    if target:
        save_session(target, session)
        print(f"TG_SESSION written to {target}.")
        print("Not printed: a session string is a live login, and a terminal that")
        print("wraps it is a terminal you cannot copy it out of correctly.")
        return 0

    print("\nPut this in .env as TG_SESSION. It is a live login to your account, so")
    print("treat it like a password and keep it out of the repository.")
    print("\nBetter: re-run with --save <path> and skip the copying, which is where")
    print("this goes wrong — a wrapped line copied back gives 'Incorrect padding'.\n")
    print(session)
    return 0
