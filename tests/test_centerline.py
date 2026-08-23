"""Centerline projection + snap tests.

Two layers:

1. **Synthetic geometry** — square / closed loop at a known origin, exercising
   the flat-ENU projection, vertex snap, and central-difference
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

from twinkly_mockup.centerline import (
    Centerline,
    Pose,
    centerline_bbox,
    read_linestring_lonlat,
)
from twinkly_mockup.circuits import load_manifest
from twinkly_mockup.enu import FlatEnu
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
    """A 1° longitude step east lands on the prime-vertical east scale, not `a`."""
    origin_lat, origin_lon = 43.0, 7.0
    path = _write_geojson(tmp_path, [(7.0, 43.0), (8.0, 43.0)])
    centerline = Centerline.load_geojson(path, origin_lat=origin_lat, origin_lon=origin_lon)
    frame = FlatEnu.at(origin_lat, origin_lon)
    expected_x = math.radians(1.0) * frame.m_per_rad_east
    assert centerline.x[0] == pytest.approx(0.0, abs=1e-9)
    assert centerline.x[1] == pytest.approx(expected_x, rel=1e-9)
    assert centerline.y[1] == pytest.approx(0.0, abs=1e-9)


def test_projection_north_unit_step(tmp_path: Path) -> None:
    """A 1° latitude step north lands on the meridional scale, with no lon term."""
    origin_lat, origin_lon = 43.0, 7.0
    path = _write_geojson(tmp_path, [(7.0, 43.0), (7.0, 44.0)])
    centerline = Centerline.load_geojson(path, origin_lat=origin_lat, origin_lon=origin_lon)
    frame = FlatEnu.at(origin_lat, origin_lon)
    expected_y = math.radians(1.0) * frame.m_per_rad_north
    assert centerline.x[1] == pytest.approx(0.0, abs=1e-9)
    assert centerline.y[1] == pytest.approx(expected_y, rel=1e-9)


def test_snap_returns_nearest_vertex_pose(tmp_path: Path) -> None:
    """A query halfway between two vertices snaps to whichever is closer."""
    # Square loop, side ~10 m, at origin (so projection is simple).
    # Vertices at NE corner of unit-cell pattern around origin.
    side_deg = math.degrees(10.0 / FlatEnu.at(43.0, 7.0).m_per_rad_north)  # ~10 m
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
    frame = FlatEnu.at(43.0, 7.0)
    side_deg_lat = math.degrees(side_m / frame.m_per_rad_north)
    side_deg_lon = math.degrees(side_m / frame.m_per_rad_east)
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


# --- Snapshot regression pins -----------------------------------------------

# (circuit, corner_name, expected (x_m, y_m), expected centerline tangent at the
#  snapped vertex, expected yaw_rad shipped in configs/<circuit>_<corner>.yaml).
# The shipped yaw_rad equals tangent + π/2 (wrapped to (-π, π]) — see the
# `YAW_BIAS_RAD` derivation in scripts/derive_corner_poses.py.
#
# Silverstone is the validation target; Monaco is kept as a second circuit so
# these pins prove the derivation is manifest-driven rather than Monaco-shaped.
SNAPSHOT_CORNERS: list[tuple[str, str, float, float, float, float]] = [
    ("monaco", "massenet", 205.992, 460.399, 0.935522, 2.506319),
    ("monaco", "loews", 296.300, 383.291, 1.700703, -3.011686),
    ("monaco", "tabac", -340.445, -161.245, -0.989999, 0.580797),
    ("silverstone", "club", -520.488, -402.204, 1.019997, 2.590793),
    ("silverstone", "luffield", -261.503, 511.424, -2.503999, -0.933202),
    ("silverstone", "abbey", -231.470, -23.000, 0.900131, 2.470927),
]


@pytest.mark.parametrize(
    ("circuit_name", "name", "x_m", "y_m", "tangent_rad", "yaw_rad"), SNAPSHOT_CORNERS
)
def test_snapshot_matches_centerline_derivation(
    circuit_name: str,
    name: str,
    x_m: float,
    y_m: float,
    tangent_rad: float,
    yaw_rad: float,
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
    hint_lat, hint_lon = module.CORNER_HINTS[circuit_name][name]

    # Sidecar and centerline come from the manifest, not from constants here —
    # the same resolution path the authoring script uses.
    circuit = load_manifest()[circuit_name]
    sidecar = yaml.safe_load(circuit.sidecar.read_text())
    centerline = Centerline.load_geojson(
        circuit.centerline,
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
    cfg = load_config(CONFIGS / f"{circuit_name}_{name}.yaml")
    assert cfg.snapshot.x_m == pytest.approx(x_m, abs=1e-3)
    assert cfg.snapshot.y_m == pytest.approx(y_m, abs=1e-3)
    assert cfg.snapshot.yaw_rad == pytest.approx(yaw_rad, abs=1e-6)
    assert cfg.car.orientation_deg == pytest.approx(-90.0)


def test_every_corner_hint_has_a_shipped_snapshot() -> None:
    """A hint with no YAML is a corner that was picked and never shipped."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "derive_corner_poses",
        Path(__file__).resolve().parent.parent / "scripts" / "derive_corner_poses.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    pinned = {(c, n) for c, n, *_ in SNAPSHOT_CORNERS}
    for circuit_name, hints in module.CORNER_HINTS.items():
        for name in hints:
            assert (CONFIGS / f"{circuit_name}_{name}.yaml").is_file()
            assert (circuit_name, name) in pinned


