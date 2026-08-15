"""import-lap bridge tests: frame crossing + uniform resampling.

The fixtures forward-project known lat/lon points into the *solver* frame (via
the same GPS_parameters mapping fastest-lap documents), so the bridge's inverse
crossing must land back on the independently-computed mockup ENU coordinates —
a true round-trip through lat/lon.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from twinkly_mockup.cli import app
from twinkly_mockup.import_lap import (
    ImportLapError,
    import_lap,
    load_native_lap,
    parse_gps_parameters,
)
from twinkly_mockup.trajectory import Trajectory

# --- solver-frame (fastest-lap) fixture parameters ---------------------------
FL_R = 6378388.0  # International 1924, as fastest-lap uses
FL_ORIGIN_LAT = 43.73
FL_ORIGIN_LON = 7.42
FL_REF_LAT = 43.73

# --- mockup ENU fixture parameters (deliberately a *different* origin) -------
WGS84_A = 6378137.0
WGS84_F = 1.0 / 298.257223563
MOCK_ORIGIN_LAT = 43.736872
MOCK_ORIGIN_LON = 7.423325


def _latlon_to_fl(lat: float, lon: float) -> tuple[float, float]:
    """Forward-project lat/lon into the solver frame, per fastest-lap's own formula.

    Transcribed from the `<GPS_parameters>` comment fastest-lap writes into every
    circuit XML (`circuit_preprocessor.hpp:818`), which the C++ at line 274
    implements — note `y` runs *south*. This sign is the whole point of the
    fixture: copying the bridge's inverse instead would make the round trip close
    on itself no matter which way either one pointed.
    """
    x = FL_R * math.cos(math.radians(FL_REF_LAT)) * math.radians(lon - FL_ORIGIN_LON)
    y = -FL_R * math.radians(lat - FL_ORIGIN_LAT)
    return x, y


def _enu_heading_to_fl(heading: float) -> float:
    """Invert a north-up ENU heading into the solver's y-south, z-down frame."""
    return -heading


def _mockup_scales(olat: float = MOCK_ORIGIN_LAT) -> tuple[float, float]:
    """WGS84 radii of curvature, spelled out rather than imported.

    The point of these fixtures is that the bridge lands on independently
    computed coordinates, so the east/north scales are rebuilt from the
    ellipsoid here instead of reusing `FlatEnu`'s arithmetic.
    """
    e2 = WGS84_F * (2.0 - WGS84_F)
    phi = math.radians(olat)
    w2 = 1.0 - e2 * math.sin(phi) ** 2
    prime_vertical = WGS84_A / math.sqrt(w2)
    meridional = WGS84_A * (1.0 - e2) / (w2 * math.sqrt(w2))
    return prime_vertical * math.cos(phi), meridional


def _latlon_to_mockup(
    lat: float, lon: float, olat: float = MOCK_ORIGIN_LAT, olon: float = MOCK_ORIGIN_LON
) -> tuple[float, float]:
    east_per_rad, north_per_rad = _mockup_scales(olat)
    return (
        math.radians(lon - olon) * east_per_rad,
        math.radians(lat - olat) * north_per_rad,
    )


def _write_circuit_xml(
    dir_: Path,
    *,
    ref_lat: float = FL_REF_LAT,
    with_gps: bool = True,
) -> Path:
    path = dir_ / "monaco.xml"
    gps = (
        f"""
    <GPS_parameters>
        <origin_longitude units="deg">{FL_ORIGIN_LON}</origin_longitude>
        <origin_latitude units="deg">{FL_ORIGIN_LAT}</origin_latitude>
        <earth_radius units="m">{FL_R}</earth_radius>
        <reference_latitude units="deg">{ref_lat}</reference_latitude>
    </GPS_parameters>"""
        if with_gps
        else ""
    )
    path.write_text(f'<circuit format="discrete" type="closed">{gps}\n</circuit>\n')
    return path


