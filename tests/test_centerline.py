"""Centerline projection + snap tests.

Two layers:

1. **Synthetic geometry** — square / closed loop at a known origin, exercising
   the equirectangular projection, vertex snap, and central-difference
   tangent. Catches sign errors and the open/closed-loop seam bug.

2. **Monaco regression pins** — the three Monaco snapshot YAMLs encode poses
   derived from `monaco_centerline.geojson` via `scripts/derive_corner_poses.py`.
   If either the GeoJSON or the corner hints change, the YAMLs go stale and CI
   surfaces it here.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
import yaml

from twinkly_mockup.centerline import EARTH_RADIUS_M, Centerline, Pose
from twinkly_mockup.config import load_config

CONFIGS = Path(__file__).resolve().parent.parent / "configs"
CENTERLINE_GEOJSON = CONFIGS / "monaco_centerline.geojson"
MOSAIC_SIDECAR = CONFIGS / "monaco_mosaic.yaml"


def _write_geojson(
    tmp_path: Path,
    coords: list[tuple[float, float]],
    name: str = "track.geojson",
) -> Path:
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {"type": "LineString", "coordinates": coords},
            }
        ],
    }
    path = tmp_path / name
    path.write_text(json.dumps(payload))
    return path


def test_projection_east_unit_step(tmp_path: Path) -> None:
    """A 1° longitude step east of origin lands on +x_ENU = cos(lat) * R * radians."""
    origin_lat, origin_lon = 43.0, 7.0
    path = _write_geojson(tmp_path, [(7.0, 43.0), (8.0, 43.0)])
    centerline = Centerline.load_geojson(path, origin_lat=origin_lat, origin_lon=origin_lon)
    expected_x = math.radians(1.0) * math.cos(math.radians(origin_lat)) * EARTH_RADIUS_M
    assert centerline.x[0] == pytest.approx(0.0, abs=1e-9)
    assert centerline.x[1] == pytest.approx(expected_x, rel=1e-9)
    assert centerline.y[1] == pytest.approx(0.0, abs=1e-9)


def test_projection_north_unit_step(tmp_path: Path) -> None:
    """A 1° latitude step north of origin lands on +y_ENU = R * radians (no lon scale)."""
    origin_lat, origin_lon = 43.0, 7.0
    path = _write_geojson(tmp_path, [(7.0, 43.0), (7.0, 44.0)])
    centerline = Centerline.load_geojson(path, origin_lat=origin_lat, origin_lon=origin_lon)
    expected_y = math.radians(1.0) * EARTH_RADIUS_M
    assert centerline.x[1] == pytest.approx(0.0, abs=1e-9)
    assert centerline.y[1] == pytest.approx(expected_y, rel=1e-9)


def test_snap_returns_nearest_vertex_pose(tmp_path: Path) -> None:
    """A query halfway between two vertices snaps to whichever is closer."""
    # Square loop, side ~10 m, at origin (so projection is simple).
    # Vertices at NE corner of unit-cell pattern around origin.
    side_deg = 10.0 / EARTH_RADIUS_M * (180.0 / math.pi)  # ~10 m in latitude
    coords = [
        (7.0, 43.0),
        (7.0 + side_deg, 43.0),
        (7.0 + side_deg, 43.0 + side_deg),
        (7.0, 43.0 + side_deg),
        (7.0, 43.0),
    ]
    path = _write_geojson(tmp_path, coords)
    centerline = Centerline.load_geojson(path, origin_lat=43.0, origin_lon=7.0)
    assert centerline.closed is True
    # Query 1 m east of origin — closest is vertex 0 at (0, 0).
    pose = centerline.snap(x_m=1.0, y_m=0.1)
    assert pose.x_m == pytest.approx(0.0, abs=1e-9)
    assert pose.y_m == pytest.approx(0.0, abs=1e-9)


def test_tangent_at_closed_loop_seam(tmp_path: Path) -> None:
    """For a closed loop, vertex 0's tangent uses the last vertex as `prev`,
    not vertex 0 itself — otherwise the seam reports a degenerate tangent."""
    # Square loop centered at origin; vertex 0 sits on a corner between two
    # known edges (N→E in racing direction, say). Central difference at vertex
    # 0 should average those, giving SE-ish tangent — not zero.
    side_m = 10.0
    side_deg_lat = side_m / EARTH_RADIUS_M * (180.0 / math.pi)
    side_deg_lon = side_deg_lat / math.cos(math.radians(43.0))
    # Build a square going CCW: E vertex → N vertex → W vertex → S vertex → E.
    coords = [
        (7.0 + side_deg_lon, 43.0),  # E
        (7.0, 43.0 + side_deg_lat),  # N
        (7.0 - side_deg_lon, 43.0),  # W
        (7.0, 43.0 - side_deg_lat),  # S
        (7.0 + side_deg_lon, 43.0),  # close back to E
    ]
    path = _write_geojson(tmp_path, coords)
    centerline = Centerline.load_geojson(path, origin_lat=43.0, origin_lon=7.0)
    assert centerline.closed is True
    # At the E vertex (index 0), incoming edge is from S (vertex 3) → E,
    # outgoing edge is E → N. Central diff: prev=S (0, -s), next=N (0, +s),
    # → tangent points NORTH (yaw = +π/2).
    yaw = centerline.tangent_at(0)
    assert yaw == pytest.approx(math.pi / 2, abs=1e-6)


def test_tangent_at_open_polyline_endpoints(tmp_path: Path) -> None:
    """Open polylines fall back to one-sided difference at endpoints."""
    coords = [(7.0, 43.0), (7.001, 43.0), (7.002, 43.0)]  # straight east
    path = _write_geojson(tmp_path, coords)
    centerline = Centerline.load_geojson(path, origin_lat=43.0, origin_lon=7.0)
    assert centerline.closed is False
    # Endpoint tangent is forward (east) → yaw = 0.
    assert centerline.tangent_at(0) == pytest.approx(0.0, abs=1e-9)
    assert centerline.tangent_at(2) == pytest.approx(0.0, abs=1e-9)


def test_rejects_geojson_with_no_linestring(tmp_path: Path) -> None:
    bad = tmp_path / "no_line.geojson"
    bad.write_text(json.dumps({"type": "FeatureCollection", "features": []}))
    with pytest.raises(ValueError, match="exactly one LineString"):
        Centerline.load_geojson(bad, origin_lat=0.0, origin_lon=0.0)


def test_rejects_too_few_vertices(tmp_path: Path) -> None:
    bad = _write_geojson(tmp_path, [(7.0, 43.0)])
    with pytest.raises(ValueError, match="fewer than 2 vertices"):
        Centerline.load_geojson(bad, origin_lat=43.0, origin_lon=7.0)


# --- Monaco regression pins -------------------------------------------------

# (corner_name, expected centerline tangent at snapped vertex,
#  expected (x_m, y_m, yaw_rad) shipped in configs/monaco_<name>.yaml).
# The shipped yaw_rad equals tangent + π/2 (wrapped to (-π, π]) — see the
# `YAW_BIAS_RAD` derivation in scripts/derive_corner_poses.py.
MONACO_CORNERS: list[tuple[str, float, float, float, float]] = [
    ("massenet", 426.525, 461.197, 0.937199, 2.507995),
    ("loews", 516.688, 383.941, 1.700252, -3.012137),
    ("tabac", -119.038, -161.636, -0.991609, 0.579187),
]


@pytest.mark.parametrize(
    ("name", "x_m", "y_m", "tangent_rad", "yaw_rad"), MONACO_CORNERS
)
def test_monaco_snapshot_matches_centerline_derivation(
    name: str, x_m: float, y_m: float, tangent_rad: float, yaw_rad: float
) -> None:
    """Shipped YAML must match what centerline + corner hint + +π/2 bias produces."""
    # Hints live in scripts/derive_corner_poses.py — import lazily so the test
    # surfaces a clear failure if the script moves.
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "derive_corner_poses",
        Path(__file__).resolve().parent.parent / "scripts" / "derive_corner_poses.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    hint_lat, hint_lon = module.CORNER_HINTS[name]

    sidecar = yaml.safe_load(MOSAIC_SIDECAR.read_text())
    centerline = Centerline.load_geojson(
        CENTERLINE_GEOJSON,
        origin_lat=sidecar["origin_lat"],
        origin_lon=sidecar["origin_lon"],
    )
    pose = centerline.snap_latlon(
        hint_lat,
        hint_lon,
        origin_lat=sidecar["origin_lat"],
        origin_lon=sidecar["origin_lon"],
    )
    # Tolerance: snap is exact to a vertex (sub-mm); atan2 of central diff is
    # microradian-stable for fixed inputs.
    assert pose.x_m == pytest.approx(x_m, abs=1e-3)
    assert pose.y_m == pytest.approx(y_m, abs=1e-3)
    assert pose.yaw_rad == pytest.approx(tangent_rad, abs=1e-6)

    # And the corresponding shipped YAML must carry the bias-adjusted yaw.
    cfg = load_config(CONFIGS / f"monaco_{name}.yaml")
    assert cfg.snapshot.x_m == pytest.approx(x_m, abs=1e-3)
    assert cfg.snapshot.y_m == pytest.approx(y_m, abs=1e-3)
    assert cfg.snapshot.yaw_rad == pytest.approx(yaw_rad, abs=1e-6)
    assert cfg.car.orientation_deg == pytest.approx(-90.0)


def test_pose_is_frozen_dataclass() -> None:
    """Pose is frozen so callers can't accidentally mutate snap() results."""
    pose = Pose(x_m=1.0, y_m=2.0, yaw_rad=0.5)
    with pytest.raises(AttributeError):
        pose.x_m = 99.0  # type: ignore[misc]
