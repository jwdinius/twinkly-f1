"""Mosaic projection + sampling tests.

Catches the silent-corruption class of bugs (wrong sign, wrong scale, y-axis
flip, yaw-sign) that look fine to the eye until they don't.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from twinkly_mockup.mosaic import CLIP_VALUE, Mosaic, MosaicSidecar

GREY_BG = (200, 200, 200)
RED = (220, 60, 60)


def _write_synthetic_mosaic(
    tmp_path: Path,
    *,
    png_size: tuple[int, int] = (100, 100),
    origin_px: tuple[float, float] = (50.0, 50.0),
    m_per_px: float = 1.0,
    north_angle_deg: float = 0.0,
    landmark_px_rect: tuple[int, int, int, int] | None = (70, 40, 90, 60),
    sidecar_name: str = "mosaic.yaml",
    png_name: str = "mosaic.png",
) -> Path:
    """Write a synthetic mosaic + sidecar to `tmp_path`. Returns the sidecar path.

    `landmark_px_rect` is a (left, top, right, bottom) of a red block placed on
    the grey background — handy for assertions about *where* the sample lands.
    """
    png_path = tmp_path / png_name
    img = Image.new("RGB", png_size, GREY_BG)
    if landmark_px_rect is not None:
        left, top, right, bottom = landmark_px_rect
        pixels = img.load()
        for x in range(left, right):
            for y in range(top, bottom):
                pixels[x, y] = RED
    img.save(png_path)

    sidecar_path = tmp_path / sidecar_name
    sidecar_path.write_text(
        f"path: {png_name}\n"
        "origin_lat: 0.0\n"
        "origin_lon: 0.0\n"
        f"origin_px: [{origin_px[0]}, {origin_px[1]}]\n"
        f"m_per_px: {m_per_px}\n"
        f"north_angle_deg: {north_angle_deg}\n"
    )
    return sidecar_path


def test_malformed_sidecar_missing_field_raises_validation_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "path: x.png\n"
        "origin_lat: 0.0\n"
        "origin_lon: 0.0\n"
        "origin_px: [0, 0]\n"
        # m_per_px omitted on purpose
    )
    with pytest.raises(ValidationError) as exc:
        Mosaic.load(bad)
    assert "m_per_px" in str(exc.value)


def test_malformed_sidecar_rejects_unknown_key(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "path: x.png\n"
        "origin_lat: 0.0\n"
        "origin_lon: 0.0\n"
        "origin_px: [0, 0]\n"
        "m_per_px: 1.0\n"
        "garbage_key: true\n"
    )
    with pytest.raises(ValidationError) as exc:
        Mosaic.load(bad)
    assert "garbage_key" in str(exc.value)


def test_malformed_sidecar_rejects_nonpositive_m_per_px(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "path: x.png\n"
        "origin_lat: 0.0\n"
        "origin_lon: 0.0\n"
        "origin_px: [0, 0]\n"
        "m_per_px: 0.0\n"
    )
    with pytest.raises(ValidationError):
        Mosaic.load(bad)


@pytest.mark.parametrize("north_angle_deg", [0.0, 10.0, -33.5])
def test_meters_to_px_to_meters_round_trip(tmp_path: Path, north_angle_deg: float) -> None:
    sidecar = _write_synthetic_mosaic(tmp_path, north_angle_deg=north_angle_deg)
    mosaic = Mosaic.load(sidecar)
    for xy_m in [(0.0, 0.0), (10.0, 0.0), (0.0, 25.0), (-7.0, 42.0), (33.0, -19.0)]:
        round_trip = mosaic.px_to_meters(mosaic.meters_to_px(xy_m))
        assert round_trip == pytest.approx(xy_m, abs=1e-9)


@pytest.mark.parametrize("north_angle_deg", [0.0, 10.0, -33.5])
def test_px_to_meters_to_px_round_trip(tmp_path: Path, north_angle_deg: float) -> None:
    sidecar = _write_synthetic_mosaic(tmp_path, north_angle_deg=north_angle_deg)
    mosaic = Mosaic.load(sidecar)
    for px in [(0.0, 0.0), (50.0, 50.0), (12.5, 87.3), (99.0, 1.0), (5.0, 95.0)]:
        round_trip = mosaic.meters_to_px(mosaic.px_to_meters(px))
        assert round_trip == pytest.approx(px, abs=1e-9)


def test_north_up_meters_to_px_matches_convention(tmp_path: Path) -> None:
    """For north_angle_deg=0: +x_ENU → +x_px (east → right); +y_ENU → -y_px (north → up)."""
    sidecar = _write_synthetic_mosaic(tmp_path)  # origin_px=(50, 50), m=1
    mosaic = Mosaic.load(sidecar)
    # 10 m east
    assert mosaic.meters_to_px((10.0, 0.0)) == pytest.approx((60.0, 50.0))
    # 10 m north → image y DECREASES
    assert mosaic.meters_to_px((0.0, 10.0)) == pytest.approx((50.0, 40.0))


def test_sample_yaw_zero_places_landmark_above_center(tmp_path: Path) -> None:
    """yaw=0: heading=east; image-up = east. A landmark east of origin appears
    above the output center (small v)."""
    # Red block at pixel column [70, 90), rows [40, 60). With origin_px=(50, 50)
    # and m=1, the block is centered around ENU (30, 0) — 30 m east of origin.
    sidecar = _write_synthetic_mosaic(tmp_path)
    mosaic = Mosaic.load(sidecar)
    out = mosaic.sample(
        center_xy_m=(0.0, 0.0),
        yaw_rad=0.0,
        viewport_m=(100.0, 100.0),
        output_px=(100, 100),
    )
    # East landmark should appear above center: small v, u ≈ output center.
    # Block spans ENU x ∈ [20, 40], y ∈ [-10, 10] in ENU.
    # With yaw=0: v_m_up = e, u_m = -n. So row 20 (v=20 → v_m_up=29.5) → red.
    assert tuple(out[20, 50]) == RED, f"expected red east-of-center at (50, 20), got {tuple(out[20, 50])}"
    # Below center (large v) is west of origin → background grey.
    assert tuple(out[80, 50]) == GREY_BG


def test_sample_yaw_half_pi_places_landmark_right_of_center(tmp_path: Path) -> None:
    """yaw=π/2: heading=north; image-up = north; image-right = east.

    A landmark east of origin appears to the RIGHT of the output center.
    Catches y-flip bugs (would put landmark below) and yaw-sign bugs (would
    put landmark left).
    """
    sidecar = _write_synthetic_mosaic(tmp_path)
    mosaic = Mosaic.load(sidecar)
    out = mosaic.sample(
        center_xy_m=(0.0, 0.0),
        yaw_rad=math.pi / 2,
        viewport_m=(100.0, 100.0),
        output_px=(100, 100),
    )
    # Landmark should land RIGHT of center: large u, v ≈ output center.
    assert tuple(out[50, 80]) == RED, f"expected red east-of-center (right) at (80, 50), got {tuple(out[50, 80])}"
    # Left of center is opposite (west) → grey background.
    assert tuple(out[50, 20]) == GREY_BG


def test_sample_out_of_mosaic_returns_clip_value(tmp_path: Path) -> None:
    sidecar = _write_synthetic_mosaic(tmp_path)
    mosaic = Mosaic.load(sidecar)
    # Center far outside the 100×100 m mosaic, viewport small enough to stay out.
    out = mosaic.sample(
        center_xy_m=(10_000.0, 10_000.0),
        yaw_rad=0.0,
        viewport_m=(10.0, 10.0),
        output_px=(20, 20),
    )
    assert np.all(out == np.array(CLIP_VALUE, dtype=np.uint8))


def test_sidecar_pydantic_round_trip_default_north_angle() -> None:
    """north_angle_deg has a sensible default of 0.0 (north-up)."""
    sidecar = MosaicSidecar.model_validate(
        {
            "path": "x.png",
            "origin_lat": 0.0,
            "origin_lon": 0.0,
            "origin_px": [0, 0],
            "m_per_px": 1.0,
        }
    )
    assert sidecar.north_angle_deg == 0.0