def _write_sidecar(dir_: Path) -> Path:
    path = dir_ / "mosaic.yaml"
    path.write_text(
        "path: mosaic.png\n"
        f"origin_lat: {MOCK_ORIGIN_LAT}\n"
        f"origin_lon: {MOCK_ORIGIN_LON}\n"
        "origin_px: [100.0, 100.0]\n"
        "m_per_px: 0.2\n"
        "north_angle_deg: 0.0\n"
    )
    return path


def _write_native_csv(
    dir_: Path,
    rows: list[tuple[float, float, float, float]],
    *,
    header: tuple[str, ...] = ("time", "x", "y", "yaw"),
) -> Path:
    path = dir_ / "native.csv"
    lines = [",".join(header)]
    lines += [",".join(f"{v:.12g}" for v in row) for row in rows]
    path.write_text("\n".join(lines) + "\n")
    return path


# A short synthetic lap: known lat/lon points near the mockup origin, plus a
# heading at each. Times are uniform at `dt` so resampling is an identity map
# and output rows line up 1:1 with these points.
_DT = 0.02
_LATLON = [
    (43.7370, 7.4230),
    (43.7372, 7.4235),
    (43.7375, 7.4240),
    (43.7378, 7.4238),
    (43.7376, 7.4232),
]
_YAWS = [0.2, 0.8, 1.5, 2.6, -2.9]


def _native_rows() -> list[tuple[float, float, float, float]]:
    rows = []
    for i, ((lat, lon), yaw) in enumerate(zip(_LATLON, _YAWS, strict=True)):
        x, y = _latlon_to_fl(lat, lon)
        rows.append((i * _DT, x, y, yaw))
    return rows


def test_frame_crossing_round_trips_to_mockup_enu(tmp_path: Path) -> None:
    native = _write_native_csv(tmp_path, _native_rows())
    circuit = _write_circuit_xml(tmp_path)
    sidecar = _write_sidecar(tmp_path)
    out = tmp_path / "lap.csv"

    import_lap(native, circuit, sidecar, out, dt=_DT)
    traj = Trajectory.load(out)

    # Uniform times → resample is identity, so knot i is the crossed point i.
    for i, (lat, lon) in enumerate(_LATLON):
        exp_x, exp_y = _latlon_to_mockup(lat, lon)
        x, y, _ = traj.sample_at(i * _DT)
        assert x == pytest.approx(exp_x, abs=1e-6)
        assert y == pytest.approx(exp_y, abs=1e-6)


def test_positive_native_y_lands_south_of_the_solver_origin(tmp_path: Path) -> None:
    """The solver's `+y` is *south*: `y = -earth_radius·(lat - origin_lat)`.

    Pinned as a bare directional claim rather than a round trip, because a round
    trip closes on itself if the fixture and the bridge share a sign error — which
    is exactly what happened. `import_lap` read `+y` as north and mirrored every
    lap about the origin latitude (~1.7 km at Silverstone, clean off the mosaic).

    Sitting the solver origin north of the mockup origin makes the assertion
    concrete: a point at `+y` must come out *below* the point at `y = 0`.
    """
    rows = [(0.0, 0.0, 0.0, 0.0), (_DT, 0.0, 500.0, 0.0)]
    native = _write_native_csv(tmp_path, rows)
    out = tmp_path / "lap.csv"

    import_lap(
        native, _write_circuit_xml(tmp_path), _write_sidecar(tmp_path), out, dt=_DT
    )
    traj = Trajectory.load(out)

    _, y_at_origin, _ = traj.sample_at(0.0)
    _, y_at_plus_500, _ = traj.sample_at(_DT)
    assert y_at_plus_500 < y_at_origin
    # 500 m south on a sphere of radius FL_R, measured in the mockup's ENU metres.
    _, north_per_rad = _mockup_scales()
    assert y_at_plus_500 - y_at_origin == pytest.approx(
        -500.0 * north_per_rad / FL_R, rel=1e-6
    )


