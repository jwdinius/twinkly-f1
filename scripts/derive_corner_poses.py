#!/usr/bin/env python3
"""Derive snapshot (x_m, y_m, yaw_rad) for the Monaco MVP corners from the
vendored centerline GeoJSON.

This is the centerline-driven authoring step for the three Monaco snapshot
YAMLs. Each corner is picked by a human-chosen lat/lon reference; we snap to
the nearest centerline vertex, read its tangent, and bias by +π/2 so that
when the renderer rotates the world by `yaw_rad` the local track tangent
ends up on the image-RIGHT axis — which is where the car nose points after
`car.orientation_deg = -90` is applied (nose-up → nose-right under CCW PIL
rotation). The asymmetric wall cutout (more tiles to the right of the
LEGO mount in the standard layout) puts the "track ahead" in the wider zone.

Output values go straight into `configs/monaco_<corner>.yaml`. Re-run after
editing the GeoJSON or the corner lat/lon picks; the regression test in
`tests/test_centerline.py` will then fail until the YAMLs are updated, so
config drift surfaces in CI.
"""

from __future__ import annotations

import math
from pathlib import Path

import yaml

from twinkly_mockup.centerline import Centerline

REPO = Path(__file__).resolve().parent.parent
MOSAIC_SIDECAR = REPO / "configs" / "monaco_mosaic.yaml"
CENTERLINE_GEOJSON = REPO / "configs" / "monaco_centerline.geojson"

# Renderer convention: car silhouette is drawn nose-up, then rotated by
# `car.orientation_deg` (CCW). With orientation_deg = -90, the nose ends up
# pointing image-RIGHT. The mosaic sampler puts ENU direction `yaw_rad` at
# image-up, so image-right corresponds to ENU direction `yaw_rad - π/2`.
# To make the nose align with the local track tangent, set
#   yaw_rad = tangent + π/2.
YAW_BIAS_RAD: float = math.pi / 2

# Human-picked (lat, lon) hint for each named corner. Snapping to the nearest
# centerline vertex absorbs the slop in these — they only need to be closer
# to the intended corner than to any other corner of the polyline. Lifted by
# eye from Google/OSM aerial imagery of the Monaco circuit.
CORNER_HINTS: dict[str, tuple[float, float]] = {
    "massenet": (43.74097, 7.42855),  # Turn 3 — top of Beau Rivage climb
    "loews": (43.74033, 7.42971),  # Turn 6 — Fairmont / Grand Hotel hairpin apex
    # Turn 12 — apex where harbor straight bends from south- into east-heading
    # before the swimming pool. Hint must sit on the racing-direction leg, not
    # the return-to-S/F leg (the polyline passes near both at similar lat).
    "tabac": (43.73542, 7.42189),
}


def main() -> None:
    sidecar = yaml.safe_load(MOSAIC_SIDECAR.read_text())
    origin_lat = sidecar["origin_lat"]
    origin_lon = sidecar["origin_lon"]

    centerline = Centerline.load_geojson(
        CENTERLINE_GEOJSON, origin_lat=origin_lat, origin_lon=origin_lon
    )

    print(f"origin: lat={origin_lat}, lon={origin_lon}")
    print(f"centerline: {centerline.x.size} vertices, closed={centerline.closed}")
    print()
    for name, (lat, lon) in CORNER_HINTS.items():
        pose = centerline.snap_latlon(
            lat, lon, origin_lat=origin_lat, origin_lon=origin_lon
        )
        yaw_rad = pose.yaw_rad + YAW_BIAS_RAD
        # Wrap into (-π, π] so YAML values stay readable; the schema accepts
        # anything in [-2π, 2π] either way.
        yaw_rad = (yaw_rad + math.pi) % (2 * math.pi) - math.pi
        print(
            f"{name}: x_m={pose.x_m:.3f}, y_m={pose.y_m:.3f}, "
            f"yaw_rad={yaw_rad:.6f} "
            f"(tangent={math.degrees(pose.yaw_rad):.2f}°, "
            f"yaw={math.degrees(yaw_rad):.2f}°)"
        )


if __name__ == "__main__":
    main()
