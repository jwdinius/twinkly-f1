"""Centerline: GeoJSON track polyline → ENU samples with local tangent.

A circuit centerline is loaded from a GeoJSON `LineString` (e.g. the
bacinger/f1-circuits files). Lat/lon vertices are projected to the project's
local ENU frame (`+x` east, `+y` north, meters) around a chosen origin
lat/lon — usually the mosaic origin so the centerline shares the mosaic
coordinate frame.

The projection is a tangent-plane (equirectangular) approximation:

    e = (lon - lon0) * cos(lat0) * R
    n = (lat - lat0) * R

This is accurate to <0.5 m over a 1 km radius at Monaco's latitude — well
below the ~0.65 m / LED resolution of the wall display. The mosaic was
reprojected to UTM Zone 32N, which has its own scale distortion at this
longitude (~+0.08%); the two errors are of the same order and both fall
inside one LED. If a future circuit needs sub-decimeter ENU/UTM alignment,
swap in `pyproj` here.

The primary consumer is the snapshot-config authoring step: given a
human-picked corner lat/lon, `snap_to_centerline` returns the nearest
on-centerline (x, y, yaw) for direct use as `snapshot.x_m / .y_m / .yaw_rad`.
The same module is reusable from any future trajectory generator that needs
to sample the racing line at fixed arc-length.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

EARTH_RADIUS_M: float = 6_378_137.0  # WGS84 semi-major axis


@dataclass(frozen=True)
class Pose:
    """ENU pose: position in meters + heading in radians CCW from +x."""

    x_m: float
    y_m: float
    yaw_rad: float


class Centerline:
    """Polyline centerline in ENU meters.

    Vertices are projected from lat/lon at load time. `tangent_at(i)` is the
    central-difference unit direction at vertex `i`; for closed circuits the
    first and last vertices are adjacent on the lap, so the seam uses the
    wrap-around neighbour rather than the one-sided difference (which would
    point along the seam chord, not the tangent).
    """

    def __init__(
        self,
        x: np.ndarray,
        y: np.ndarray,
        *,
        closed: bool,
    ) -> None:
        if x.shape != y.shape or x.ndim != 1 or x.size < 2:
            raise ValueError(
                f"centerline needs ≥2 matched 1-D x/y arrays, got x={x.shape} y={y.shape}"
            )
        self._x = x
        self._y = y
        self._closed = closed

    @classmethod
    def load_geojson(
        cls,
        geojson_path: Path,
        *,
        origin_lat: float,
        origin_lon: float,
    ) -> "Centerline":
        """Load a single `LineString` feature and project to ENU around the origin."""
        geojson_path = Path(geojson_path)
        with geojson_path.open("r") as f:
            raw = json.load(f)

        coords = _extract_linestring_coords(raw, geojson_path)
        lons = np.asarray([c[0] for c in coords], dtype=np.float64)
        lats = np.asarray([c[1] for c in coords], dtype=np.float64)

        cos_lat0 = math.cos(math.radians(origin_lat))
        e = np.radians(lons - origin_lon) * cos_lat0 * EARTH_RADIUS_M
        n = np.radians(lats - origin_lat) * EARTH_RADIUS_M

        # A polyline is "closed" if its first and last vertex coincide
        # (F1 GeoJSONs canonically duplicate vertex 0 at the seam). Drop the
        # duplicate so vertex 0's modulo-wrapped `prev` is the true previous
        # vertex, not a copy of itself.
        closed = bool(math.hypot(e[0] - e[-1], n[0] - n[-1]) < 1.0)
        if closed:
            e = e[:-1]
            n = n[:-1]
        return cls(e, n, closed=closed)

    @property
    def x(self) -> np.ndarray:
        return self._x

    @property
    def y(self) -> np.ndarray:
        return self._y

    @property
    def closed(self) -> bool:
        return self._closed

    def tangent_at(self, idx: int) -> float:
        """Return the local tangent angle (rad, CCW from +x) at vertex `idx`."""
        n = self._x.size
        if not 0 <= idx < n:
            raise IndexError(f"vertex index {idx} out of range [0, {n})")

        if self._closed:
            prev_i = (idx - 1) % n
            next_i = (idx + 1) % n
        else:
            prev_i = max(idx - 1, 0)
            next_i = min(idx + 1, n - 1)

        dx = self._x[next_i] - self._x[prev_i]
        dy = self._y[next_i] - self._y[prev_i]
        return math.atan2(dy, dx)

    def snap(self, x_m: float, y_m: float) -> Pose:
        """Snap a query ENU point to the nearest centerline vertex.

        Returns the vertex's `(x, y)` and its local tangent — exactly what a
        snapshot YAML needs for `x_m / y_m / yaw_rad`.
        """
        dx = self._x - x_m
        dy = self._y - y_m
        idx = int(np.argmin(dx * dx + dy * dy))
        return Pose(
            x_m=float(self._x[idx]),
            y_m=float(self._y[idx]),
            yaw_rad=self.tangent_at(idx),
        )

    def snap_latlon(
        self,
        lat: float,
        lon: float,
        *,
        origin_lat: float,
        origin_lon: float,
    ) -> Pose:
        """Snap a query lat/lon (using the same origin as load) to the nearest vertex."""
        cos_lat0 = math.cos(math.radians(origin_lat))
        x_m = math.radians(lon - origin_lon) * cos_lat0 * EARTH_RADIUS_M
        y_m = math.radians(lat - origin_lat) * EARTH_RADIUS_M
        return self.snap(x_m, y_m)


def _extract_linestring_coords(raw: dict, path: Path) -> list[tuple[float, float]]:
    """Pull `[lon, lat]` pairs from a GeoJSON `FeatureCollection` or `Feature`."""
    if raw.get("type") == "FeatureCollection":
        features = raw.get("features") or []
        line_features = [f for f in features if (f.get("geometry") or {}).get("type") == "LineString"]
        if len(line_features) != 1:
            raise ValueError(
                f"GeoJSON at {path} must contain exactly one LineString feature, "
                f"got {len(line_features)}"
            )
        coords = line_features[0]["geometry"]["coordinates"]
    elif raw.get("type") == "Feature" and (raw.get("geometry") or {}).get("type") == "LineString":
        coords = raw["geometry"]["coordinates"]
    elif raw.get("type") == "LineString":
        coords = raw["coordinates"]
    else:
        raise ValueError(
            f"GeoJSON at {path} is not a LineString / Feature(LineString) / "
            f"FeatureCollection-of-one-LineString"
        )

    if len(coords) < 2:
        raise ValueError(f"LineString at {path} has fewer than 2 vertices")
    return [(float(lon), float(lat)) for lon, lat, *_ in coords]