def test_yaw_flips_sense_crossing_out_of_the_y_south_frame(tmp_path: Path) -> None:
    """A yaw CCW about the solver's `z`-down axis is CW seen from above.

    The companion to the position flip above, and it fails the same way: a lap
    whose positions are right but whose headings are mirrored points the car
    across the track. Checked against the real Silverstone lap by comparing each
    yaw to the heading implied by the path's own finite differences — flipped,
    the mean disagreement is 0.88° (the car's sideslip); unflipped, 97°.

    `ref_lat == origin_lat` here so the axis-ratio correction is ~1.0035 and the
    coarse tolerance below isolates the sign, which is what this test is about.
    """
    headings_enu = [0.0, math.pi / 2, -math.pi / 2, 2.6]
    rows = [
        (i * _DT, 0.0, 0.0, _enu_heading_to_fl(h))
        for i, h in enumerate(headings_enu)
    ]
    native = _write_native_csv(tmp_path, rows)
    out = tmp_path / "lap.csv"

    import_lap(
        native,
        _write_circuit_xml(tmp_path, ref_lat=MOCK_ORIGIN_LAT),
        _write_sidecar(tmp_path),
        out,
        dt=_DT,
    )
    traj = Trajectory.load(out)

    for i, expected in enumerate(headings_enu):
        _, _, yaw_out = traj.sample_at(i * _DT)
        assert yaw_out == pytest.approx(expected, abs=2e-3)


def test_yaw_carries_the_frames_axis_ratio_even_at_a_matched_reference_latitude(
    tmp_path: Path,
) -> None:
    """Matching latitudes no longer make the heading map an identity.

    The solver frame is spherical — one radius on both axes — while the mockup
    frame scales east by `N·cos φ` and north by `M`. So even with
    `ref_lat == origin_lat` the two disagree by `N/M` (~1.0035 here), and a
    heading crossing between them picks that up. Under the old spherical mockup
    model the north scales cancelled and this was exactly identity; asserting
    the ratio rather than identity is what pins the fix.
    """
    native = _write_native_csv(tmp_path, _native_rows())
    circuit = _write_circuit_xml(tmp_path, ref_lat=MOCK_ORIGIN_LAT)
    sidecar = _write_sidecar(tmp_path)
    out = tmp_path / "lap.csv"

    import_lap(native, circuit, sidecar, out, dt=_DT)
    traj = Trajectory.load(out)

    east_per_rad, north_per_rad = _mockup_scales()
    solver_east_per_rad = FL_R * math.cos(math.radians(MOCK_ORIGIN_LAT))
    k = (east_per_rad / solver_east_per_rad) / (north_per_rad / FL_R)
    assert k == pytest.approx(1.0035, abs=1e-4)

    for i, yaw_in in enumerate(_YAWS):
        _, _, yaw_out = traj.sample_at(i * _DT)
        expected = math.atan2(-math.sin(yaw_in), k * math.cos(yaw_in))
        # Tolerance is the CSV's own `%.9g` write precision, not the maths'.
        assert yaw_out == pytest.approx(expected, abs=1e-8)
        assert -math.pi <= yaw_out <= math.pi


def test_output_is_schema_valid_and_uniform(tmp_path: Path) -> None:
    native = _write_native_csv(tmp_path, _native_rows())
    circuit = _write_circuit_xml(tmp_path)
    sidecar = _write_sidecar(tmp_path)
    out = tmp_path / "lap.csv"

    import_lap(native, circuit, sidecar, out, dt=_DT)
    traj = Trajectory.load(out)  # raises if the schema is violated
    assert traj.dt == pytest.approx(_DT)
    assert traj.t[0] == pytest.approx(0.0)  # rebased to zero


