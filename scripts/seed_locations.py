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
import sys
import time
import urllib.error
import urllib.request

import psycopg
from psycopg.rows import dict_row

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

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    scope = {c.strip().upper() for c in args.enable.split(",") if c.strip()}
    bounds = zone_bounds()

    unknown = scope - bounds.keys()
    if unknown:
        print(f"not in the zone 1-3 lists: {sorted(unknown)}", file=sys.stderr)
        return 2

    coords: dict[str, tuple[float, float] | None] = {}
    if not args.skip_coords:
        print(f"fetching {len(bounds)} district centroids from postcodes.io", file=sys.stderr)
        for i, code in enumerate(sorted(bounds), 1):
            coords[code] = fetch_centroid(code)
            if i % 25 == 0:
                print(f"  {i}/{len(bounds)}", file=sys.stderr)
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
                conn.execute("SELECT key FROM sources WHERE enabled ORDER BY key").fetchall()
            ]
            if not keys:
                print("no enabled sources; apply the migrations first", file=sys.stderr)
                return 2

        for code, (zmin, zmax) in sorted(bounds.items()):
            latlng = coords.get(code)
            conn.execute(
                """
                INSERT INTO locations
                       (kind, code, city, tfl_zone_min, tfl_zone_max, approx, lat, lng)
                VALUES ('postcode_district', %(code)s, 'London',
                        %(zmin)s, %(zmax)s, true, %(lat)s, %(lng)s)
                ON CONFLICT (kind, code) DO UPDATE
                   SET tfl_zone_min = excluded.tfl_zone_min,
                       tfl_zone_max = excluded.tfl_zone_max,
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
