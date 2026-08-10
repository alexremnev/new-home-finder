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
`--via bot` (default) re-sends the text through your own bot, which is why
`TG_DEST_BOT_TOKEN` exists. Only text survives: to re-upload a photo the script
would have to download it first, and that is not worth it for a mirror.

`--via forward` forwards natively, as you. Media, formatting and the "forwarded
from" header all survive, and no bot token is involved — but your account must be
a member of the destination chat.

Usage
-----
    python mirror.py login                 # once, interactively: prints a session
    python mirror.py once                  # forward what is new, then exit (cron)
    python mirror.py once --backfill 3     # also take the last 3, to prove it works
    python mirror.py once --dry-run        # print, send nothing, remember nothing
    python mirror.py watch                 # stay connected and forward live
    python mirror.py watch --via forward   # keep photos and formatting

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
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
STATE = HERE / "state.json"
LIMIT = 4096
PAUSE_SECONDS = 1.1

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


def media_label(message: Any) -> str | None:
    for attribute, label in (
        ("photo", "photo"), ("video", "video"), ("document", "document"),
        ("audio", "audio"), ("voice", "voice"), ("sticker", "sticker"),
        ("gif", "gif"), ("contact", "contact"), ("geo", "location"), ("poll", "poll"),
    ):
        if getattr(message, attribute, None):
            return label
    return "media" if getattr(message, "media", None) else None


def describe(message: Any, source: str) -> str:
    """One message as plain text.

    No parse mode anywhere: this is text somebody else wrote, so any markup it
    happens to contain would be a rejected message rather than an ugly one.
    """
    stamp = message.date.strftime("%d %b %H:%M") if message.date else ""
    header = " · ".join(part for part in (f"@{source}", stamp, f"msg {message.id}") if part)
    label = media_label(message)
    text = (message.text or "").strip()
    if label and text:
        text = f"[{label}]\n{text}"
    elif label:
        text = f"[{label}, no text]"
    elif not text:
        text = "[no text]"
    body = f"{header}\n\n{text}"
    return body if len(body) <= LIMIT else body[: LIMIT - 14].rstrip() + "\n… (truncated)"


class Destination:
    """Where copies go, and how. Both routes expose the same `send`, so the
    forwarding loop does not branch on the choice."""

    def __init__(self, via: str, chat: str, token: str | None, client: Any) -> None:
        self.via = via
        self.chat: str | int = int(chat) if chat.lstrip("-").isdigit() else chat
        self.token = token
        self.client = client

    @classmethod
    def build(cls, via: str, client: Any) -> Destination:
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
        return cls(via, chat, token, client)

    async def send(self, message: Any, source: str) -> None:
        if self.via == "forward":
            await self.client.forward_messages(self.chat, message)
            return
        assert self.token is not None
        response = post(self.token, "sendMessage", {
            "chat_id": self.chat,
            "text": describe(message, source),
            "disable_web_page_preview": True,
        })
        if not response.get("ok"):
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
) -> tuple[int, str]:
    """Send in order, stopping at the first failure.

    Returns the highest id actually delivered. Stopping matters: the cursor may
    only advance over messages that arrived, or a single failure would silently
    swallow everything queued behind it.
    """
    delivered = 0
    for index, message in enumerate(messages):
        if dry_run:
            print(f"\n{describe(message, source)}")
            delivered = message.id
            continue
        if index:
            await asyncio.sleep(PAUSE_SECONDS)
        try:
            await destination.send(message, source)
        except FloodWaitError as error:
            return delivered, f"Telegram asked for a {error.seconds}s pause; stopping here"
        except Fault as error:
            return delivered, str(error)
        delivered = message.id
    return delivered, ""


async def do_once(args: argparse.Namespace) -> int:
    api_id, api_hash = credentials()
    client = await connect(api_id, api_hash)
    destination = Destination.build(args.via, client)
    state = read_state()
    problems: list[str] = []

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
            delivered, problem = await deliver_batch(
                client, destination, source, messages, args.dry_run
            )
            if delivered and not args.dry_run:
                state[source] = delivered
                print(f"mirror: @{source} delivered through {delivered}")
            if problem:
                problems.append(f"@{source}: {problem}")
    finally:
        await client.disconnect()

    if args.dry_run:
        print("\nmirror: dry run, nothing sent and the cursor is unchanged")
        return 1 if problems else 0

    write_state(state)
    for problem in problems:
        print(f"mirror: {problem}", file=sys.stderr)
    return 1 if problems else 0


async def do_watch(args: argparse.Namespace) -> int:
    api_id, api_hash = credentials()
    client = await connect(api_id, api_hash)
    destination = Destination.build(args.via, client)
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
        print(f"mirror: @{name} msg {event.message.id} → {destination.chat}")

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

    for name, help_text in (("once", "forward what is new, then exit"),
                            ("watch", "stay connected and forward live")):
        sub = subcommands.add_parser(name, help=help_text)
        sub.add_argument("--via", choices=("bot", "forward"), default="bot",
                         help="bot: re-send the text through your bot (default). "
                              "forward: forward as yourself, keeping media and formatting")
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
