"""Turn a saved page into a committable test fixture.

A test needs the page's *structure* — tags, classes, data attributes — and the
*format* of its field values, so that a selector change or a unit change is
caught. It does not need the listing's prose, its photographs, or its contact
details, and a public repository is the wrong place for those.

This reduces a saved page to the part a test uses:

  kept     tag structure; class, id, data-*, and accessibility attributes;
           short text nodes, which is where field values live ("£590.00 p/m",
           "12 Sep", "Furnished"); icon elements and their fragment references
  dropped  script, style, noscript, comments, meta and link tags; vector
           geometry; external URLs; long text nodes, which is where prose lives

Icons are kept because a yes/no field is often not text at all: a tick or a cross
in the value cell, with the answer carried by the icon's class, its `#fragment`
reference, or an accessibility label. Dropping them turns "pets allowed" into an
empty cell, which reads as "not stated" — a different answer entirely.

The result is a derived artifact of our own, an order of magnitude smaller, with
readable diffs when a site changes its markup.

Usage:
    python scripts/reduce_fixture.py snapshots/openrent/detail.plain_honest.html \\
        -o tests/fixtures/openrent/detail.html
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from html.parser import HTMLParser

DROP_TAGS = {"script", "style", "noscript", "iframe", "meta", "link", "picture", "source"}

# Vector geometry carries no field values, only coordinates. The svg and use
# elements around it are kept: that is where the icon's identity lives.
DROP_TAGS |= {"path", "defs", "g", "circle", "rect", "polygon", "polyline",
              "ellipse", "line", "mask", "clippath", "lineargradient", "stop",
              "filter", "pattern", "symbol", "desc"}
VOID_TAGS = {"br", "hr", "img", "input", "meta", "link", "source", "col", "area", "base"}

# Attributes a selector may reasonably target. Everything else is noise for a
# structural fixture, and URLs additionally carry content we are not keeping.
KEEP_ATTRS = (
    "class", "id", "itemprop", "itemtype", "role", "type", "name", "content",
    # Accessibility text frequently states a boolean outright ("Yes", "Allowed"),
    # which makes it the most reliable reading of an icon.
    "aria-label", "aria-hidden", "aria-checked", "title", "alt", "value",
)
URL_ATTRS = ("src", "srcset", "data-src", "poster", "action")

# href is kept only when it is an in-document fragment such as `#icon-tick`,
# which identifies an icon rather than pointing at content.
FRAGMENT_ATTRS = ("href", "xlink:href")

# Text at or below this length is a field value; above it, prose.
TEXT_LIMIT = 120
PLACEHOLDER = "[text removed]"


class Reducer(HTMLParser):
    def __init__(self, text_limit: int = TEXT_LIMIT) -> None:
        super().__init__(convert_charrefs=True)
        self.text_limit = text_limit
        self.out: list[str] = []
        self._skip_depth = 0
        self._skip_tag: str | None = None
        self.dropped_text = 0
        self.dropped_urls = 0
        self.kept_icons = 0

    # ── tags ──────────────────────────────────────────────────────────────

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth:
            if tag == self._skip_tag:
                self._skip_depth += 1
            return
        if tag in DROP_TAGS:
            if tag not in VOID_TAGS:
                self._skip_tag, self._skip_depth = tag, 1
            return
        self.out.append(f"<{tag}{self._attrs(attrs)}>")

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            if tag == self._skip_tag:
                self._skip_depth -= 1
                if not self._skip_depth:
                    self._skip_tag = None
            return
        if tag not in DROP_TAGS and tag not in VOID_TAGS:
            self.out.append(f"</{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth or tag in DROP_TAGS:
            return
        self.out.append(f"<{tag}{self._attrs(attrs)}/>")

    def _attrs(self, attrs: list[tuple[str, str | None]]) -> str:
        parts = []
        for name, value in attrs:
            low = name.lower()
            if low in URL_ATTRS:
                self.dropped_urls += 1
                continue
            if low in FRAGMENT_ATTRS:
                if value and value.startswith("#"):
                    parts.append(f' {low}="{value}"')
                    self.kept_icons += 1
                else:
                    self.dropped_urls += 1
                continue
            if low in KEEP_ATTRS or low.startswith("data-"):
                parts.append(f' {low}="{(value or "").strip()}"' if value else f" {low}")
        return "".join(parts)

    # ── text ──────────────────────────────────────────────────────────────

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        collapsed = re.sub(r"\s+", " ", data).strip()
        if not collapsed:
            return
        if len(collapsed) > self.text_limit:
            self.dropped_text += 1
            self.out.append(PLACEHOLDER)
            return
        self.out.append(collapsed)

    def handle_comment(self, data: str) -> None:
        return


def reduce_html(html: str, *, text_limit: int = TEXT_LIMIT) -> tuple[str, dict[str, int]]:
    reducer = Reducer(text_limit)
    reducer.feed(html)
    reducer.close()
    body = "".join(reducer.out)
    # One tag per line keeps diffs readable when a site changes its markup.
    body = re.sub(r"><", ">\n<", body)
    stats = {
        "in_bytes": len(html),
        "out_bytes": len(body),
        "text_nodes_dropped": reducer.dropped_text,
        "urls_dropped": reducer.dropped_urls,
        "icon_refs_kept": reducer.kept_icons,
    }
    return body + "\n", stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=pathlib.Path)
    ap.add_argument("-o", "--out", type=pathlib.Path, required=True)
    ap.add_argument(
        "--text-limit", type=int, default=TEXT_LIMIT,
        help=f"text nodes longer than this are replaced (default {TEXT_LIMIT})",
    )
    args = ap.parse_args()

    if not args.source.exists():
        print(f"not found: {args.source}", file=sys.stderr)
        return 2

    reduced, stats = reduce_html(
        args.source.read_text(encoding="utf-8", errors="replace"),
        text_limit=args.text_limit,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(reduced, encoding="utf-8")

    ratio = stats["out_bytes"] / stats["in_bytes"] if stats["in_bytes"] else 0
    print(
        f"{args.source} -> {args.out}\n"
        f"  {stats['in_bytes']:,} -> {stats['out_bytes']:,} bytes ({ratio:.1%})\n"
        f"  {stats['text_nodes_dropped']} long text nodes replaced, "
        f"{stats['urls_dropped']} urls removed, "
        f"{stats['icon_refs_kept']} icon references kept"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
