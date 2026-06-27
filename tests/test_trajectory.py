"""Trajectory load + sample + schema-rejection tests.

Locks the CSV contract before phase-2 streaming so the renderer can plug in
new data without code changes. Tests exercise documented behavior (knot
match, mid-knot interpolation, endpoint clamp, schema errors) rather than
internal data layout.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from twinkly_mockup.trajectory import (
    REQUIRED_COLUMNS,
    Trajectory,
    TrajectorySchemaError,
)


def _write_csv(path: Path, header: list[str], rows: list[list[float | str]]) -> Path:
    lines = [",".join(header)]
    for row in rows:
        lines.append(",".join(str(v) for v in row))
    path.write_text("\n".join(lines) + "\n")
    return path


def _valid_csv(tmp_path: Path, *, n: int = 5, dt: float = 0.01) -> Path:
    rows = [
        [i * dt, float(i), 2.0 * i, 0.1 * i]
        for i in range(n)
    ]
    return _write_csv(tmp_path / "traj.csv", list(REQUIRED_COLUMNS), rows)


def test_load_returns_trajectory_with_expected_metadata(tmp_path: Path) -> None:
    traj = Trajectory.load(_valid_csv(tmp_path, n=5, dt=0.01))
    assert traj.dt == pytest.approx(0.01)
    assert traj.duration == pytest.approx(0.04)
    assert tuple(traj.t) == pytest.approx((0.0, 0.01, 0.02, 0.03, 0.04))


def test_sample_at_knot_matches_row_exactly(tmp_path: Path) -> None:
    traj = Trajectory.load(_valid_csv(tmp_path, n=5, dt=0.01))
    for i, t_knot in enumerate(traj.t):
        x, y, yaw = traj.sample_at(float(t_knot))
        assert (x, y, yaw) == pytest.approx((float(i), 2.0 * i, 0.1 * i))


def test_sample_at_midpoint_linear_interpolates(tmp_path: Path) -> None:
    traj = Trajectory.load(_valid_csv(tmp_path, n=5, dt=0.01))
    # midway between knot 1 (t=0.01, x=1, y=2, yaw=0.1) and knot 2 (t=0.02, x=2, y=4, yaw=0.2)
    x, y, yaw = traj.sample_at(0.015)
    assert (x, y, yaw) == pytest.approx((1.5, 3.0, 0.15))


def test_sample_at_before_first_knot_clamps_to_first(tmp_path: Path) -> None:
    traj = Trajectory.load(_valid_csv(tmp_path, n=5, dt=0.01))
    first = traj.sample_at(float(traj.t[0]))
    assert traj.sample_at(-100.0) == pytest.approx(first)


def test_sample_at_after_last_knot_clamps_to_last(tmp_path: Path) -> None:
    traj = Trajectory.load(_valid_csv(tmp_path, n=5, dt=0.01))
    last = traj.sample_at(float(traj.t[-1]))
    assert traj.sample_at(1_000.0) == pytest.approx(last)


def test_extra_columns_are_ignored(tmp_path: Path) -> None:
    """Phase-2 producers may emit vx/vy/omega; the renderer must accept and ignore."""
    path = tmp_path / "extra.csv"
    _write_csv(
        path,
        ["t", "x", "y", "yaw", "vx", "vy", "omega"],
        [
            [i * 0.01, float(i), 2.0 * i, 0.1 * i, 99.0, 88.0, 77.0]
            for i in range(4)
        ],
    )
    traj = Trajectory.load(path)
    assert traj.sample_at(0.01) == pytest.approx((1.0, 2.0, 0.1))


def test_columns_in_arbitrary_order(tmp_path: Path) -> None:
    """Schema is by name, not position."""
    path = tmp_path / "reorder.csv"
    _write_csv(
        path,
        ["yaw", "y", "x", "t"],
        [[0.1 * i, 2.0 * i, float(i), i * 0.01] for i in range(4)],
    )
    traj = Trajectory.load(path)
    assert traj.sample_at(0.02) == pytest.approx((2.0, 4.0, 0.2))


def test_non_monotone_t_rejected(tmp_path: Path) -> None:
    path = tmp_path / "non_monotone.csv"
    _write_csv(
        path,
        list(REQUIRED_COLUMNS),
        [
            [0.00, 0.0, 0.0, 0.0],
            [0.01, 1.0, 0.0, 0.0],
            [0.005, 2.0, 0.0, 0.0],  # t goes backwards
            [0.02, 3.0, 0.0, 0.0],
        ],
    )
    with pytest.raises(TrajectorySchemaError) as exc:
        Trajectory.load(path)
    assert "non-monotone t" in str(exc.value)


def test_non_uniform_dt_rejected(tmp_path: Path) -> None:
    path = tmp_path / "non_uniform.csv"
    _write_csv(
        path,
        list(REQUIRED_COLUMNS),
        [
            [0.00, 0.0, 0.0, 0.0],
            [0.01, 1.0, 0.0, 0.0],
            [0.03, 2.0, 0.0, 0.0],  # gap of 0.02 instead of 0.01
            [0.04, 3.0, 0.0, 0.0],
        ],
    )
    with pytest.raises(TrajectorySchemaError) as exc:
        Trajectory.load(path)
    assert "non-uniform dt" in str(exc.value)


def test_missing_column_rejected(tmp_path: Path) -> None:
    path = tmp_path / "missing.csv"
    _write_csv(
        path,
        ["t", "x", "y"],  # yaw missing
        [[i * 0.01, float(i), 2.0 * i] for i in range(4)],
    )
    with pytest.raises(TrajectorySchemaError) as exc:
        Trajectory.load(path)
    msg = str(exc.value)
    assert "missing required column" in msg
    assert "yaw" in msg


def test_yaw_out_of_range_rejected(tmp_path: Path) -> None:
    path = tmp_path / "yaw_oob.csv"
    _write_csv(
        path,
        list(REQUIRED_COLUMNS),
        [
            [0.00, 0.0, 0.0, 0.0],
            [0.01, 1.0, 0.0, 7.0],  # > 2π
            [0.02, 2.0, 0.0, 0.0],
            [0.03, 3.0, 0.0, 0.0],
        ],
    )
    with pytest.raises(TrajectorySchemaError) as exc:
        Trajectory.load(path)
    assert "yaw out of range" in str(exc.value)


def test_yaw_span_over_one_revolution_rejected(tmp_path: Path) -> None:
    """span(yaw) > 2π means the producer didn't unwind — sample_at would
    wrap-around incorrectly during interpolation."""
    path = tmp_path / "yaw_wrap.csv"
    _write_csv(
        path,
        list(REQUIRED_COLUMNS),
        [
            [0.00, 0.0, 0.0, -math.pi],
            [0.01, 1.0, 0.0, 0.0],
            [0.02, 2.0, 0.0, math.pi],
            [0.03, 3.0, 0.0, math.pi + 0.5],  # span = 2π + 0.5
        ],
    )
    with pytest.raises(TrajectorySchemaError) as exc:
        Trajectory.load(path)
    assert "more than one revolution" in str(exc.value)


def test_full_single_revolution_accepted(tmp_path: Path) -> None:
    """A trajectory that exactly covers one revolution is the boundary case."""
    n = 8
    rows = [
        [i * 0.01, float(i), 0.0, -math.pi + (2.0 * math.pi * i / (n - 1))]
        for i in range(n)
    ]
    path = _write_csv(tmp_path / "full_rev.csv", list(REQUIRED_COLUMNS), rows)
    traj = Trajectory.load(path)
    assert traj.sample_at(0.0)[2] == pytest.approx(-math.pi)
    assert traj.sample_at(0.07)[2] == pytest.approx(math.pi)


def test_empty_csv_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.write_text("")
    with pytest.raises(TrajectorySchemaError) as exc:
        Trajectory.load(path)
    assert "empty" in str(exc.value).lower()


def test_single_row_rejected(tmp_path: Path) -> None:
    path = _write_csv(
        tmp_path / "one.csv",
        list(REQUIRED_COLUMNS),
        [[0.0, 1.0, 2.0, 0.0]],
    )
    with pytest.raises(TrajectorySchemaError) as exc:
        Trajectory.load(path)
    assert "at least 2 rows" in str(exc.value)


def test_smoke_load_then_sample_without_render_pipeline(tmp_path: Path) -> None:
    """Acceptance: importable from REPL, load → sample_at works standalone."""
    from twinkly_mockup.trajectory import Trajectory as TopLevel  # noqa: F401

    traj = Trajectory.load(_valid_csv(tmp_path, n=10, dt=0.01))
    sample = traj.sample_at(0.045)
    assert len(sample) == 3
    assert all(isinstance(v, float) for v in sample)
