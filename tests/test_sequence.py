"""Lap sequence: frame timing, camera-to-car alignment, and the rendered movie."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw
from typer.testing import CliRunner

from twinkly_mockup.cli import app
from twinkly_mockup.config import CarSpec, load_config
from twinkly_mockup.mosaic import Mosaic
from twinkly_mockup.sequence import camera_yaw_for_heading, frame_times, render_lap
from twinkly_mockup.trajectory import Trajectory

MARK_M = 20.0  # how far ahead of the car the fixture mark sits


def _write_marked_mosaic(dir_: Path, mark_enu: tuple[float, float]) -> Path:
    """200×200 m mosaic, grey but for a red mark at `mark_enu` metres.

    `m_per_px` is 1, the origin pixel is the image centre, and `north_angle_deg`
    is 0 — so ENU `+x` is image-right and `+y` is image-up, straight off the
    sidecar contract, with no dependence on the renderer's own projection.
    """
    png = dir_ / "marked.png"
    img = Image.new("RGB", (200, 200), (128, 128, 128))
    e, n = mark_enu
    cx, cy = 100.0 + e, 100.0 - n  # ENU→px: +x right, +y up
    ImageDraw.Draw(img).ellipse((cx - 6, cy - 6, cx + 6, cy + 6), fill=(230, 30, 30))
    img.save(png)

    sidecar = dir_ / "marked_mosaic.yaml"
    sidecar.write_text(
        "path: marked.png\n"
        "origin_lat: 0.0\n"
        "origin_lon: 0.0\n"
        "origin_px: [100.0, 100.0]\n"
        "m_per_px: 1.0\n"
        "north_angle_deg: 0.0\n"
    )
    return sidecar


def _write_trajectory(path: Path, headings: list[float], dt: float = 0.02) -> Path:
    """Trajectory CSV that walks 1 m per sample along each heading in turn."""
    rows = ["t,x,y,yaw"]
    x = y = 0.0
    for i, yaw in enumerate(headings):
        rows.append(f"{i * dt:.6f},{x:.6f},{y:.6f},{yaw:.9f}")
        x += math.cos(yaw)
        y += math.sin(yaw)
    path.write_text("\n".join(rows) + "\n")
    return path


def _write_config(dir_: Path, sidecar_name: str) -> Path:
    cfg = dir_ / "sim.yaml"
    cfg.write_text(
        "layout:\n"
        "  outer_tiles_w: 3\n"
        "  outer_tiles_h: 3\n"
        "  cutout_tiles_w: 1\n"
        "  cutout_tiles_h: 1\n"
        "  cutout_offset_x: 1\n"
        "  cutout_offset_y: 1\n"
        "render:\n"
        "  scale_px_per_led: 10\n"
        "snapshot:\n"
        f"  mosaic: {sidecar_name}\n"
        "  x_m: 0.0\n"
        "  y_m: 0.0\n"
        "  viewport_m: [60.0, 60.0]\n"
        "car:\n"
        "  dimensions_cm: [8.0, 4.0]\n"
        "  orientation_deg: -90.0\n"
    )
    return cfg


# --- camera / car alignment -------------------------------------------------


def test_camera_yaw_puts_the_nose_on_the_heading_for_the_shipped_mounting() -> None:
    """The shipped `orientation_deg = -90` mounting biases the camera by +π/2.

    This is the constant `scripts/derive_corner_poses.py` hardcodes as
    `YAW_BIAS_RAD` for the still-frame snapshots; the sequence derives it.
    """
    car = CarSpec(orientation_deg=-90.0)
    for heading in (0.0, 1.3, -2.7, math.pi):
        assert camera_yaw_for_heading(heading, car) == pytest.approx(heading + math.pi / 2)


def test_camera_yaw_follows_any_mounting_angle() -> None:
    """Nose heading = camera yaw + orientation, so camera yaw = heading − orientation."""
    for orientation in (-90.0, 0.0, 37.0, 90.0):
        car = CarSpec(orientation_deg=orientation)
        camera_yaw = camera_yaw_for_heading(1.0, car)
        nose_heading = camera_yaw + math.radians(orientation)
        assert nose_heading == pytest.approx(1.0)


@pytest.mark.parametrize("heading", [0.0, math.pi / 2, math.pi, -math.pi / 2, 2.2])
def test_the_track_ahead_lands_where_the_nose_points(tmp_path: Path, heading: float) -> None:
    """A mark `MARK_M` ahead of the car renders on the nose side, at any heading.

    The mark is placed in the mosaic from the sidecar's own ENU↔pixel contract
    (see `_write_marked_mosaic`), not from `Mosaic.meters_to_px`, so a sign error
    shared by the projection and its inverse cannot make this pass.

    With `orientation_deg = -90` the nose points image-right, so "ahead" must
    land right of centre and on the horizontal midline.
    """
    ahead = (MARK_M * math.cos(heading), MARK_M * math.sin(heading))
    sidecar = _write_marked_mosaic(tmp_path, ahead)
    mosaic = Mosaic.load(sidecar)
    car = CarSpec(orientation_deg=-90.0)

    crop = mosaic.sample(
        center_xy_m=(0.0, 0.0),
        yaw_rad=camera_yaw_for_heading(heading, car),
        viewport_m=(60.0, 60.0),
        output_px=(120, 120),
    )

    r, g, b = crop[..., 0].astype(int), crop[..., 1].astype(int), crop[..., 2].astype(int)
    red = (r > 150) & (g < 100) & (b < 100)
    assert red.any(), "the mark ahead of the car fell outside the crop"
    rows, cols = np.nonzero(red)
    # 60 m across 120 px = 2 px/m, so 20 m ahead sits 40 px right of centre (60).
    assert cols.mean() == pytest.approx(100.0, abs=3.0)
    assert rows.mean() == pytest.approx(60.0, abs=3.0)


# --- frame timing -----------------------------------------------------------


def test_frame_times_span_the_lap_at_the_requested_rate(tmp_path: Path) -> None:
    traj = Trajectory.load(_write_trajectory(tmp_path / "t.csv", [0.0] * 51))  # 1.0 s
    at_50 = frame_times(traj, fps=50.0)
    assert len(at_50) == 51
    assert at_50[0] == pytest.approx(0.0)
    assert at_50[-1] == pytest.approx(1.0)

    at_25 = frame_times(traj, fps=25.0)
    assert len(at_25) == 26
    assert at_25[-1] == pytest.approx(1.0)


def test_frame_times_rejects_a_nonpositive_rate(tmp_path: Path) -> None:
    traj = Trajectory.load(_write_trajectory(tmp_path / "t.csv", [0.0] * 10))
    with pytest.raises(ValueError, match="fps"):
        frame_times(traj, fps=0.0)


# --- rendering the lap ------------------------------------------------------


def test_render_lap_writes_an_mp4_of_the_whole_lap(tmp_path: Path) -> None:
    _write_marked_mosaic(tmp_path, (MARK_M, 0.0))
    config = load_config(_write_config(tmp_path, "marked_mosaic.yaml"))
    traj = Trajectory.load(
        _write_trajectory(tmp_path / "t.csv", list(np.linspace(0.0, math.pi / 2, 26)))
    )
    out = tmp_path / "lap.mp4"

    result = render_lap(config, traj, fps=50.0, out_path=out)

    assert result.frame_count == 26
    # 26 frames at 50 fps is 0.52 s of video; the lap itself spans 0.50 s,
    # the extra frame being the closing endpoint.
    assert result.duration_s == pytest.approx(0.52)
    assert out.exists() and out.stat().st_size > 0


def test_render_lap_writes_a_png_sequence_when_out_is_a_directory(tmp_path: Path) -> None:
    _write_marked_mosaic(tmp_path, (MARK_M, 0.0))
    config = load_config(_write_config(tmp_path, "marked_mosaic.yaml"))
    traj = Trajectory.load(_write_trajectory(tmp_path / "t.csv", [0.0] * 11))
    out = tmp_path / "frames"

    result = render_lap(config, traj, fps=50.0, out_path=out)

    pngs = sorted(out.glob("*.png"))
    assert len(pngs) == result.frame_count == 11
    assert pngs[0].name == "frame_00000.png"
    # 3*6*10 + 2*10 = 200 px square, same canvas as the still-frame path.
    with Image.open(pngs[0]) as img:
        assert img.size == (200, 200)


def test_the_world_moves_beneath_a_stationary_car(tmp_path: Path) -> None:
    """Consecutive frames differ — that difference *is* the track moving."""
    _write_marked_mosaic(tmp_path, (MARK_M, 0.0))
    config = load_config(_write_config(tmp_path, "marked_mosaic.yaml"))
    traj = Trajectory.load(_write_trajectory(tmp_path / "t.csv", [0.0] * 11))
    out = tmp_path / "frames"

    render_lap(config, traj, fps=50.0, out_path=out)

    frames = [np.array(Image.open(p).convert("RGB")) for p in sorted(out.glob("*.png"))]
    assert not np.array_equal(frames[0], frames[-1]), "the track never moved"


def test_render_lap_cli_reports_what_it_wrote(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_marked_mosaic(tmp_path, (MARK_M, 0.0))
    cfg = _write_config(tmp_path, "marked_mosaic.yaml")
    csv = _write_trajectory(tmp_path / "t.csv", [0.0] * 11)
    out = tmp_path / "lap.mp4"

    result = CliRunner().invoke(
        app,
        ["render-lap", str(cfg), "--trajectory", str(csv), "--out", str(out), "--fps", "50"],
    )

    assert result.exit_code == 0, result.output
    assert out.exists()
    assert "11 frames" in result.output
