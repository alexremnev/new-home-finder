"""Structural hash of a response body.

The point is to change when the markup changes and to stay the same when only the
listings do. Two things follow from that:

  * detection — a changed fingerprint says "the format moved" before extraction
    has a chance to fail quietly;
  * free recovery — a retired schema is keyed by the fingerprint it was written
    for, so when a site reverts an experiment the previous schema is reactivated
    without asking a model anything.

Content is deliberately excluded. Text, numbers, ids and URLs all vary per
listing; only the shape of the document is hashed.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

DOM_DEPTH = 3
JSON_DEPTH = 4

# Utility classes encode layout state rather than structure, and they churn.
# Including them would make the fingerprint change for reasons that do not
# affect a selector.
VOLATILE_CLASS = re.compile(
    r"^(?:"
    r"[mp][trblxy]?-(?:auto|\d+)|g?a?p-\d+|row-gap-\d+|column-gap-\d+"
    r"|w-\d+|h-\d+|fs-[\w-]+|lh-[\w-]+|text-(?:start|end|center|nowrap|break)"
    r"|d-(?:none|block|flex|inline|inline-flex|md-none|lg-none)"
    r"|col(?:-\w+)*|order-\d+|z-\d+|opacity-\d+"
    r")$"
)


def fingerprint(body: bytes | str, *, strategy: str, item: str | None = None) -> str:
    """Return a short structural hash for `body`."""
    raw = body.encode() if isinstance(body, str) else body
    signature = (
        _dom_signature(raw, item or "body")
        if strategy == "dom"
        else _json_signature(raw, strategy)
    )
    return hashlib.sha1(signature.encode()).hexdigest()[:16]


# ── dom ───────────────────────────────────────────────────────────────────


def _dom_signature(raw: bytes, item: str) -> str:
    from selectolax.parser import HTMLParser

    tree = HTMLParser(raw.decode("utf-8", errors="replace"))
    nodes = tree.css(item)
    if not nodes:
        return "dom:no-match"
    parts = sorted({_node_signature(node, DOM_DEPTH) for node in nodes})
    return "dom:" + "|".join(parts)


def _node_signature(node: Any, depth: int) -> str:
    own = _tag_signature(node)
    if depth <= 0:
        return own
    children = sorted(
        {_node_signature(child, depth - 1) for child in node.iter(include_text=False)}
    )
    return f"{own}({','.join(children)})" if children else own


def _tag_signature(node: Any) -> str:
    attrs = node.attributes or {}
    classes = sorted(
        cls
        for cls in (attrs.get("class") or "").split()
        if cls and not VOLATILE_CLASS.match(cls)
    )
    data = sorted(name for name in attrs if name.startswith("data-"))
    bits = [node.tag or "?"]
    if classes:
        bits.append("." + ".".join(classes))
    if data:
        bits.append("[" + ",".join(data) + "]")
    return "".join(bits)


# ── structured ────────────────────────────────────────────────────────────


def _json_signature(raw: bytes, strategy: str) -> str:
    from worker.extract.engine import _payload  # noqa: PLC0415 - avoids a cycle at import time

    try:
        payload = _payload(strategy, raw)
    except Exception:  # noqa: BLE001 - an unparseable body is itself a signature
        return f"{strategy}:unparseable"
    return f"{strategy}:" + "|".join(sorted(_key_paths(payload, JSON_DEPTH)))


def _key_paths(payload: Any, depth: int, prefix: str = "") -> set[str]:
    """Key paths with the type of each leaf, ignoring every value."""
    if depth <= 0:
        return {f"{prefix}:…"}
    if isinstance(payload, dict):
        out: set[str] = set()
        for key in sorted(payload):
            out |= _key_paths(payload[key], depth - 1, f"{prefix}.{key}" if prefix else key)
        return out or {f"{prefix}:{{}}"}
    if isinstance(payload, list):
        # An array's length varies with the number of listings; only the shape of
        # its first element matters.
        return (
            _key_paths(payload[0], depth - 1, f"{prefix}[]")
            if payload
            else {f"{prefix}[]:empty"}
        )
    return {f"{prefix}:{type(payload).__name__}"}


def describe(body: bytes | str, *, strategy: str, item: str | None = None) -> str:
    """The signature behind a fingerprint, for diagnosing an unexpected change."""
    raw = body.encode() if isinstance(body, str) else body
    signature = (
        _dom_signature(raw, item or "body")
        if strategy == "dom"
        else _json_signature(raw, strategy)
    )
    return json.dumps({"fingerprint": fingerprint(raw, strategy=strategy, item=item),
                       "signature": signature[:2000]}, ensure_ascii=False)
