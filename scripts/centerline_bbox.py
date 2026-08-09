#!/usr/bin/env python3
"""Print the mosaic download bbox for a circuit's centerline, plus a margin.

The download region is not a hand-picked rectangle: it is the centerline's own
extent grown by a fixed margin in metres, so no vertex can fall outside the
imagery and no tile is fetched for asphalt that isn't there. `scripts/build_*
_mosaic.sh` calls this and feeds the result straight to racetrack-mosaic, which
keeps the bbox in step with the GeoJSON the same way ADR-0004 keeps the Sidecar
in step with the raster.

    scripts/centerline_bbox.py configs/monaco_centerline.geojson --margin-m 100
    # --sw 43.731463,7.420101 --ne 43.742013,7.431478

`--format` picks the shape of the output:

* `args`  (default) — `--sw lat,lon --ne lat,lon`, ready to splice into a
  racetrack-mosaic command line
* `shell` — `SW=lat,lon` / `NE=lat,lon` lines to `eval` in a build script
* `pretty` — `lat,lon -> lat,lon`, for the human-readable build note
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from twinkly_mockup.centerline import centerline_bbox  # noqa: E402

DEFAULT_MARGIN_M = 100.0
"""Metres of apron around the centerline. Wide enough for track limits, run-off
and the LEGO car's footprint at every circuit here; override with --margin-m."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("geojson", type=Path, help="centerline GeoJSON (one LineString)")
    parser.add_argument(
        "--margin-m",
        type=float,
        default=DEFAULT_MARGIN_M,
        help=f"metres of margin on every side (default: {DEFAULT_MARGIN_M:g})",
    )
    parser.add_argument(
        "--format",
        choices=("args", "shell", "pretty"),
        default="args",
        help="output shape (default: args)",
    )
    args = parser.parse_args(argv)

    bbox = centerline_bbox(args.geojson, margin_m=args.margin_m)

    if args.format == "args":
        print(f"--sw {bbox.sw()} --ne {bbox.ne()}")
    elif args.format == "shell":
        print(f"SW={bbox.sw()}")
        print(f"NE={bbox.ne()}")
    else:
        print(f"{bbox.sw()} -> {bbox.ne()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
