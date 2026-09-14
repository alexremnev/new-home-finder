from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from worker.env import load_env

URL_ATTRS = ("url", "data", "query", "bot_id", "fwd_text", "text", "button_id")

def describe(obj: object, attrs: tuple[str, ...] = URL_ATTRS) -> str:
    parts = [type(obj).__name__]
    for name in attrs:
        value = getattr(obj, name, None)
        if value is None:
            continue
        shown = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
        parts.append(f"{name}={shown[:120]!r}")
    return "  ".join(parts)

async def probe(chat: str, count: int) -> int:
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    import os

    client = TelegramClient(
        StringSession(os.environ["TG_SESSION"]),
        int(os.environ["TG_API_ID"]),
        os.environ["TG_API_HASH"],
    )
    await client.connect()
    try:
        me = await client.get_me()
        print(f"account: @{me.username or me.id}  bot={bool(getattr(me, 'bot', False))}")

        entity = await client.get_entity(chat)
        print(f"chat: {type(entity).__name__} id={entity.id} "
              f"title={getattr(entity, 'title', None)!r}")
        print()

        async for message in client.iter_messages(entity, limit=count):
            print(f"=== message {message.id}  {message.date:%Y-%m-%d %H:%M}")
            print(f"    text: {len(message.text or '')} chars, "
                  f"media={type(message.media).__name__ if message.media else None}")

            markup = getattr(message, "reply_markup", None)
            print(f"    reply_markup: {type(markup).__name__ if markup else None}")
            for r, row in enumerate(getattr(markup, "rows", None) or []):
                for b, button in enumerate(getattr(row, "buttons", None) or []):
                    print(f"      button[{r}][{b}]: {describe(button)}")

            entities = getattr(message, "entities", None) or []
            for e in entities:
                if getattr(e, "url", None) or type(e).__name__ == "MessageEntityTextUrl":
                    print(f"      entity: {describe(e, ('url',))}")
            print()
    finally:
        await client.disconnect()
    return 0

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print the structure of the newest messages: button classes and "
                    "where a url does or does not sit. Reads nothing into the database."
    )
    parser.add_argument("--env", help="env file to read TG_* from")
    parser.add_argument("--chat", help="override TG_WATCH")
    parser.add_argument("--count", type=int, default=3)
    args = parser.parse_args()

    load_env(pathlib.Path(args.env) if args.env else None)

    import os

    missing = [n for n in ("TG_API_ID", "TG_API_HASH", "TG_SESSION") if not os.environ.get(n)]
    if missing:
        print(f"probe: not set: {', '.join(missing)}", file=sys.stderr)
        return 2

    chat = args.chat or (os.environ.get("TG_WATCH") or "").strip()
    if not chat:
        print("probe: TG_WATCH is not set and --chat was not given", file=sys.stderr)
        return 2

    return asyncio.run(probe(chat, args.count))

if __name__ == "__main__":
    raise SystemExit(main())
