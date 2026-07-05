"""Trajectory: time-indexed `(x, y, yaw)` lookup loaded from a CSV.

This module locks the CSV contract between this renderer and the future lap
optimizer (`fastest-lap.dev`). It is not wired into the MVP render path — the
MVP composes from a hard-coded snapshot — but the schema is enforced so the
phase-2 streaming work can plug in by swapping data, not code.

Schema:

* Required columns: ``t, x, y, yaw``. Any further columns
  (e.g. ``vx, vy, omega``) are accepted and silently ignored.
* ``t`` is in seconds, strictly increasing, with uniform spacing ``dt``.
* ``x, y`` are in meters in the project's ENU frame (``+x`` east, ``+y``
  north).
* ``yaw`` is in radians CCW from ``+x``. Each value must lie in ``[-2π, 2π]``
  (a loose sanity bound catching e.g. degree-valued input); there is **no**
  span / single-revolution constraint. A full closed lap nets a full 2π and
  backtracks through the hairpin/chicanes, so producers may emit any wrapped
  sequence — ``import-lap`` emits ``[-π, π]`` with no global unwinding. See
  ADR-0001.

Sampling ``x, y`` at non-knot times is linear interpolation between adjacent
knots. ``yaw`` is interpolated as a **unit vector** (interpolate ``cos``/``sin``
then ``atan2``), so it is wrap-safe by construction and takes the short way
across the ±π seam. Times outside the covered range clamp to the first / last
knot.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

import numpy as np

REQUIRED_COLUMNS: tuple[str, ...] = ("t", "x", "y", "yaw")
YAW_ABS_LIMIT: float = 2.0 * math.pi
DT_RELATIVE_TOLERANCE: float = 1e-6


class TrajectorySchemaError(ValueError):
    """Raised when a CSV violates the documented Trajectory schema."""


class Trajectory:
    """Uniform-``dt`` trajectory with linear ``(x, y, yaw)`` sampling."""

    def __init__(
        self,
        t: np.ndarray,
        x: np.ndarray,
        y: np.ndarray,
        yaw: np.ndarray,
        dt: float,
    ) -> None:
        self._t = t
        self._x = x
        self._y = y
        self._yaw = yaw
        self._dt = dt

    @classmethod
    def load(cls, csv_path: Path) -> "Trajectory":
        csv_path = Path(csv_path)
        with csv_path.open("r", newline="") as f:
            reader = csv.reader(f)
            try:
                header = next(reader)
            except StopIteration as e:
                raise TrajectorySchemaError(
                    f"trajectory CSV at {csv_path} is empty"
                ) from e
            rows = [row for row in reader if row]

        header = [name.strip() for name in header]
        missing = [c for c in REQUIRED_COLUMNS if c not in header]
        if missing:
            raise TrajectorySchemaError(
                f"trajectory CSV at {csv_path} is missing required column(s): "
                f"{', '.join(missing)} (found: {', '.join(header)})"
            )

        col_index = {name: header.index(name) for name in REQUIRED_COLUMNS}
        try:
            data = np.array(
                [
                    [float(row[col_index[c]]) for c in REQUIRED_COLUMNS]
                    for row in rows
                ],
                dtype=np.float64,
            )
        except (ValueError, IndexError) as e:
            raise TrajectorySchemaError(
                f"trajectory CSV at {csv_path} has a malformed numeric row: {e}"
            ) from e

        if data.shape[0] < 2:
            raise TrajectorySchemaError(
                f"trajectory CSV at {csv_path} must have at least 2 rows, "
                f"got {data.shape[0]}"
            )

        t, x, y, yaw = data[:, 0], data[:, 1], data[:, 2], data[:, 3]
        dt = _validate_monotone_uniform_t(t, csv_path)
        _validate_yaw_range(yaw, csv_path)
        return cls(t=t, x=x, y=y, yaw=yaw, dt=dt)

    @property
    def t(self) -> np.ndarray:
        return self._t

    @property
    def dt(self) -> float:
        return self._dt

    @property
    def duration(self) -> float:
        return float(self._t[-1] - self._t[0])

    def sample_at(self, t: float) -> tuple[float, float, float]:
        """Return ``(x, y, yaw)`` at time ``t``.

        ``x, y`` linearly interpolate between knots. ``yaw`` interpolates as a
        unit vector (``cos``/``sin`` then ``atan2``), so it is wrap-safe and
        crosses the ±π seam the short way. Clamps to the first / last knot for
        times outside the covered range.
        """
        x = float(np.interp(t, self._t, self._x))
        y = float(np.interp(t, self._t, self._y))
        cos_y = float(np.interp(t, self._t, np.cos(self._yaw)))
        sin_y = float(np.interp(t, self._t, np.sin(self._yaw)))
        yaw = float(math.atan2(sin_y, cos_y))
        return x, y, yaw


def _validate_monotone_uniform_t(t: np.ndarray, csv_path: Path) -> float:
    diffs = np.diff(t)
    if np.any(diffs <= 0.0):
        bad_idx = int(np.argmax(diffs <= 0.0))
        raise TrajectorySchemaError(
            f"trajectory CSV at {csv_path} has non-monotone t: "
            f"t[{bad_idx}]={t[bad_idx]} >= t[{bad_idx + 1}]={t[bad_idx + 1]}"
        )
    dt_mean = float(diffs.mean())
    tol = DT_RELATIVE_TOLERANCE * abs(dt_mean) if dt_mean != 0.0 else DT_RELATIVE_TOLERANCE
    deviations = np.abs(diffs - dt_mean)
    if np.any(deviations > tol):
        bad_idx = int(np.argmax(deviations))
        raise TrajectorySchemaError(
            f"trajectory CSV at {csv_path} has non-uniform dt: "
            f"dt[{bad_idx}]={diffs[bad_idx]} differs from mean {dt_mean} "
            f"by more than tolerance {tol}"
        )
    return dt_mean


def _validate_yaw_range(yaw: np.ndarray, csv_path: Path) -> None:
    # No span / single-revolution constraint: sample_at is wrap-safe (ADR-0001).
    # Only a loose absolute bound to catch e.g. degree-valued input.
    if np.any(np.abs(yaw) > YAW_ABS_LIMIT):
        bad_idx = int(np.argmax(np.abs(yaw) > YAW_ABS_LIMIT))
        raise TrajectorySchemaError(
            f"trajectory CSV at {csv_path} has yaw out of range: "
            f"yaw[{bad_idx}]={yaw[bad_idx]} outside [-2π, 2π]"
        )