def test_pose_is_frozen_dataclass() -> None:
    """Pose is frozen so callers can't accidentally mutate snap() results."""
    pose = Pose(x_m=1.0, y_m=2.0, yaw_rad=0.5)
    with pytest.raises(AttributeError):
        pose.x_m = 99.0  # type: ignore[misc]


# --- download bbox -----------------------------------------------------------


def test_bbox_margin_is_at_least_the_requested_metres(tmp_path: Path) -> None:
    """Every edge sits ≥ margin_m from the nearest vertex, measured in ENU metres."""
    margin_m = 100.0
    coords = [(7.42, 43.73), (7.43, 43.74), (7.421, 43.741), (7.42, 43.73)]
    bbox = centerline_bbox(_write_geojson(tmp_path, coords), margin_m=margin_m)

    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    frame = FlatEnu.at(max(lats), min(lons))  # worst case: shortest degree of longitude
    east_edges = [min(lons) - bbox.west, bbox.east - max(lons)]
    north_edges = [min(lats) - bbox.south, bbox.north - max(lats)]
    edges_m = [math.radians(d) * frame.m_per_rad_east for d in east_edges]
    edges_m += [math.radians(d) * frame.m_per_rad_north for d in north_edges]
    for edge_m in edges_m:
        assert edge_m >= margin_m - 1e-6  # float round-trip, not slack
        # And no more than a hair over — the box is tight, not generous.
        assert edge_m < margin_m * 1.01


def test_bbox_contains_every_monaco_vertex() -> None:
    """The regression the derived bbox exists to prevent: a clipped centerline."""
    bbox = centerline_bbox(CENTERLINE_GEOJSON, margin_m=100.0)
    for lon, lat in read_linestring_lonlat(CENTERLINE_GEOJSON):
        assert bbox.west < lon < bbox.east
        assert bbox.south < lat < bbox.north


def test_bbox_zero_margin_is_the_tight_extent(tmp_path: Path) -> None:
    coords = [(7.0, 43.0), (7.01, 43.02), (7.0, 43.0)]
    bbox = centerline_bbox(_write_geojson(tmp_path, coords), margin_m=0.0)
    assert (bbox.south, bbox.west, bbox.north, bbox.east) == (43.0, 7.0, 43.02, 7.01)


def test_bbox_rejects_negative_margin(tmp_path: Path) -> None:
    path = _write_geojson(tmp_path, [(7.0, 43.0), (7.01, 43.02)])
    with pytest.raises(ValueError, match="non-negative"):
        centerline_bbox(path, margin_m=-1.0)


def test_bbox_corner_strings_are_lat_lon_for_racetrack_mosaic(tmp_path: Path) -> None:
    """racetrack-mosaic's --sw/--ne take `lat,lon`, not GeoJSON's lon/lat order."""
    bbox = centerline_bbox(
        _write_geojson(tmp_path, [(7.0, 43.0), (7.01, 43.02)]), margin_m=0.0
    )
    assert bbox.sw() == "43.000000,7.000000"
    assert bbox.ne() == "43.020000,7.010000"
