#!/usr/bin/env python3
"""Derive snapshot (x_m, y_m, yaw_rad) for a circuit's corners from its
vendored centerline GeoJSON.

This is the centerline-driven authoring step for the snapshot YAMLs. Each
corner is picked by a human-chosen lat/lon reference; we snap to the nearest
centerline vertex, read its tangent, and bias by +π/2 so that when the renderer
rotates the world by `yaw_rad` the local track tangent ends up on the
image-RIGHT axis — which is where the car nose points after
`car.orientation_deg = -90` is applied (nose-up → nose-right under CCW PIL
rotation). The asymmetric wall cutout (more tiles to the right of the LEGO
mount in the standard layout) puts the "track ahead" in the wider zone.

The Sidecar and Centerline a circuit uses are resolved through the circuit
manifest (`configs/circuits.json`), never named here — a circuit is declared in
one place (CONTEXT.md, ADR-0003) and this script is one of its consumers.

Output values go straight into `configs/<circuit>_<corner>.yaml`. Re-run after
editing a GeoJSON or a corner lat/lon pick; the regression test in
`tests/test_centerline.py` will then fail until the YAMLs are updated, so config
drift surfaces in CI.

    uv run scripts/derive_corner_poses.py                # every circuit with hints
    uv run scripts/derive_corner_poses.py silverstone    # just one
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from twinkly_mockup.centerline import Centerline  # noqa: E402
from twinkly_mockup.circuits import load_manifest  # noqa: E402

# Renderer convention: car silhouette is drawn nose-up, then rotated by
# `car.orientation_deg` (CCW). With orientation_deg = -90, the nose ends up
# pointing image-RIGHT. The mosaic sampler puts ENU direction `yaw_rad` at
# image-up, so image-right corresponds to ENU direction `yaw_rad - π/2`.
# To make the nose align with the local track tangent, set
#   yaw_rad = tangent + π/2.
YAW_BIAS_RAD: float = math.pi / 2

# Human-picked (lat, lon) hint per circuit, per named corner. Snapping to the
# nearest centerline vertex absorbs the slop in these — a hint only needs to be
# closer to the intended corner than to any other corner of the polyline.
# Lifted by eye from the circuit's own mosaic with the centerline overlaid.
CORNER_HINTS: dict[str, dict[str, tuple[float, float]]] = {
    # Monaco is retained as a regression fixture, not a validation target: the
    # circuit runs through a tunnel, so a top-down mosaic cannot render a lap.
    "monaco": {
        "massenet": (43.74097, 7.42855),  # Turn 3 — top of Beau Rivage climb
        "loews": (43.74033, 7.42971),  # Turn 6 — Fairmont / Grand Hotel hairpin apex
        # Turn 12 — apex where harbor straight bends from south- into east-heading
        # before the swimming pool. Hint must sit on the racing-direction leg, not
        # the return-to-S/F leg (the polyline passes near both at similar lat).
        "tabac": (43.73542, 7.42189),
    },
    # Silverstone, the validation target. Picking these the way Monaco's were
    # picked — by corner *dynamics* — produces three identical grey rectangles.
    # Silverstone's half-width is 7 m, so at the LEGO-scale 12x8 m viewport both
    # painted edges sit outside the crop and a centerline-centred sample is bare
    # asphalt; Monaco's 5 m keeps its edges just inside. Measured over every
    # centerline vertex, crop contrast (σ of grey) has median 2.9 here against
    # Monaco's 35.4. So these three are chosen by contrast first — they are the
    # only places on the lap with anything in frame — and each is then read for
    # whichever dynamic case it happens to cover.
    "silverstone": {
        # Turn 18 exit onto the Hamilton straight, past The Wing. σ=44, the
        # highest anywhere on the lap: pit wall, painted edge and grandstand
        # shadow all land in frame. The "does anything read as track?" case.
        "club": (52.06766, -1.02417),
        # Turn 7 — Luffield, a long ~200° slow right. σ=31, and the most heading
        # swept in one corner: the Loews analogue for yaw-rotation continuity
        # and tight cutout margins.
        "luffield": (52.07587, -1.02039),
        # Turn 1 — Abbey, the fast right off the end of the Hamilton straight.
        # σ=28, and the high-speed turn-in case.
        "abbey": (52.07107, -1.01995),
    },
}


def poses_for(circuit_name: str) -> None:
    """Print the YAML-ready pose for each of `circuit_name`'s corner hints."""
    circuit = load_manifest()[circuit_name]
    sidecar = yaml.safe_load(circuit.sidecar.read_text())
    origin_lat = sidecar["origin_lat"]
    origin_lon = sidecar["origin_lon"]

    centerline = Centerline.load_geojson(
        circuit.centerline, origin_lat=origin_lat, origin_lon=origin_lon
    )

    print(f"{circuit_name}: {circuit.title}")
    print(f"  sidecar: {circuit.sidecar.name}  origin: lat={origin_lat}, lon={origin_lon}")
    print(f"  centerline: {centerline.x.size} vertices, closed={centerline.closed}")
    for name, (lat, lon) in CORNER_HINTS[circuit_name].items():
        pose = centerline.snap_latlon(
            lat, lon, origin_lat=origin_lat, origin_lon=origin_lon
        )
        yaw_rad = pose.yaw_rad + YAW_BIAS_RAD
        # Wrap into (-π, π] so YAML values stay readable; the schema accepts
        # anything in [-2π, 2π] either way.
        yaw_rad = (yaw_rad + math.pi) % (2 * math.pi) - math.pi
        print(
            f"  {name}: x_m={pose.x_m:.3f}, y_m={pose.y_m:.3f}, "
            f"yaw_rad={yaw_rad:.6f} "
            f"(tangent={math.degrees(pose.yaw_rad):.2f}°, "
            f"yaw={math.degrees(yaw_rad):.2f}°)"
        )
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "circuit",
        nargs="*",
        choices=sorted(CORNER_HINTS),
        help="circuit to derive poses for (default: all circuits with hints)",
    )
    args = parser.parse_args()
    for name in args.circuit or sorted(CORNER_HINTS):
        poses_for(name)


if __name__ == "__main__":
    main()
