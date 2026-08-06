"""Load a local `.env` file.

Nothing reads `.env` on its own: the standard library only exposes the process
environment. This fills that gap for local runs while leaving deployed runs
alone.

Values already present in the environment win. In CI the values arrive as real
environment variables from the secret store, and a stale file checked out beside
them must not override them.

One deliberate difference from some `.env` parsers: text after `#` on a value
line is *not* treated as a comment. A Postgres password may legitimately contain
`#`, and silently truncating a connection string produces a confusing
authentication failure. Only a line whose first non-space character is `#` is a
comment. Wrap a value in quotes if it has leading or trailing spaces.
"""

from __future__ import annotations

import os
import pathlib

FILENAME = ".env"


def find_env_file(start: pathlib.Path | None = None) -> pathlib.Path | None:
    """Look for `.env` beside the project root, walking up from `start`."""
    here = (start or pathlib.Path(__file__)).resolve()
    for directory in [here, *here.parents]:
        candidate = directory / FILENAME
        if candidate.is_file():
            return candidate
    return None


def parse(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if name.startswith("export "):
            name = name[len("export ") :].strip()
        if not name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            quote = value[0]
            value = value[1:-1]
            if quote == '"':
                value = value.replace("\\n", "\n").replace("\\t", "\t")
        values[name] = value
    return values


def load_env(path: pathlib.Path | None = None, *, override: bool = False) -> int:
    """Put `.env` values into the environment. Returns how many were applied."""
    env_file = path or find_env_file()
    if env_file is None or not env_file.is_file():
        return 0
    applied = 0
    for name, value in parse(env_file.read_text(encoding="utf-8")).items():
        if override or not os.environ.get(name):
            os.environ[name] = value
            applied += 1
    return applied
