"""Flat-ENU: the one tangent-plane projection every lat/lon in this repo crosses.

Local Cartesian metres about an origin — `+e` east, `+n` north — obtained by
scaling angular offsets by the ellipsoid's **radii of curvature at the origin
latitude**::

    e = radians(lon - lon0) · N(φ0) · cos φ0
    n = radians(lat - lat0) · M(φ0)

`N` is the prime-vertical radius (the one that governs east–west arcs) and `M`
the meridional radius (north–south). They differ by about 0.35% at mid
latitudes, and neither equals the semi-major axis `a`: using `a` for both — as
this repo did until #23 — stretches east by ~+0.16% and squeezes north by
~−0.19%.

That matters more than the size suggests. The stretch and the squeeze go in
*opposite* directions, so the error is an **anisotropic** scale: it is neither a
rotation nor a uniform scale, and ADR-0004's rotation-plus-scale affine into
mosaic pixels therefore cannot absorb it. It passed straight through to the
image corners as ~1.4 m — 7 px, about 12% of a track width. With the correct
radii the same corners agree with `pyproj` to under a tenth of a metre, which is
what makes ADR-0004's "sub-pixel" a property of the pipeline and not just of the
pixel math in isolation.

This is still a tangent-plane approximation, not a projection: it is exact at
the origin and degrades with distance. Over the ~1 km circuits here that is well
inside a pixel. It is deliberately *not* the mosaic's UTM grid — the rotation
and scale between the two live in the Sidecar (ADR-0004).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# WGS84 — the datum the mosaics, the bacinger centerlines and fastest-lap's
# GPS_parameters all quote positions against.
WGS84_A_M: float = 6_378_137.0  # semi-major axis
WGS84_INVERSE_FLATTENING: float = 298.257223563
WGS84_E2: float = (2.0 - 1.0 / WGS84_INVERSE_FLATTENING) / WGS84_INVERSE_FLATTENING
"""First eccentricity squared, `e² = f(2 − f)`."""


@dataclass(frozen=True)
class FlatEnu:
    """A flat-ENU frame anchored at one origin.

    `m_per_rad_east` and `m_per_rad_north` are the two scale factors baked in at
    construction — `N(φ0)·cos φ0` and `M(φ0)`. Holding them rather than
    recomputing per point is what keeps the frame *flat*: every point in a lap
    shares the origin's curvature, which is the whole approximation.
    """

    origin_lat: float
    origin_lon: float
    m_per_rad_east: float
    m_per_rad_north: float

    @classmethod
    def at(cls, origin_lat: float, origin_lon: float) -> "FlatEnu":
        """Build the frame tangent to WGS84 at `(origin_lat, origin_lon)`."""
        phi = math.radians(origin_lat)
        w2 = 1.0 - WGS84_E2 * math.sin(phi) ** 2
        prime_vertical = WGS84_A_M / math.sqrt(w2)
        meridional = WGS84_A_M * (1.0 - WGS84_E2) / (w2 * math.sqrt(w2))
        return cls(
            origin_lat=origin_lat,
            origin_lon=origin_lon,
            m_per_rad_east=prime_vertical * math.cos(phi),
            m_per_rad_north=meridional,
        )

    def to_enu(self, lat, lon):
        """Project lat/lon (degrees, scalar or array) to ENU metres."""
        e = np.radians(np.asarray(lon, dtype=np.float64) - self.origin_lon)
        n = np.radians(np.asarray(lat, dtype=np.float64) - self.origin_lat)
        return e * self.m_per_rad_east, n * self.m_per_rad_north

    def to_lonlat(self, e, n):
        """Inverse of `to_enu`, returning `(lon, lat)` in degrees."""
        lon = np.degrees(np.asarray(e, dtype=np.float64) / self.m_per_rad_east)
        lat = np.degrees(np.asarray(n, dtype=np.float64) / self.m_per_rad_north)
        return lon + self.origin_lon, lat + self.origin_lat

    def heading_from(self, other_east_per_rad: float, other_north_per_rad: float) -> float:
        """Scale factor mapping a heading out of another flat frame into this one.

        Two flat frames over the same lat/lon are related by one scale per axis,
        so a direction `(cos ψ, sin ψ)` in the source frame arrives here as
        `(cos ψ · k, sin ψ)` up to an overall factor, with

            k = (this east scale / source east scale)
              ÷ (this north scale / source north scale)

        Apply it as `atan2(sin ψ, k · cos ψ)`. When both frames are spherical
        with the same radius the north scales cancel and `k` collapses to the
        ratio of `cos(latitude)` terms — which is what this replaced.
        """
        return (self.m_per_rad_east / other_east_per_rad) / (
            self.m_per_rad_north / other_north_per_rad
        )
