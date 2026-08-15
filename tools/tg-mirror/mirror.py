"""Copy what a bot sends *you* into a chat of your own.

Standalone on purpose: nothing here imports from `worker/`, nothing here touches
the scraper's database, and its dependency (Telethon) is not in the project's
pyproject. The two can be broken, upgraded, and deleted independently.

Why this exists at all, and why it cannot be a bot
--------------------------------------------------
The Bot API cannot read another bot's traffic. A bot has no access to a second
bot's updates, Telegram does not deliver one bot's messages to another even in a
shared group, and a bot's *outgoing* messages are not exposed to anybody — not
even to itself. So there is no bot-shaped solution to "copy what @HomeScoutUK_bot
sends me" when that bot belongs to someone else.

What is left is the obvious thing: those messages are already in your account.
This signs in as *you* over MTProto, reads your own conversation with the bot, and
re-sends each new message where you want it. No token for the other bot is needed
or possible.

Two consequences worth knowing before you run it
------------------------------------------------
A session string is a live login. Anyone holding it is signed in as you, without
your password and without a code — it is closer to a cookie for your whole account
than to a password. Keep it in `.env` or a secret store, never in the repository,
and revoke it from Telegram → Settings → Devices if it leaks.

Automating a user account is not what user accounts are for, and Telegram polices
it. Reading your own incoming messages at a human pace is the mild end of that
spectrum, but the account carries the risk, not a bot. Long-lived logins from one
machine look ordinary; repeated logins from rotating datacentre IPs do not, which
is the argument for a laptop or a small VPS over CI.

Delivery
--------
`--via bot` (default) re-sends through your own bot, which is why
`TG_DEST_BOT_TOKEN` exists. The photo comes too: file ids belong to the bot that
saw them, so a second bot cannot reference the same file and the bytes have to be
downloaded and re-uploaded through this machine. Add `--no-media` to skip that.

Links get the same treatment for the same reason — reconstruction, not
reference. A listing bot puts them where `message.text` does not reach: hidden
behind a hyperlinked word, or on an inline keyboard button. Both are dug out and
appended as bare URLs, so nothing that could be opened is lost.

`--via forward` forwards natively, as you. Media, formatting, buttons and the
"forwarded from" header all survive untouched, Telegram moves the file
server-side so nothing is downloaded, and no bot token is involved — but your
account must be a member of the destination chat.

Usage
-----
    python mirror.py login                 # once, interactively: prints a session
    python mirror.py dump --limit 5        # print messages in full; changes nothing
    python mirror.py once                  # forward what is new, then exit (cron)
    python mirror.py once --backfill 3     # also take the last 3, to prove it works
    python mirror.py once --dry-run        # print, send nothing, remember nothing
    python mirror.py once --no-media       # text and links only, no upload
    python mirror.py watch                 # stay connected and forward live
    python mirror.py watch --via forward   # forward as yourself, nothing rebuilt

The cursor is the last id already forwarded, per chat, in `state.json` beside this
file. A first run with no cursor forwards nothing and records where it started —
otherwise switching this on would replay the entire history into the destination.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import re
import sys
from datetime import datetime
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
STATE = HERE / "state.json"
LIMIT = 4096
# A caption is not a message: Telegram allows 4096 characters of text and only
# 1024 attached to a photo. Anything longer has to travel as its own message, or
# the tail is silently lost.
CAPTION_LIMIT = 1024
# The Bot API's upload ceilings. A photo over the first is still deliverable as a
# document, which is worth doing: a large photo is usually the interesting one.
PHOTO_BYTES = 10 * 1024 * 1024
UPLOAD_BYTES = 50 * 1024 * 1024
PAUSE_SECONDS = 1.1

# Wording the source bot uses that is not worth mirroring verbatim. Add a pair
# here rather than editing describe(); the substitution is deliberately the only
# thing done to the text, so what arrives is otherwise exactly what was sent.
#
# Case-insensitive, and trailing punctuation is swallowed with the phrase —
# "…posted!" would otherwise leave "NEW ALERT!" with an exclamation mark that was
# never part of the replacement.
#
# Punctuation only, deliberately not `\s`: matching whitespace here would eat the
# newline after the phrase and glue the replacement onto the first line of the
# body — "NEW ALERT£1,950/mo".
REWRITES: tuple[tuple[str, str], ...] = (
    (r"A listing matching your criteria has just been posted[!.:]*", "NEW ALERT"),
)

try:
    from telethon import TelegramClient, events
    from telethon.errors import (
        AuthKeyUnregisteredError,
        FloodWaitError,
        SessionPasswordNeededError,
    )
    from telethon.sessions import StringSession
except ModuleNotFoundError:  # pragma: no cover - a setup error, not a runtime one
    sys.exit(
        "mirror: Telethon is not installed.\n"
        "  python3 -m venv .venv && . .venv/bin/activate\n"
        "  pip install -r requirements.txt\n"
        "Install it in a virtualenv of its own — it is deliberately absent from the "
        "project's pyproject so the scraper never depends on it."
    )


class Fault(RuntimeError):
    """Something the operator has to fix. Reported as one line, without a traceback."""


# ----------------------------------------------------------------- configuration


def load_env() -> None:
    """Read `.env` beside this script. Real environment variables win, so a stale
    file checked out next to CI secrets cannot override them."""
    path = HERE / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip().strip("\"'")
        if name and not os.environ.get(name):
            os.environ[name] = value


def need(name: str, hint: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise Fault(f"{name} is not set. {hint}")
    return value


def credentials() -> tuple[int, str]:
    api_id = need("TG_API_ID", "Create an app at https://my.telegram.org → API development tools.")
    api_hash = need("TG_API_HASH", "It is shown beside the api_id on the same page.")
    if not api_id.isdigit():
        raise Fault(f"TG_API_ID must be the number from my.telegram.org, got {api_id!r}")
    return int(api_id), api_hash


def watched() -> list[str]:
    raw = os.environ.get("TG_WATCH") or "HomeScoutUK_bot"
    names = [name.strip().lstrip("@") for name in raw.split(",") if name.strip()]
    if not names:
        raise Fault("TG_WATCH is empty. Name at least one chat, for example HomeScoutUK_bot")
    return names


# ------------------------------------------------------------------------- state


def read_state() -> dict[str, int]:
    if not STATE.is_file():
        return {}
    try:
        loaded = json.loads(STATE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # A truncated file would otherwise crash every run from now on. Starting
        # over costs one gap; refusing to start costs every message after it.
        print(f"mirror: {STATE.name} is not readable JSON, starting from scratch", file=sys.stderr)
        return {}
    return {str(k): int(v) for k, v in loaded.items() if str(v).lstrip("-").isdigit()}


def write_state(state: dict[str, int]) -> None:
    # Written to a neighbour and moved into place: an interrupted write must not
    # leave a half-file where the cursor used to be.
    temporary = STATE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(STATE)


# ---------------------------------------------------------------------- delivery


def post(token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return dict(json.load(response))
    except urllib.error.HTTPError as exc:
        try:
            return dict(json.load(exc))
        except Exception:  # noqa: BLE001 - a non-JSON body still carries a status
            return {"ok": False, "error_code": exc.code, "description": exc.reason}
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return {"ok": False, "error_code": 0, "description": f"{type(exc).__name__}: {exc}"}


def post_multipart(
    token: str, method: str, fields: dict[str, Any],
    file_field: str, filename: str, mime: str, content: bytes,
) -> dict[str, Any]:
    """Upload one file with its text fields, as multipart/form-data.

    Hand-rolled rather than pulled from a library: `requests` would be a second
    dependency for one request, and the encoding is a dozen lines. The boundary is
    random so it cannot collide with bytes inside a photo — a fixed one would
    corrupt the upload on exactly the file that happened to contain it.
    """
    boundary = "----mirror" + os.urandom(16).hex()
    parts: list[bytes] = []
    for name, value in fields.items():
        if value is None:
            continue
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
            f"{value}\r\n".encode()
        )
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
        f"filename=\"{filename}\"\r\nContent-Type: {mime}\r\n\r\n".encode()
    )
    body = b"".join(parts) + content + f"\r\n--{boundary}--\r\n".encode()

    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        # Generous: a few megabytes over a domestic uplink is not a 30s operation.
        with urllib.request.urlopen(request, timeout=180) as response:
            return dict(json.load(response))
    except urllib.error.HTTPError as exc:
        try:
            return dict(json.load(exc))
        except Exception:  # noqa: BLE001 - a non-JSON body still carries a status
            return {"ok": False, "error_code": exc.code, "description": exc.reason}
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return {"ok": False, "error_code": 0, "description": f"{type(exc).__name__}: {exc}"}


def links_in(message: Any) -> list[str]:
    """Every URL the message carries, including the ones `message.text` does not show.

    Three places hold links and only one of them is the visible text:

      a bare https://… in the body            — already in `message.text`
      a word hyperlinked to somewhere else    — the text says "View", the URL lives
                                                on the entity
      an inline keyboard button               — the URL is not in the text at all

    A listing bot uses the last two for exactly the links worth having, so dropping
    them would mean mirroring an advert with no way to open it.
    """
    found: list[str] = []
    for entity in getattr(message, "entities", None) or []:
        url = getattr(entity, "url", None)
        if url:
            found.append(str(url))
    markup = getattr(message, "reply_markup", None)
    for row in getattr(markup, "rows", None) or []:
        for button in getattr(row, "buttons", None) or []:
            url = getattr(button, "url", None)
            if url:
                found.append(str(url))

    # Dropped if the body already shows it, so a plain link is not printed twice.
    # Order is preserved and duplicates removed: the bot's own ordering is
    # meaningful, and a dict keeps it while a set would not.
    body = getattr(message, "text", None) or ""
    return list(dict.fromkeys(url for url in found if url not in body))


def media_label(message: Any) -> str | None:
    for attribute, label in (
        ("photo", "photo"), ("video", "video"), ("document", "document"),
        ("audio", "audio"), ("voice", "voice"), ("sticker", "sticker"),
        ("gif", "gif"), ("contact", "contact"), ("geo", "location"), ("poll", "poll"),
    ):
        if getattr(message, attribute, None):
            return label
    return "media" if getattr(message, "media", None) else None


def rewritten(text: str) -> str:
    for pattern, replacement in REWRITES:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text.strip()


def describe(message: Any, source: str, *, media_attached: bool = False) -> str:
    """One message as plain text.

    No parse mode anywhere: this is text somebody else wrote, so any markup it
    happens to contain would be a rejected message rather than an ugly one. Which
    is also why hidden links are spelled out below instead of being re-linked —
    a visible URL cannot be mangled by an escaping mistake.

    No provenance header. There was one — source, date, message id — and it earned
    its place only if copies from several bots share a chat. With one source it is
    three facts nobody reads above every alert, and the id in particular belongs in
    the log rather than in the message. `source` is kept in the signature because
    the log lines and any future multi-source header need it.

    `media_attached` says the photo is travelling with this text as its caption,
    so the "[photo]" placeholder is not needed and would only be noise.
    """
    label = None if media_attached else media_label(message)
    text = rewritten(message.text or "")
    if label and text:
        text = f"[{label}]\n{text}"
    elif label:
        text = f"[{label}, no text]"
    elif not text:
        text = "" if media_attached else "[no text]"

    extra = links_in(message)
    if extra:
        text = (text + "\n\n" if text else "") + "\n".join(extra)

    return text if len(text) <= LIMIT else text[: LIMIT - 14].rstrip() + "\n… (truncated)"


class Destination:
    """Where copies go, and how. Both routes expose the same `send`, so the
    forwarding loop does not branch on the choice."""

    def __init__(self, via: str, chat: str, token: str | None, client: Any,
                 media: bool = True) -> None:
        self.via = via
        self.chat: str | int = int(chat) if chat.lstrip("-").isdigit() else chat
        self.token = token
        self.client = client
        self.media = media

    @classmethod
    def build(cls, via: str, client: Any, media: bool = True) -> Destination:
        chat = need(
            "TG_DEST_CHAT",
            "The chat id copies go to. Your own chat id works; a private group works too.",
        )
        token = None
        if via == "bot":
            token = need(
                "TG_DEST_BOT_TOKEN",
                "The token of *your* bot, from @BotFather → /mybots → API Token. "
                "Or use --via forward, which needs no bot at all.",
            )
        return cls(via, chat, token, client, media)

    def check(self, response: dict[str, Any]) -> None:
        if response.get("ok"):
            return
        code = response.get("error_code") or 0
        raise Fault(
            f"your bot could not deliver to {self.chat}: "
            f"{code}: {response.get('description') or 'unknown error'}"
            + (
                "\nA bot cannot message a chat it has never been spoken to — open "
                "your bot and press Start, or add it to the destination group."
                if code == 403 else ""
            )
        )

    async def send(self, message: Any, source: str) -> None:
        if self.via == "forward":
            # Nothing to reconstruct: a native forward carries the photo, the
            # formatting, the buttons and the "forwarded from" header as they are.
            await self.client.forward_messages(self.chat, message)
            return

        assert self.token is not None
        upload = await self.fetch_media(message) if self.media else None
        if upload is None:
            self.check(post(self.token, "sendMessage", {
                "chat_id": self.chat,
                "text": describe(message, source),
                "disable_web_page_preview": True,
            }))
            return

        method, field, filename, mime, content = upload
        # The caption holds a quarter of what a message holds, so a long body
        # travels separately rather than being cut off at 1024. Deliberately not
        # both: a duplicated caption reads as a bug.
        caption = describe(message, source, media_attached=True)
        fits = len(caption) <= CAPTION_LIMIT
        self.check(post_multipart(
            self.token, method,
            # `or None` because a photo with no text now describes to an empty
            # string, and an empty caption field is noise on the wire.
            {"chat_id": self.chat, "caption": (caption if fits else None) or None},
            field, filename, mime, content,
        ))
        if not fits:
            await asyncio.sleep(PAUSE_SECONDS)
            self.check(post(self.token, "sendMessage", {
                "chat_id": self.chat,
                "text": describe(message, source, media_attached=True),
                "disable_web_page_preview": True,
            }))

    async def fetch_media(self, message: Any) -> tuple[str, str, str, str, bytes] | None:
        """Download the attachment, ready to re-upload. None when there is nothing
        to send, or nothing that can be sent.

        A bot cannot re-use the other bot's file id — file ids are scoped to the bot
        that saw them — so the bytes genuinely have to make the round trip through
        this machine. That is the price of `--via bot`; `--via forward` pays none of
        it because Telegram moves the file server-side.
        """
        if not getattr(message, "media", None):
            return None
        # A link preview is `media` too, and there is no file behind it. Sending
        # nothing is right: the URL is already in the text.
        if getattr(message, "web_preview", None) or getattr(message, "poll", None):
            return None

        info = getattr(message, "file", None)
        size = getattr(info, "size", None) or 0
        if size > UPLOAD_BYTES:
            return None  # describe() will name it instead

        try:
            content = await self.client.download_media(message, file=bytes)
        except Exception as error:  # noqa: BLE001 - any failure here degrades to text
            print(f"mirror: could not download the attachment of msg {message.id} "
                  f"({type(error).__name__}), sending the text alone", file=sys.stderr)
            return None
        if not content:
            return None

        extension = getattr(info, "ext", None) or ""
        mime = getattr(info, "mime_type", None) or "application/octet-stream"
        name = getattr(info, "name", None) or f"msg{message.id}{extension or '.bin'}"

        if getattr(message, "photo", None) and len(content) <= PHOTO_BYTES:
            return "sendPhoto", "photo", name or "photo.jpg", mime or "image/jpeg", content
        if getattr(message, "video", None):
            return "sendVideo", "video", name, mime, content
        if getattr(message, "voice", None):
            return "sendVoice", "voice", name, mime, content
        if getattr(message, "audio", None):
            return "sendAudio", "audio", name, mime, content
        if getattr(message, "gif", None):
            return "sendAnimation", "animation", name, mime, content
        # Everything else, including a photo too large for sendPhoto, goes as a
        # document: it keeps the bytes intact and Telegram still previews images.
        return "sendDocument", "document", name, mime, content


# ---------------------------------------------------------------------- the work


async def connect(api_id: int, api_hash: str) -> Any:
    session = need(
        "TG_SESSION",
        "Run `python mirror.py login` once and put the printed string here.",
    )
    client = TelegramClient(StringSession(session), api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        raise Fault(
            "that session is not authorised any more — it was probably revoked in "
            "Telegram → Settings → Devices. Run `python mirror.py login` again."
        )
    return client


async def do_login() -> int:
    api_id, api_hash = credentials()
    client = TelegramClient(StringSession(), api_id, api_hash)
    try:
        await client.start()  # prompts for the phone, the code, and 2FA if enabled
    except SessionPasswordNeededError:
        return fail("two-step verification is on; enter the password when prompted")
    me = await client.get_me()
    print(f"\nSigned in as {me.first_name} (@{me.username or me.id}).")
    print("\nPut this in .env as TG_SESSION — it is a live login to your account,")
    print("so treat it like a password and keep it out of the repository:\n")
    print(client.session.save())
    await client.disconnect()
    return 0


async def deliver_batch(
    client: Any, destination: Destination, source: str, messages: list[Any], dry_run: bool
) -> tuple[int, int, str]:
    """Send in order, stopping at the first failure.

    Returns the highest id actually delivered, how many were sent, and the problem
    that stopped it. Both numbers are needed and neither implies the other: the id
    is what the cursor may advance to, the count is what the log reports, and a
    single message delivered from a batch of nine gives a high id and a count of
    one. Stopping matters — the cursor may only advance over messages that
    arrived, or one failure would silently swallow everything queued behind it.
    """
    delivered = 0
    count = 0
    for index, message in enumerate(messages):
        if dry_run:
            print(f"\n--- msg {message.id} ---\n{describe(message, source)}")
            delivered, count = message.id, count + 1
            continue
        if index:
            await asyncio.sleep(PAUSE_SECONDS)
        try:
            await destination.send(message, source)
        except FloodWaitError as error:
            return delivered, count, f"Telegram asked for a {error.seconds}s pause; stopping here"
        except Fault as error:
            return delivered, count, str(error)
        delivered, count = message.id, count + 1
    return delivered, count, ""


def stamp() -> str:
    """Local wall-clock time, for the log. Seconds included: two runs in the same
    minute happen the first time anything is tested by hand."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def do_once(args: argparse.Namespace) -> int:
    started = stamp()
    api_id, api_hash = credentials()
    client = await connect(api_id, api_hash)
    destination = Destination.build(args.via, client, media=not args.no_media)
    state = read_state()
    problems: list[str] = []
    sent_total = 0
    per_source: list[str] = []

    try:
        for source in watched():
            try:
                entity = await client.get_entity(source)
            except (ValueError, TypeError):
                problems.append(
                    f"@{source}: no such chat in this account. Open it in Telegram and "
                    "send it something first — an account can only read dialogs it has."
                )
                continue

            cursor = state.get(source)
            if cursor is None:
                # No cursor: record where we are instead of replaying the history.
                newest = await client.get_messages(entity, limit=1)
                start = newest[0].id if newest else 0
                if args.backfill > 0:
                    recent = await client.get_messages(entity, limit=args.backfill)
                    messages = sorted(recent, key=lambda m: m.id)
                else:
                    messages = []
                    print(f"mirror: @{source} first run, cursor set at {start}, "
                          "forwarding starts with the next message "
                          "(use --backfill N to take the last N now)")
                cursor = (messages[0].id - 1) if messages else start
            else:
                messages = [
                    message
                    async for message in client.iter_messages(entity, min_id=cursor, reverse=True)
                ]

            # Only what the bot sent. Your own replies are already yours, and
            # mirroring them back would double every conversation.
            messages = [m for m in messages if not m.out]

            if not messages:
                state[source] = max(cursor, state.get(source, cursor))
                print(f"mirror: @{source} nothing new")
                continue

            print(f"mirror: @{source} {len(messages)} new")
            delivered, count, problem = await deliver_batch(
                client, destination, source, messages, args.dry_run
            )
            sent_total += count
            per_source.append(f"@{source}: {count}")
            if delivered and not args.dry_run:
                state[source] = delivered
                print(f"mirror: @{source} sent {count}, cursor now {delivered}")
            if problem:
                problems.append(f"@{source}: {problem}")
    finally:
        await client.disconnect()

    if args.dry_run:
        print(f"\nmirror: dry run, {sent_total} would have been sent; "
              "nothing sent and the cursor is unchanged")
        return 1 if problems else 0

    write_state(state)
    for problem in problems:
        print(f"mirror: {problem}", file=sys.stderr)

    # The line the log is read for. One line, fixed shape, both timestamps: the
    # start says when the task fired, the end says when it let go, and the gap
    # between them is the first thing worth knowing when a run seems stuck.
    # Prefixed SUMMARY so a week of logs answers "how much arrived, and when"
    # with a findstr rather than by reading.
    detail = f" [{', '.join(per_source)}]" if len(per_source) > 1 else ""
    print(f"mirror: SUMMARY started {started} finished {stamp()} "
          f"sent {sent_total} failed {len(problems)}{detail}")
    return 1 if problems else 0


