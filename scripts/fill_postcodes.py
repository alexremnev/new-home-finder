"""Fill in the postcodes of listings already stored without one.

The sweep now derives a postcode from coordinates for every new listing that
needs one (see 0056 and `worker.sources.geo`), but the back catalogue was
stored before that existed: every Zoopla listing past the page budget, and two
in three Rightmove ones, sit there with `postcode` NULL. Those are the alerts
that said only "E14", and the rows `mark_duplicate` skipped.

One-off by design, and safe to re-run: it only ever looks at rows that still
have no postcode, and what it writes is marked `postcode_source = 'derived'`,
so a portal's own answer is never overwritten and the duplicate rule is left
asking only about postcodes a portal stated.

    uv run python scripts/fill_postcodes.py --dry-run
    uv run python scripts/fill_postcodes.py --limit 2000
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from worker.env import load_env

try:
    import psycopg
    from psycopg.rows import dict_row
except ModuleNotFoundError:
    sys.exit("fill_postcodes: psycopg is not installed. Run `uv sync` first.")

from worker.sources.geo import BATCH, Ask, nearest

WANTED = """
SELECT id, external_id, source_key, lat, lng, postcode_district
  FROM listings
 WHERE postcode IS NULL
   AND lat IS NOT NULL AND lng IS NOT NULL
 ORDER BY first_seen_at DESC
 LIMIT %(limit)s
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=1000,
                    help="listings to look at (default 1000)")
    ap.add_argument("--dry-run", action="store_true",
                    help="ask, report, write nothing")
    args = ap.parse_args()

    load_env()
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("fill_postcodes: DATABASE_URL is not set", file=sys.stderr)
        return 1

    with psycopg.connect(url, autocommit=True, row_factory=dict_row) as conn:
        rows = conn.execute(WANTED, {"limit": args.limit}).fetchall()
        if not rows:
            print("fill_postcodes: nothing without a postcode and with a coordinate")
            return 0

        print(f"fill_postcodes: asking about {len(rows)} listing(s), "
              f"{BATCH} at a time")
        # The listing id as the key, so the answer needs no second lookup —
        # external_id is only unique within a source.
        found = nearest([
            Ask(key=str(row["id"]), lat=float(row["lat"]), lng=float(row["lng"]),
                district=row["postcode_district"])
            for row in rows
        ])

        refused = len(rows) - len(found)
        print(f"fill_postcodes: {len(found)} answered, {refused} not "
              "(nothing within range, or the outward code disagreed)")

        if args.dry_run:
            for row in rows[:10]:
                got = found.get(str(row["id"]))
                print(f"  {row['source_key']}/{row['external_id']} "
                      f"{row['postcode_district']} -> {got or '(nothing)'}")
            print("fill_postcodes: dry run, nothing written")
            return 0

        if not found:
            return 0

        # One statement. The guard on `postcode IS NULL` is repeated here and
        # not only in the select: between the two, a sweep may have read the
        # listing's own page and written the real thing, and that must win.
        written = conn.execute(
            """
            UPDATE listings l
               SET postcode = v.postcode, postcode_source = 'derived'
              FROM (SELECT * FROM unnest(
                        %(ids)s::bigint[], %(codes)s::text[]
                    ) AS t(id, postcode)) AS v
             WHERE l.id = v.id AND l.postcode IS NULL
            RETURNING l.id
            """,
            {
                "ids": [int(key) for key in found],
                "codes": [found[key] for key in found],
            },
        ).fetchall()
        print(f"fill_postcodes: wrote {len(written)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