def test_resamples_non_uniform_native_times(tmp_path: Path) -> None:
    """Solver times are arc-length-based (non-uniform); output must be uniform
    and land on the interpolated path."""
    # Two points 1.0 s apart in native time; request a 0.25 s grid → 5 samples.
    (lat0, lon0), (lat1, lon1) = _LATLON[0], _LATLON[2]
    x0, y0 = _latlon_to_fl(lat0, lon0)
    x1, y1 = _latlon_to_fl(lat1, lon1)
    native = _write_native_csv(
        tmp_path,
        [(3.0, x0, y0, 0.1), (4.0, x1, y1, 0.1)],  # non-zero, non-unit t0
    )
    circuit = _write_circuit_xml(tmp_path)
    sidecar = _write_sidecar(tmp_path)
    out = tmp_path / "lap.csv"

    import_lap(native, circuit, sidecar, out, dt=0.25)
    traj = Trajectory.load(out)

    assert traj.dt == pytest.approx(0.25)
    assert tuple(traj.t) == pytest.approx((0.0, 0.25, 0.5, 0.75, 1.0))
    # Midpoint (t=0.5) is halfway along the crossed segment.
    mx, my, _ = traj.sample_at(0.5)
    ex0, ey0 = _latlon_to_mockup(lat0, lon0)
    ex1, ey1 = _latlon_to_mockup(lat1, lon1)
    assert mx == pytest.approx(0.5 * (ex0 + ex1), abs=1e-6)
    assert my == pytest.approx(0.5 * (ey0 + ey1), abs=1e-6)


def test_accepts_t_alias_and_ignores_extra_columns(tmp_path: Path) -> None:
    rows = [(*(_native_rows()[i]), 99.0) for i in range(len(_LATLON))]
    native = _write_native_csv(
        tmp_path, rows, header=("t", "x", "y", "yaw", "vx")
    )
    circuit = _write_circuit_xml(tmp_path)
    sidecar = _write_sidecar(tmp_path)
    out = tmp_path / "lap.csv"

    import_lap(native, circuit, sidecar, out, dt=_DT)
    assert Trajectory.load(out).dt == pytest.approx(_DT)


def test_missing_gps_parameters_rejected(tmp_path: Path) -> None:
    native = _write_native_csv(tmp_path, _native_rows())
    circuit = _write_circuit_xml(tmp_path, with_gps=False)
    sidecar = _write_sidecar(tmp_path)
    with pytest.raises(ImportLapError) as exc:
        import_lap(native, circuit, sidecar, tmp_path / "lap.csv", dt=_DT)
    assert "GPS_parameters" in str(exc.value)


def test_non_increasing_native_time_rejected(tmp_path: Path) -> None:
    rows = _native_rows()
    rows[2] = (rows[1][0], *rows[2][1:])  # duplicate a timestamp
    native = _write_native_csv(tmp_path, rows)
    with pytest.raises(ImportLapError) as exc:
        load_native_lap(native)
    assert "non-increasing time" in str(exc.value)


def test_missing_spatial_column_rejected(tmp_path: Path) -> None:
    native = _write_native_csv(
        tmp_path,
        [(0.0, 1.0, 2.0), (0.02, 3.0, 4.0)],
        header=("time", "x", "y"),  # yaw missing
    )
    with pytest.raises(ImportLapError) as exc:
        load_native_lap(native)
    assert "yaw" in str(exc.value)


def test_parse_gps_parameters_reads_all_fields(tmp_path: Path) -> None:
    gps = parse_gps_parameters(_write_circuit_xml(tmp_path))
    assert gps.origin_lat == pytest.approx(FL_ORIGIN_LAT)
    assert gps.origin_lon == pytest.approx(FL_ORIGIN_LON)
    assert gps.earth_radius == pytest.approx(FL_R)
    assert gps.reference_lat == pytest.approx(FL_REF_LAT)


def test_cli_import_lap_end_to_end(tmp_path: Path) -> None:
    native = _write_native_csv(tmp_path, _native_rows())
    circuit = _write_circuit_xml(tmp_path)
    sidecar = _write_sidecar(tmp_path)
    out = tmp_path / "lap.csv"

    result = CliRunner().invoke(
        app,
        [
            "import-lap",
            str(native),
            "--circuit",
            str(circuit),
            "--mosaic",
            str(sidecar),
            "--out",
            str(out),
            "--dt",
            str(_DT),
        ],
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    assert "samples" in result.output
    Trajectory.load(out)  # valid schema
