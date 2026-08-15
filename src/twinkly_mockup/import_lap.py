"""import-lap: fastest-lap native lap CSV → renderer trajectory CSV.

Bridges the `fastest-lap` solver's raw output into the mockup's trajectory
schema (`trajectory.py`). All interpretation happens host-side (ADR-0002); the
solver only emits files. Two transforms live here:

1. **Frame crossing via lat/lon.** `fastest-lap` emits positions in *its own*
   local-Cartesian frame, anchored at *its* origin with *its* earth radius. The
   `<GPS_parameters>` block of the circuit XML records the exact mapping::

       x =  earth_radius · cos(reference_latitude) · (longitude − origin_longitude)
       y = −earth_radius · (latitude  − origin_latitude)

   (angle differences in radians — note the solver's frame is *spherical*, one
   radius for both axes). We invert it to lat/lon, then re-project into the
   mockup ENU frame around the mosaic origin through the shared `FlatEnu`, the
   same projection `centerline.py` uses. Routing through lat/lon absorbs the
   differing origins, radii and axis scales.

   **The solver frame is `x`-east / `y`-south / `z`-down, not north-up ENU.**
   Both signs are written verbatim into every circuit XML's `<GPS_parameters>`
   comment, and come from `circuit_preprocessor.hpp:274`, which builds the
   measured boundaries as `-(latitude − roll0)·R_earth` with `-altitude`. So
   crossing into the mockup's north-up ENU flips north *and* the sense of yaw:
   a heading CCW about `z`-down is CW seen from above.

   An earlier revision of this module asserted the opposite ("fastest-lap is
   north-up ENU, no axis flip needed") and mirrored every lap about the origin
   latitude — ~1.7 km at Silverstone, right off the mosaic. It survived because
   the tests forward-projected their fixtures with the same wrong sign, so the
   round trip closed on itself. The tests below now pin the sign against the
   documented formula, not against this module's inverse of it.

2. **Resample to uniform dt.** The solver's mesh is arc-length based, so its
   time samples are non-uniform; the trajectory schema requires uniform `dt`. We
   resample onto a uniform grid — linear for `x`/`y`, unit-vector (`cos`/`sin`
   then `atan2`) for `yaw` so it stays wrap-safe (ADR-0001) and emits in
   `[−π, π]`.

The result is written as a `t, x, y, yaw` CSV and validated by loading it back
through `Trajectory` before returning.
"""

from __future__ import annotations

import csv
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from .enu import FlatEnu
from .mosaic import MosaicSidecar
from .trajectory import REQUIRED_COLUMNS, Trajectory

DEFAULT_DT_S: float = 0.02  # 50 Hz playback grid
# Native solver CSV: `time` distinguishes it from the output schema's `t`, but
# accept `t` too so a hand-made fixture isn't rejected on a cosmetic mismatch.
NATIVE_TIME_ALIASES: tuple[str, ...] = ("time", "t")
NATIVE_SPATIAL_COLUMNS: tuple[str, ...] = ("x", "y", "yaw")


class ImportLapError(ValueError):
    """Raised when the native solver artifacts can't be bridged."""


@dataclass(frozen=True)
class GpsParameters:
    """The equirectangular anchor recorded in a circuit XML's GPS_parameters."""

    origin_lat: float
    origin_lon: float
    earth_radius: float
    reference_lat: float


def parse_gps_parameters(circuit_xml_path: Path) -> GpsParameters:
    """Read the `<GPS_parameters>` block from a fastest-lap circuit XML."""
    circuit_xml_path = Path(circuit_xml_path)
    try:
        root = ET.parse(circuit_xml_path).getroot()
    except (ET.ParseError, OSError) as e:
        raise ImportLapError(
            f"could not parse circuit XML at {circuit_xml_path}: {e}"
        ) from e

    gps = root.find("GPS_parameters")
    if gps is None:
        raise ImportLapError(
            f"circuit XML at {circuit_xml_path} has no <GPS_parameters> block; "
            f"is it a fastest-lap circuit_preprocessor output?"
        )

    def _field(tag: str) -> float:
        el = gps.find(tag)
        if el is None or el.text is None:
            raise ImportLapError(
                f"circuit XML at {circuit_xml_path} is missing "
                f"<{tag}> inside <GPS_parameters>"
            )
        try:
            return float(el.text)
        except ValueError as e:
            raise ImportLapError(
                f"circuit XML at {circuit_xml_path} has non-numeric <{tag}>: "
                f"{el.text!r}"
            ) from e

    return GpsParameters(
        origin_lat=_field("origin_latitude"),
        origin_lon=_field("origin_longitude"),
        earth_radius=_field("earth_radius"),
        reference_lat=_field("reference_latitude"),
    )


