"""robots.txt matching per RFC 9309.

The standard library's `urllib.robotparser` implements the 1994 draft and ignores
wildcards: it reads `Disallow: /api/*` as a literal path and therefore treats
`/api/x` as allowed. Since respecting robots.txt is load-bearing rather than
decorative here, the rules are matched properly instead.

Implemented:
  * group selection against our product token (the text before the first `/`),
    longest case-insensitive match, with `*` as the fallback group
  * `*` as any sequence and `$` as end-of-path in rule patterns
  * most specific rule wins, with allow beating disallow on equal specificity
  * `Crawl-delay` and `Sitemap` directives

Not implemented: percent-encoding normalisation. Paths are compared as given,
which is sufficient for the sites in use and is noted rather than assumed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass
class Rule:
    pattern: str
    allow: bool
    regex: re.Pattern[str] = field(init=False)

    def __post_init__(self) -> None:
        self.regex = _compile(self.pattern)

    @property
    def specificity(self) -> int:
        return len(self.pattern)


def _compile(pattern: str) -> re.Pattern[str]:
    """Translate a robots path pattern into a regex anchored at the path start."""
    out = []
    for char in pattern.rstrip("$"):
        out.append(".*" if char == "*" else re.escape(char))
    body = "".join(out)
    return re.compile(f"^{body}\\Z" if pattern.endswith("$") else f"^{body}")


@dataclass
class Group:
    tokens: list[str] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    crawl_delay: float | None = None


class RobotsTxt:
    def __init__(self) -> None:
        self.groups: list[Group] = []
        self.sitemaps: list[str] = []
        self.parsed = False

    # ── parsing ───────────────────────────────────────────────────────────

    @classmethod
    def parse(cls, text: str) -> RobotsTxt:
        robots = cls()
        current: Group | None = None
        starting_group = False

        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            field_name, _, value = line.partition(":")
            key = field_name.strip().lower()
            value = value.strip()

            if key == "user-agent":
                # Consecutive user-agent lines share one group of rules.
                if current is None or not starting_group:
                    current = Group()
                    robots.groups.append(current)
                    starting_group = True
                current.tokens.append(value.lower())
                continue

            starting_group = False
            if key in ("allow", "disallow"):
                if current is None:  # rules before any user-agent line
                    continue
                if key == "disallow" and value == "":
                    continue  # an empty Disallow imposes nothing
                if value:
                    current.rules.append(Rule(value, allow=(key == "allow")))
            elif key == "crawl-delay" and current is not None:
                try:
                    current.crawl_delay = float(value)
                except ValueError:
                    pass
            elif key == "sitemap" and value:
                robots.sitemaps.append(value)

        robots.parsed = True
        return robots

    # ── querying ──────────────────────────────────────────────────────────

    @staticmethod
    def product_token(user_agent: str) -> str:
        """The identifying part of a User-Agent, before any version or comment.

        Matching against the whole header would let a group aimed at some other
        crawler apply to us by coincidence, since a full User-Agent contains a URL
        and arbitrary words.
        """
        head = user_agent.split("/", 1)[0].strip()
        return (head.split()[0] if head.split() else user_agent).lower()

    def group_for(self, user_agent: str) -> Group | None:
        """Select the group whose token best matches the agent.

        Longest matching token wins; `*` applies only when nothing else matches.
        A token contained in our product token counts as a match, which errs
        toward considering ourselves restricted rather than exempt.
        """
        ua = self.product_token(user_agent)
        best: tuple[int, Group] | None = None
        wildcard: Group | None = None
        for group in self.groups:
            for token in group.tokens:
                if token == "*":
                    wildcard = wildcard or group
                elif token and token in ua and (best is None or len(token) > best[0]):
                    best = (len(token), group)
        return best[1] if best else wildcard

    def can_fetch(self, user_agent: str, url: str) -> bool:
        if not self.parsed:
            return True
        group = self.group_for(user_agent)
        if group is None:
            return True

        path = _path_of(url)
        winner: Rule | None = None
        for rule in group.rules:
            if rule.regex.search(path) and (
                winner is None
                or rule.specificity > winner.specificity
                # Allow wins a tie, per RFC 9309.
                or (rule.specificity == winner.specificity and rule.allow)
            ):
                winner = rule
        return winner.allow if winner else True

    def crawl_delay(self, user_agent: str) -> float | None:
        group = self.group_for(user_agent)
        return group.crawl_delay if group else None

    def disallows_everything(self, user_agent: str) -> bool:
        return not self.can_fetch(user_agent, "/")


def _path_of(url: str) -> str:
    if url.startswith(("http://", "https://")):
        parts = urlsplit(url)
        path = parts.path or "/"
        return f"{path}?{parts.query}" if parts.query else path
    return url or "/"
