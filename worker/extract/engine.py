"""Apply an extraction schema to a response body.

The engine executes a schema; it never contains one. Selectors live in the
database as data, because they are rewritten automatically when a site changes
its markup, and the vocabulary a schema may use is fixed by `FieldRule`: a path,
a selector, a label, an attribute, a regular expression, a constant, or a
presence test. There is no expression language and nothing is evaluated, so a
schema produced by a model cannot do anything this module does not already do.

Four strategies, differing only in how the listing elements are found:

    dom          CSS selection over HTML
    next_data    the JSON embedded in a `__NEXT_DATA__` script
    jsonld       the JSON in `application/ld+json` scripts
    api_json     a JSON response body
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

from worker.contracts.extraction import ExtractionSchema, FieldRule
from worker.contracts.listing import RawValue

NEXT_DATA = re.compile(
    rb'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.S | re.I
)
LD_JSON = re.compile(
    rb'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I
)
WHITESPACE = re.compile(r"\s+")


class ExtractionError(RuntimeError):
    pass


def apply(
    schema: ExtractionSchema, body: bytes | str, *, base: dict[str, RawValue] | None = None
) -> list[dict[str, RawValue]]:
    """Extract one row per listing element found in `body`.

    `base` supplies values the page does not carry — an external id taken from
    the URL, for instance — and a field extracted from the page overrides it only
    if it produced something.
    """
    raw = body.encode() if isinstance(body, str) else body
    rows: list[dict[str, RawValue]] = []

    if schema.strategy == "dom":
        items: Iterator[Any] = _dom_items(schema, raw)
        read = _read_dom
    else:
        items = _json_items(schema, raw)
        read = _read_json

    for item in items:
        row: dict[str, RawValue] = dict(base or {})
        for name, rule in schema.fields.items():
            value = read(item, rule)
            if value is not None or name not in row:
                row[name] = value
        rows.append(row)
    return rows


# ── dom ───────────────────────────────────────────────────────────────────


def _dom_items(schema: ExtractionSchema, raw: bytes) -> Iterator[Any]:
    from selectolax.parser import HTMLParser

    tree = HTMLParser(raw.decode("utf-8", errors="replace"))
    assert schema.item is not None  # guaranteed by the schema validator
    yield from tree.css(schema.item)


def _read_dom(item: Any, rule: FieldRule) -> RawValue:
    if rule.const is not None:
        return rule.const

    node = item if rule.sel == "self" else None
    if node is None and rule.sel:
        node = item.css_first(rule.sel)
    if node is None and rule.label:
        node = _value_beside_label(item, rule)
    if node is None:
        return None

    if rule.is_tri_state:
        return _tri_state(node, rule)
    if rule.attr:
        return _finish(node.attributes.get(rule.attr), rule)
    return _finish(node.text(), rule)


def _value_beside_label(item: Any, rule: FieldRule) -> Any | None:
    """Find the value cell that sits beside a label cell.

    The label cell can hold more than the label — a help button, a hidden tooltip
    — so the comparison is on the start of its text rather than the whole of it.
    """
    assert rule.label is not None
    wanted = rule.label.strip().lower()
    label_tags = _tags(rule.label_in)
    value_tags = _tags(rule.value_in)

    for candidate in item.css(",".join(label_tags)):
        text = _normalise(candidate.text())
        if not text or not text.lower().startswith(wanted):
            continue
        parent = candidate.parent
        if parent is None:
            continue
        label_html = candidate.html
        siblings = [
            child
            for child in parent.iter(include_text=False)
            if child.tag in value_tags and child.html != label_html
        ]
        if siblings:
            # The value is the last cell of the row: a row may carry an extra
            # cell between the label and the value.
            return siblings[-1]
    return None


def _tri_state(node: Any, rule: FieldRule) -> bool | None:
    """True, False, or None for "not stated" — never collapse the third state."""
    if rule.false_if and node.css_first(rule.false_if) is not None:
        return False
    if rule.true_if and node.css_first(rule.true_if) is not None:
        return True
    return None


def _tags(spec: str) -> list[str]:
    return [t.strip().lower() for t in spec.split(",") if t.strip()]


# ── structured ────────────────────────────────────────────────────────────


def _json_items(schema: ExtractionSchema, raw: bytes) -> Iterator[Any]:
    payload = _payload(schema.strategy, raw)
    assert schema.root is not None  # guaranteed by the schema validator
    for found in _jsonpath(schema.root, payload):
        if isinstance(found, list):
            yield from found
        else:
            yield found


def _payload(strategy: str, raw: bytes) -> Any:
    if strategy == "api_json":
        return json.loads(raw.decode("utf-8", errors="replace"))
    if strategy == "next_data":
        match = NEXT_DATA.search(raw)
        if not match:
            raise ExtractionError("no __NEXT_DATA__ script in the document")
        return json.loads(match.group(1).decode("utf-8", errors="replace"))
    if strategy == "jsonld":
        blocks = [
            json.loads(m.group(1).decode("utf-8", errors="replace")) for m in LD_JSON.finditer(raw)
        ]
        if not blocks:
            raise ExtractionError("no application/ld+json script in the document")
        return blocks if len(blocks) > 1 else blocks[0]
    raise ExtractionError(f"unknown strategy {strategy!r}")


def _read_json(item: Any, rule: FieldRule) -> RawValue:
    if rule.const is not None:
        return rule.const
    if not rule.path:
        return None
    found = list(_jsonpath(rule.path, item))
    if not found:
        return None
    value = found[0]
    if isinstance(value, (dict, list)):
        return None
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)) and not rule.regex:
        return value
    return _finish(str(value), rule)


def _jsonpath(expression: str, payload: Any) -> Iterator[Any]:
    from jsonpath_ng.ext import parse

    for match in parse(expression).find(payload):
        yield match.value


# ── shared ────────────────────────────────────────────────────────────────


def _normalise(text: str | None) -> str:
    return WHITESPACE.sub(" ", text or "").strip()


def _finish(text: str | None, rule: FieldRule) -> RawValue:
    value = _normalise(text)
    if not value:
        return None
    if rule.regex:
        match = re.search(rule.regex, value)
        if not match:
            return None
        value = _normalise(match.group(1) if match.groups() else match.group(0))
    return value or None
