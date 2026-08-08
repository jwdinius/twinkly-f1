"""The shared flat-ENU tangent plane (#23).

`pyproj.Geod` is the authority: a flat-ENU offset is *supposed* to be the true
geodesic distance in that direction, and the whole point of the two radii of
curvature is that they make it so at the origin. The sphere-of-radius-`a` model
this replaced misses by ~±0.17% — small, but *anisotropic*, so nothing further
down the pipeline can absorb it.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from twinkly_mockup.enu import WGS84_A_M, FlatEnu

pyproj = pytest.importorskip("pyproj")

ORIGINS = [
    (43.736871113, 7.423323737),  # Monaco
    (52.071275707, -1.016577275),  # Silverstone
    (0.0, 0.0),  # equator — where the two radii are furthest apart
    (-37.849722, 144.968333),  # Albert Park, southern hemisphere
]


@pytest.mark.parametrize(("lat", "lon"), ORIGINS)
def test_scales_are_the_true_geodesic_metres_per_radian(lat: float, lon: float) -> None:
    """One metre of ENU is one metre on the ellipsoid, in both directions."""
    frame = FlatEnu.at(lat, lon)
    geod = pyproj.Geod(ellps="WGS84")
    # Distance is the assertion that isolates the scale factors; the per-axis
    # tolerance is looser because a 100 m geodesic due east curves a millimetre
    # off the parallel at these latitudes, which is the tangent plane's own
    # second-order error and not a scale error. Even 5 mm over 100 m is 30×
    # tighter than the 0.17% the spherical model was wrong by.
    for azimuth, (de, dn) in [(90.0, (100.0, 0.0)), (0.0, (0.0, 100.0))]:
        end_lon, end_lat, _ = geod.fwd(lon, lat, azimuth, 100.0)
        e, n = (float(v) for v in frame.to_enu(end_lat, end_lon))
        assert math.hypot(e, n) == pytest.approx(100.0, abs=1e-4)
        assert (e, n) == pytest.approx((de, dn), abs=5e-3)


@pytest.mark.parametrize(("lat", "lon"), ORIGINS)
def test_the_two_radii_differ_and_neither_is_the_semi_major_axis(
    lat: float, lon: float
) -> None:
    """The failure this replaced was using `a` for both axes.

    At the equator the north scale is the smallest radius on the ellipsoid and
    the east scale is `a` exactly; everywhere else both differ from `a`, and the
    gap between them is what no rotation-plus-scale can undo.
    """
    frame = FlatEnu.at(lat, lon)
    assert frame.m_per_rad_north < frame.m_per_rad_east / max(
        math.cos(math.radians(lat)), 1e-12
    )
    if lat != 0.0:
        assert frame.m_per_rad_north != pytest.approx(WGS84_A_M, rel=1e-6)


@pytest.mark.parametrize(("lat", "lon"), ORIGINS)
def test_round_trip_closes(lat: float, lon: float) -> None:
    frame = FlatEnu.at(lat, lon)
    e = np.array([0.0, 560.0, -430.0, 12.5])
    n = np.array([0.0, -880.0, 640.0, -3.25])
    back_lon, back_lat = frame.to_lonlat(e, n)
    got_e, got_n = frame.to_enu(back_lat, back_lon)
    assert got_e == pytest.approx(e, abs=1e-9)
    assert got_n == pytest.approx(n, abs=1e-9)


def test_origin_projects_to_zero() -> None:
    frame = FlatEnu.at(43.736871113, 7.423323737)
    e, n = frame.to_enu(43.736871113, 7.423323737)
    assert (float(e), float(n)) == (0.0, 0.0)


def test_heading_ratio_is_unity_between_identical_frames() -> None:
    """A frame crossed into itself must not rotate headings."""
    frame = FlatEnu.at(43.7, 7.4)
    assert frame.heading_from(frame.m_per_rad_east, frame.m_per_rad_north) == 1.0


def test_heading_ratio_from_a_spherical_frame_is_the_curvature_ratio() -> None:
    """The correction `import_lap` picks up when the solver shares its latitude.

    fastest-lap's frame is spherical — one radius on both axes — so crossing out
    of it at a matched reference latitude leaves exactly `N/M`, the ratio the old
    `cos(lat0)/cos(reference_lat)` form silently assumed away.
    """
    lat = 43.736871113
    frame = FlatEnu.at(lat, 0.0)
    solver_radius = 6_378_388.0  # International 1924, as fastest-lap uses
    k = frame.heading_from(
        solver_radius * math.cos(math.radians(lat)), solver_radius
    )
    assert k == pytest.approx(
        frame.m_per_rad_east / (frame.m_per_rad_north * math.cos(math.radians(lat)))
    )
    assert k == pytest.approx(1.0035, abs=1e-4)