def load_native_lap(
    native_csv_path: Path,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Load `time, x, y, yaw` columns from a native solver CSV.

    Extra columns (e.g. velocities) are accepted and ignored. Returns arrays in
    the solver's native frame; `time` must be strictly increasing.
    """
    native_csv_path = Path(native_csv_path)
    with native_csv_path.open("r", newline="") as f:
        reader = csv.reader(f)
        try:
            header = [name.strip() for name in next(reader)]
        except StopIteration as e:
            raise ImportLapError(f"native lap CSV at {native_csv_path} is empty") from e
        rows = [row for row in reader if row]

    time_col = next((c for c in NATIVE_TIME_ALIASES if c in header), None)
    if time_col is None:
        raise ImportLapError(
            f"native lap CSV at {native_csv_path} is missing a time column "
            f"(one of {', '.join(NATIVE_TIME_ALIASES)}); found: {', '.join(header)}"
        )
    missing = [c for c in NATIVE_SPATIAL_COLUMNS if c not in header]
    if missing:
        raise ImportLapError(
            f"native lap CSV at {native_csv_path} is missing required column(s): "
            f"{', '.join(missing)} (found: {', '.join(header)})"
        )

    wanted = (time_col, *NATIVE_SPATIAL_COLUMNS)
    idx = {name: header.index(name) for name in wanted}
    try:
        data = np.array(
            [[float(row[idx[c]]) for c in wanted] for row in rows],
            dtype=np.float64,
        )
    except (ValueError, IndexError) as e:
        raise ImportLapError(
            f"native lap CSV at {native_csv_path} has a malformed numeric row: {e}"
        ) from e

    if data.shape[0] < 2:
        raise ImportLapError(
            f"native lap CSV at {native_csv_path} must have at least 2 rows, "
            f"got {data.shape[0]}"
        )

    time, x, y, yaw = data[:, 0], data[:, 1], data[:, 2], data[:, 3]
    if np.any(np.diff(time) <= 0.0):
        bad = int(np.argmax(np.diff(time) <= 0.0))
        raise ImportLapError(
            f"native lap CSV at {native_csv_path} has non-increasing time: "
            f"time[{bad}]={time[bad]} >= time[{bad + 1}]={time[bad + 1]}"
        )
    return time, x, y, yaw


def native_to_mockup_enu(
    x: np.ndarray,
    y: np.ndarray,
    gps: GpsParameters,
    origin_lat: float,
    origin_lon: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Cross solver-frame positions into mockup ENU meters via lat/lon."""
    # 1. Invert the solver's equirectangular mapping to lat/lon (degrees).
    #    `y` points *south* (y = -R·Δlat), so the inverse subtracts.
    lat = gps.origin_lat - np.degrees(y / gps.earth_radius)
    lon = gps.origin_lon + np.degrees(
        x / (gps.earth_radius * math.cos(math.radians(gps.reference_lat)))
    )
    # 2. Re-project into the mockup ENU frame (identical to centerline.py).
    return FlatEnu.at(origin_lat, origin_lon).to_enu(lat, lon)


def native_yaw_to_mockup(
    yaw: np.ndarray, gps: GpsParameters, origin_lat: float
) -> np.ndarray:
    """Map solver heading into the mockup frame.

    Two corrections compose here.

    **Handedness.** The solver's `y` points south and its `z` points down, so a
    yaw measured CCW in that frame is CW seen from above in the mockup's north-up
    ENU: the sense flips. Checked against the Silverstone lap by comparing each
    yaw with the heading implied by the path's own finite differences — the flip
    lands within 0.88° on average (that residual is the car's sideslip angle),
    while leaving it out is off by 97°.

    **Axis ratio.** Both frames are flat tangent planes over the same lat/lon, so
    a heading additionally maps by the ratio of their per-axis scale factors. The
    old form was
    ``cos(origin_lat) / cos(reference_lat)`` — correct only while both frames
    were spherical with the same radius, which made the two *north* scales
    cancel. The mockup's north scale is now the meridional radius `M(φ0)` and
    the solver's is still its own `earth_radius`, so they no longer do; the
    general ratio is computed by `FlatEnu.heading_from`.

    The correction is small (`M/N ≈ 1.0035` at Monaco) but it is the same shape
    as the position error #23 removes, and leaving it would put the heading back
    on the model the positions just left.
    """
    solver_east_per_rad = gps.earth_radius * math.cos(math.radians(gps.reference_lat))
    k = FlatEnu.at(origin_lat, 0.0).heading_from(solver_east_per_rad, gps.earth_radius)
    # Negating sin alone is the y-south flip: cos(-yaw) == cos(yaw).
    return np.arctan2(-np.sin(yaw), k * np.cos(yaw))


def _resample_uniform(
    time: np.ndarray,
    x_m: np.ndarray,
    y_m: np.ndarray,
    yaw_m: np.ndarray,
    dt: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Resample onto a uniform grid rebased to t=0; yaw via unit-vector interp."""
    span = float(time[-1] - time[0])
    n = int(math.floor(span / dt)) + 1
    if n < 2:
        raise ImportLapError(
            f"lap duration {span:.6g}s is shorter than one dt={dt:.6g}s; "
            f"nothing to resample"
        )
    grid = dt * np.arange(n)  # rebased to 0; absolute solver t0 is arbitrary
    src_t = time - time[0]
    x_u = np.interp(grid, src_t, x_m)
    y_u = np.interp(grid, src_t, y_m)
    cos_u = np.interp(grid, src_t, np.cos(yaw_m))
    sin_u = np.interp(grid, src_t, np.sin(yaw_m))
    yaw_u = np.arctan2(sin_u, cos_u)
    return grid, x_u, y_u, yaw_u


def _write_trajectory_csv(
    out_csv_path: Path,
    t: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    yaw: np.ndarray,
) -> None:
    out_csv_path = Path(out_csv_path)
    out_csv_path.parent.mkdir(parents=True, exist_ok=True)
    with out_csv_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(REQUIRED_COLUMNS)
        for row in zip(t, x, y, yaw, strict=True):
            writer.writerow(f"{v:.9g}" for v in row)


def import_lap(
    native_csv_path: Path,
    circuit_xml_path: Path,
    mosaic_sidecar_path: Path,
    out_csv_path: Path,
    *,
    dt: float = DEFAULT_DT_S,
) -> Path:
    """Convert a native fastest-lap CSV into a renderer trajectory CSV.

    Crosses the solver frame into mockup ENU via lat/lon (using the circuit
    XML's GPS_parameters and the mosaic sidecar's origin), resamples to uniform
    `dt`, writes `t, x, y, yaw`, and validates the result by loading it through
    `Trajectory`. Returns the output path.
    """
    if dt <= 0.0:
        raise ImportLapError(f"dt must be positive, got {dt}")

    gps = parse_gps_parameters(circuit_xml_path)
    sidecar = _load_sidecar(mosaic_sidecar_path)
    time, x, y, yaw = load_native_lap(native_csv_path)

    x_m, y_m = native_to_mockup_enu(
        x, y, gps, sidecar.origin_lat, sidecar.origin_lon
    )
    yaw_m = native_yaw_to_mockup(yaw, gps, sidecar.origin_lat)
    grid, x_u, y_u, yaw_u = _resample_uniform(time, x_m, y_m, yaw_m, dt)

    _write_trajectory_csv(out_csv_path, grid, x_u, y_u, yaw_u)
    Trajectory.load(out_csv_path)  # fail loudly if we produced an invalid CSV
    return Path(out_csv_path)


def _load_sidecar(mosaic_sidecar_path: Path) -> MosaicSidecar:
    mosaic_sidecar_path = Path(mosaic_sidecar_path)
    with mosaic_sidecar_path.open("r") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ImportLapError(
            f"mosaic sidecar at {mosaic_sidecar_path} must be a YAML mapping, "
            f"got {type(raw).__name__}"
        )
    return MosaicSidecar.model_validate(raw)