async def do_dump(args: argparse.Namespace) -> int:
    """Print recent messages in full, for writing a parser against.

    Read-only: no cursor is touched, nothing is sent, nothing is stored. Running it
    does not consume anything, so it can be run as often as needed.

    Everything a parser might key on is printed, including the parts that are
    invisible when you read the chat: which words are hyperlinked and to where,
    what the buttons point at, and whether several photos are one album. The exact
    text is printed twice — once readable and once as `repr()` — because leading
    spaces, double newlines and non-breaking spaces are precisely what a parser
    trips over and precisely what copying by hand loses.
    """
    api_id, api_hash = credentials()
    client = await connect(api_id, api_hash)
    try:
        for source in watched():
            try:
                entity = await client.get_entity(source)
            except (ValueError, TypeError):
                print(f"mirror: no such chat in this account: @{source}", file=sys.stderr)
                continue

            messages = await client.get_messages(entity, limit=args.limit)
            # Oldest first, and only what the bot sent: your own replies are not
            # what the parser will see.
            incoming = [m for m in reversed(messages) if not m.out]
            print(f"===== @{source}: {len(incoming)} message(s), oldest first =====")

            for message in incoming:
                print("\n" + "=" * 72)
                head = [f"id {message.id}"]
                if message.date:
                    head.append(message.date.strftime("%Y-%m-%d %H:%M:%S %Z").strip())
                grouped = getattr(message, "grouped_id", None)
                if grouped:
                    # Several photos sent as one album arrive as separate messages,
                    # and only the first carries the caption. A parser that does not
                    # know this reads the rest as empty listings.
                    head.append(f"album {grouped}")
                kinds = [
                    key for key in (
                        "photo", "video", "document", "audio", "voice", "sticker",
                        "gif", "contact", "geo", "poll", "web_preview",
                    )
                    if getattr(message, key, None)
                ]
                if kinds:
                    head.append("media: " + ", ".join(kinds))
                print("--- " + " | ".join(head))

                text = message.text or ""
                print("--- text as shown:")
                print(text if text else "(empty)")
                print("--- text exactly (repr):")
                print(repr(text))

                # Hidden links: the chat shows a word, the URL lives on the entity.
                # `get_entities_text` is used rather than slicing by offset because
                # Telegram counts offsets in UTF-16 units, and a message full of
                # emoji would slice wrongly.
                try:
                    pairs = message.get_entities_text()
                except Exception:  # noqa: BLE001 - older Telethon, or no entities
                    pairs = []
                if pairs:
                    print("--- entities:")
                    for entity_obj, covered in pairs:
                        url = getattr(entity_obj, "url", None)
                        print(f"    {type(entity_obj).__name__}: {covered!r}"
                              + (f"  ->  {url}" if url else ""))

                markup = getattr(message, "reply_markup", None)
                rows = getattr(markup, "rows", None) or []
                if rows:
                    print("--- buttons:")
                    for row_index, row in enumerate(rows):
                        for button in getattr(row, "buttons", None) or []:
                            label = getattr(button, "text", "")
                            url = getattr(button, "url", None)
                            data = getattr(button, "data", None)
                            print(f"    row {row_index}: {label!r}"
                                  + (f"  ->  {url}" if url else "")
                                  + (f"  [callback {data!r}]" if data else ""))
    finally:
        await client.disconnect()

    print("\n" + "=" * 72)
    print("Copy everything above, from the first ===== line.")
    return 0


