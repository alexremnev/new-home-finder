"""Seed the geographic reference data and the coverage scope.

Two different things, deliberately separated:

  locations          complete reference data for zones 1-3. Seeded once, never
                     gated. Widening coverage later must not require new rows.
  source_locations   which of those districts a source actually watches, via
                     the `enabled` flag. This is the coverage scope, and it is
                     data so that changing it needs no deploy.

The first stage watches three districts. Everything else is present but disabled:

    python scripts/seed_locations.py --enable SE16,SE8,E14

To widen coverage afterwards, no code and no re-seed is needed:

    UPDATE source_locations SET enabled = true
     WHERE source_key = 'openrent'
       AND location_id IN (SELECT id FROM locations WHERE code IN ('E1','SE1'));

Zone membership comes from the approximate district lists in the specification
and is stored with approx = true. Zone boundaries are defined for stations, not
territories, so several districts genuinely straddle two zones; the columns are
min and max for that reason. Deriving zones properly from station coordinates is
a later refinement and does not change this schema.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from worker.env import load_env  # noqa: E402 - path set above for direct runs

# Approximate zone membership. A district listed in two zones is split by a zone
# boundary and gets min/max accordingly.
ZONE_DISTRICTS: dict[int, list[str]] = {
    1: "EC1 EC2 EC3 EC4 WC1 WC2 W1 SW1 NW1 N1 E1 SE1 SE11 SW3 SW7 W2 W8".split(),
    2: (
        "E1 E1W E2 E3 E8 E9 E14 N1 N4 N5 N7 N16 N19 NW3 NW5 NW6 NW8 "
        "SE4 SE5 SE8 SE10 SE11 SE13 SE14 SE15 SE16 SE17 SE22 SE24 "
        "SW2 SW3 SW4 SW5 SW6 SW7 SW8 SW9 SW10 SW11 SW15 SW18 "
        "W2 W6 W8 W9 W10 W11 W12 W14"
    ).split(),
    3: (
        "E3 E5 E6 E7 E10 E11 E12 E13 E15 E16 E17 "
        "N2 N6 N8 N10 N15 N16 N17 N19 N22 NW2 NW4 NW10 NW11 "
        "SE3 SE6 SE7 SE9 SE10 SE12 SE13 SE18 SE19 SE21 SE22 SE23 SE24 SE26 SE27 "
        "SW2 SW12 SW13 SW14 SW15 SW16 SW17 SW18 SW19 W3 W4 W5 W13"
    ).split(),
}

# Every outward code in London, whether or not its zone is known.
#
# Two separate facts, and conflating them is what limited the reference data to
# zones 1-3: ZONE_DISTRICTS says *which zone a district is in*, and that is only
# known for the inner ones. This says *which districts exist in London*, which is a
# much longer list and has nothing to do with zones. A district seeded from here
# with no entry above simply has a null zone and `approx = true` — honest, and
# nothing reads the zone except a report.
#
# The outer areas are those falling substantially within Greater London. The
# boundaries are genuinely arguable — WD and DA straddle it, KT reaches into Surrey
# — and being slightly generous is the cheaper mistake: a district nobody has a
# listing in is a filter that matches nothing, while a missing district is a
# subscriber told their own area "isn't covered yet".
#
# Anything still missed registers itself: `store.ensure_district` adds a district
# the moment a listing arrives from it, so this list is a good start rather than a
# boundary that has to be right.
LONDON_AREAS: dict[str, list[int]] = {
    # Inner: the eight London postcode areas.
    "E": list(range(1, 21)),
    "EC": [1, 2, 3, 4],
    "N": list(range(1, 23)),
    "NW": list(range(1, 12)),
    "SE": list(range(1, 29)),
    "SW": list(range(1, 21)),
    "W": list(range(1, 15)),
    "WC": [1, 2],
    # Outer: Greater London beyond the inner areas.
    "BR": list(range(1, 9)),          # Bromley
    "CR": [0, 2, 3, 4, 5, 6, 7, 8],   # Croydon
    "DA": [1, 5, 6, 7, 8, 14, 15, 16, 17, 18],  # Bexley, Dartford edge
    "EN": [1, 2, 3, 4, 5],            # Enfield
    "HA": list(range(0, 10)),         # Harrow
    "IG": list(range(1, 12)),         # Redbridge, Barking
    "KT": list(range(1, 11)),         # Kingston
    "RM": list(range(1, 15)),         # Havering, Barking
    "SM": list(range(1, 8)),          # Sutton
    "TW": list(range(1, 21)),         # Richmond, Hounslow
    "UB": list(range(1, 12)),         # Hillingdon, Ealing
    "WD": [6, 23],                    # Harrow Weald edge
}

# Codes that are not plain "<area><number>" and would otherwise be missed.
EXTRA_DISTRICTS = ("E1W", "N1C", "NW1W")


def all_districts() -> list[str]:
    """Every district to seed, in a stable order."""
    codes = {f"{area}{number}" for area, numbers in LONDON_AREAS.items() for number in numbers}
    codes.update(EXTRA_DISTRICTS)
    # Whatever ZONE_DISTRICTS knows about is included even if the ranges above miss
    # it, so zone knowledge can never be dropped by an edit to the ranges.
    codes.update(code for codes_in_zone in ZONE_DISTRICTS.values() for code in codes_in_zone)
    return sorted(codes, key=lambda c: (len(c), c))


POSTCODES_IO = "https://api.postcodes.io/outcodes/{code}"
DEFAULT_SCOPE = ("SE16", "SE8", "E14")


def zone_bounds() -> dict[str, tuple[int, int]]:
    seen: dict[str, list[int]] = {}
    for zone, codes in ZONE_DISTRICTS.items():
        for code in codes:
            seen.setdefault(code, []).append(zone)
    return {code: (min(zones), max(zones)) for code, zones in seen.items()}


def fetch_centroid(code: str, *, timeout: int = 10) -> tuple[float, float] | None:
    """Look up a district centroid. Open ONS data, no key required."""
    try:
        with urllib.request.urlopen(POSTCODES_IO.format(code=code), timeout=timeout) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    result = payload.get("result") or {}
    lat, lng = result.get("latitude"), result.get("longitude")
    return (float(lat), float(lng)) if lat is not None and lng is not None else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--enable",
        default=",".join(DEFAULT_SCOPE),
        help="districts the source should watch now (comma separated)",
    )
    ap.add_argument(
        "--source",
        action="append",
        help="source to scope; repeatable. Default: every enabled source",
    )
    ap.add_argument(
        "--skip-coords", action="store_true", help="do not call postcodes.io; leave lat/lng null"
    )
    args = ap.parse_args()

    load_env()
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print(
            "DATABASE_URL is not set.\n"
            "  Create .env next to pyproject.toml (copy .env.example) and put the\n"
            "  Supabase connection string in it, or set the variable in the shell.",
            file=sys.stderr,
        )
        return 2

    scope = {c.strip().upper() for c in args.enable.split(",") if c.strip()}
    bounds = zone_bounds()
    districts = all_districts()

    # Checked against every district, not only the zoned ones: --enable HA1 used to
    # be refused as "not in the zone 1-3 lists", which was true and unhelpful.
    unknown = scope - set(districts)
    if unknown:
        print(f"not London districts: {sorted(unknown)}", file=sys.stderr)
        return 2

    coords: dict[str, tuple[float, float] | None] = {}
    if not args.skip_coords:
        print(f"fetching {len(districts)} district centroids from postcodes.io", file=sys.stderr)
        for i, code in enumerate(districts, 1):
            coords[code] = fetch_centroid(code)
            if i % 25 == 0:
                print(f"  {i}/{len(districts)}", file=sys.stderr)
            time.sleep(0.05)  # open service; stay well below any sensible limit

    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        if args.source:
            keys = args.source
            missing = [
                k for k in keys
                if conn.execute("SELECT 1 FROM sources WHERE key = %s", (k,)).fetchone() is None
            ]
            if missing:
                print(f"unknown sources {missing}; apply the migrations", file=sys.stderr)
                return 2
        else:
            keys = [
                r["key"] for r in
                conn.execute(
                    # Feed sources are excluded from the default sweep on purpose.
                    # `enabled` means a scrape scope for a source that fetches pages —
                    # one more district is one more request — and something else
                    # entirely for a feed, where nothing is requested per district and
                    # narrowing it only throws away listings that already arrived.
                    # Including them here would silently reset a feed's coverage to
                    # `--enable` every time this script is run for the scrapers.
                    # Name one explicitly with --source to override.
                    """
                    SELECT key FROM sources
                     WHERE enabled AND coalesce(config->>'kind', '') <> 'telegram_feed'
                     ORDER BY key
                    """
                ).fetchall()
            ]
            if not keys:
                print("no enabled sources; apply the migrations first", file=sys.stderr)
                return 2

        for code in districts:
            # The zone where it is known, null where it is not. Coalescing to a
            # number would put a guess where a filter can read it; the existing
            # `approx = true` already says the zone is not authoritative.
            zmin, zmax = bounds.get(code, (None, None))
            latlng = coords.get(code)
            conn.execute(
                """
                INSERT INTO locations
                       (kind, code, city, tfl_zone_min, tfl_zone_max, approx, lat, lng)
                VALUES ('postcode_district', %(code)s, 'London',
                        %(zmin)s, %(zmax)s, true, %(lat)s, %(lng)s)
                ON CONFLICT (kind, code) DO UPDATE
                   -- coalesce so re-seeding never *removes* zone knowledge a
                   -- previous run or a manual correction put there.
                   SET tfl_zone_min = coalesce(excluded.tfl_zone_min, locations.tfl_zone_min),
                       tfl_zone_max = coalesce(excluded.tfl_zone_max, locations.tfl_zone_max),
                       lat = coalesce(excluded.lat, locations.lat),
                       lng = coalesce(excluded.lng, locations.lng)
                """,
                {
                    "code": code,
                    "zmin": zmin,
                    "zmax": zmax,
                    "lat": latlng[0] if latlng else None,
                    "lng": latlng[1] if latlng else None,
                },
            )

        # external_id is what the source itself uses to denote the place. For
        # sitemap-based discovery that is the outward code appearing in a listing
        # URL; a source that searches per area needs its own identifier, and
        # existing values are therefore left alone rather than overwritten.
        summaries = []
        for key in keys:
            conn.execute(
                """
                INSERT INTO source_locations (source_key, location_id, external_id, enabled)
                SELECT %(source)s, l.id, lower(l.code), l.code = ANY(%(scope)s)
                  FROM locations l
                 WHERE l.kind = 'postcode_district'
                ON CONFLICT (source_key, location_id) DO UPDATE
                   SET enabled = excluded.enabled
                """,
                {"source": key, "scope": list(scope)},
            )
            summaries.append((key, conn.execute(
                """
                SELECT count(*) AS total,
                       count(*) FILTER (WHERE sl.enabled) AS enabled,
                       count(*) FILTER (WHERE l.lat IS NULL) AS without_coords
                  FROM source_locations sl JOIN locations l ON l.id = sl.location_id
                 WHERE sl.source_key = %s
                """,
                (key,),
            ).fetchone()))
        conn.commit()

    for key, summary in summaries:
        assert summary is not None
        print(
            f"{key}: {summary['total']} districts, {summary['enabled']} enabled "
            f"({', '.join(sorted(scope))}), {summary['without_coords']} without coordinates"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