async def do_watch(args: argparse.Namespace) -> int:
    api_id, api_hash = credentials()
    client = await connect(api_id, api_hash)
    destination = Destination.build(args.via, client, media=not args.no_media)
    sources = watched()
    state = read_state()

    @client.on(events.NewMessage(chats=sources, incoming=True))
    async def handler(event: Any) -> None:  # pragma: no cover - needs a live account
        name = getattr(await event.get_chat(), "username", None) or str(event.chat_id)
        try:
            await destination.send(event.message, name)
        except (Fault, FloodWaitError) as error:
            # Do not advance the cursor: `once` will pick this up later.
            print(f"mirror: @{name} msg {event.message.id} not delivered — {error}",
                  file=sys.stderr)
            return
        state[name] = event.message.id
        write_state(state)
        print(f"mirror: {stamp()} sent 1 — @{name} msg {event.message.id} "
              f"→ {destination.chat}", flush=True)

    print(f"mirror: watching {', '.join('@' + s for s in sources)}, "
          f"delivering via {args.via}. Ctrl-C to stop.")
    try:
        await client.run_until_disconnected()
    except asyncio.CancelledError:  # pragma: no cover - Ctrl-C
        pass
    finally:
        await client.disconnect()
    return 0


def fail(message: str) -> int:
    print(f"mirror: {message}", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copy what a bot sends you into a chat of your own.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("login", help="sign in once and print a session string")

    dump = subcommands.add_parser(
        "dump", help="print recent messages in full, for writing a parser against")
    dump.add_argument("--limit", type=int, default=5, metavar="N",
                      help="how many recent messages to print (default 5)")

    for name, help_text in (("once", "forward what is new, then exit"),
                            ("watch", "stay connected and forward live")):
        sub = subcommands.add_parser(name, help=help_text)
        sub.add_argument("--via", choices=("bot", "forward"), default="bot",
                         help="bot: re-send through your bot, photo included (default). "
                              "forward: forward as yourself, keeping everything as it is")
        sub.add_argument("--no-media", action="store_true",
                         help="skip attachments and send the text alone. Faster and "
                              "cheaper on a metered connection; the text still names "
                              "what was dropped")
        if name == "once":
            sub.add_argument("--backfill", type=int, default=0, metavar="N",
                             help="on a first run, take the last N messages (default 0)")
            sub.add_argument("--dry-run", action="store_true",
                             help="print what would be sent; send nothing, remember nothing")

    args = parser.parse_args()
    load_env()
    try:
        if args.command == "login":
            return asyncio.run(do_login())
        if args.command == "dump":
            return asyncio.run(do_dump(args))
        if args.command == "once":
            return asyncio.run(do_once(args))
        return asyncio.run(do_watch(args))
    except Fault as error:
        return fail(str(error))
    except AuthKeyUnregisteredError:
        return fail("this session was revoked. Run `python mirror.py login` again.")
    except FloodWaitError as error:
        return fail(f"Telegram asked to wait {error.seconds}s before trying again")
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
