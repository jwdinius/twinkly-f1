"""Integration test: `twinkly-mockup render` end-to-end via the typer runner."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from typer.testing import CliRunner

from twinkly_mockup.cli import app

WALL = (240, 240, 235)
BLACK = (0, 0, 0)


def _write_fixture_mosaic(dir_: Path) -> Path:
    """100×100 quadrant-colored mosaic centered on the origin pixel.
    Returns the sidecar path."""
    png = dir_ / "fixture.png"
    img = Image.new("RGB", (100, 100), (245, 245, 245))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 50, 50), fill=(60, 200, 80))     # NW green
    d.rectangle((50, 0, 100, 50), fill=(220, 60, 60))   # NE red
    d.rectangle((0, 50, 50, 100), fill=(60, 120, 230))  # SW blue
    d.rectangle((50, 50, 100, 100), fill=(230, 200, 60))  # SE yellow
    img.save(png)

    sidecar = dir_ / "fixture_mosaic.yaml"
    sidecar.write_text(
        "path: fixture.png\n"
        "origin_lat: 0.0\n"
        "origin_lon: 0.0\n"
        "origin_px: [50.0, 50.0]\n"
        "m_per_px: 1.0\n"
        "north_angle_deg: 0.0\n"
    )
    return sidecar


def _write_fixture_config(tmp_path: Path, output_relative: str = "out/fixture.png") -> Path:
    _write_fixture_mosaic(tmp_path)
    cfg = tmp_path / "fixture.yaml"
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
        "  wall_color: [240, 240, 235]\n"
        "snapshot:\n"
        "  mosaic: fixture_mosaic.yaml\n"
        "  x_m: 0.0\n"
        "  y_m: 0.0\n"
        "  yaw_rad: 0.0\n"
        "  viewport_m: [80.0, 80.0]\n"
        "car:\n"
        # Tiny car so it fits inside the 1×1-tile cutout (16cm) of the fixture.
        "  dimensions_cm: [8.0, 4.0]\n"
        f"output_path: {output_relative}\n"
    )
    return cfg


def test_render_produces_png_with_real_satellite_content(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    cfg_path = _write_fixture_config(tmp_path)

    runner = CliRunner()
    result = runner.invoke(app, ["render", str(cfg_path)])
    assert result.exit_code == 0, result.output

    png = tmp_path / "out" / "fixture.png"
    assert png.exists()

    with Image.open(png) as img:
        rgb = img.convert("RGB")
        # 3*6*10 + 2*10 = 200 px
        assert rgb.size == (200, 200)
        arr = np.array(rgb)

    # Wall pixels still present from the cutout region.
    assert (arr == np.array(WALL)).all(axis=-1).any()
    # Black substrate / inter-tile gaps still present.
    assert (arr == np.array(BLACK)).all(axis=-1).any()
    # At least three of the four source-quadrant hues come through, proving
    # real satellite content (not placeholder mid-grey, which would have none).
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    hues_present = sum(
        [
            ((r > 150) & (g < 120) & (b < 120)).any(),  # red
            ((g > 150) & (r < 120) & (b < 120)).any(),  # green
            ((b > 150) & (r < 120) & (g < 180)).any(),  # blue
            ((r > 150) & (g > 150) & (b < 120)).any(),  # yellow
        ]
    )
    assert hues_present >= 3, f"expected ≥3 quadrant hues in rendered frame, got {hues_present}"


def test_help_works() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "render" in result.output


def _write_layout_partial(dir_: Path, name: str, outer_w: int, outer_h: int) -> Path:
    """Layout-only sweep YAML with a 1×1 cutout sized to fit outer_w/outer_h."""
    path = dir_ / name
    path.write_text(
        "layout:\n"
        f"  outer_tiles_w: {outer_w}\n"
        f"  outer_tiles_h: {outer_h}\n"
        "  cutout_tiles_w: 1\n"
        "  cutout_tiles_h: 1\n"
        f"  cutout_offset_x: {outer_w // 2}\n"
        f"  cutout_offset_y: {outer_h // 2}\n"
        "render:\n"
        "  scale_px_per_led: 10\n"
    )
    return path


def _write_snapshot_only_config(
    tmp_path: Path, name: str, layout_config: str | None
) -> Path:
    """Snapshot YAML with no inline layout — relies on layout_config or CLI override."""
    cfg = tmp_path / name
    body = ""
    if layout_config is not None:
        body += f"layout_config: {layout_config}\n"
    body += (
        "snapshot:\n"
        "  mosaic: fixture_mosaic.yaml\n"
        "  x_m: 0.0\n"
        "  y_m: 0.0\n"
        "  yaw_rad: 0.0\n"
        "car:\n"
        "  dimensions_cm: [8.0, 4.0]\n"
    )
    cfg.write_text(body)
    return cfg


def _expected_grid_px(outer_w: int, outer_h: int, scale: int = 10) -> tuple[int, int]:
    # Mirrors led.grid_pixel_size: leds + inter-tile gap pixels.
    w = outer_w * 6 * scale + (outer_w - 1) * scale
    h = outer_h * 6 * scale + (outer_h - 1) * scale
    return w, h


def test_render_all_writes_one_png_per_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_fixture_mosaic(tmp_path)
    layout_a = _write_layout_partial(tmp_path, "layout_a.yaml", 3, 3)
    cfg_a = _write_snapshot_only_config(tmp_path, "snap_a.yaml", "layout_a.yaml")
    cfg_b = _write_snapshot_only_config(tmp_path, "snap_b.yaml", "layout_a.yaml")
    out_dir = tmp_path / "out_render_all"

    result = CliRunner().invoke(
        app,
        ["render-all", str(cfg_a), str(cfg_b), "--out", str(out_dir)],
    )
    assert result.exit_code == 0, result.output

    a_png = out_dir / "snap_a.png"
    b_png = out_dir / "snap_b.png"
    assert a_png.exists() and b_png.exists()
    expected = _expected_grid_px(3, 3)
    with Image.open(a_png) as img:
        assert img.size == expected
    with Image.open(b_png) as img:
        assert img.size == expected
    # Unused layout reference unused — but we ran a_png; double-check layout.
    assert layout_a.exists()


def test_render_all_cross_product_with_layout_override(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_fixture_mosaic(tmp_path)
    layout_small = _write_layout_partial(tmp_path, "layout_small.yaml", 3, 3)
    layout_big = _write_layout_partial(tmp_path, "layout_big.yaml", 5, 4)
    # Snapshot configs each reference layout_small as their default — overridden by CLI.
    snap_x = _write_snapshot_only_config(tmp_path, "snap_x.yaml", "layout_small.yaml")
    snap_y = _write_snapshot_only_config(tmp_path, "snap_y.yaml", "layout_small.yaml")
    out_dir = tmp_path / "out_sweep"

    result = CliRunner().invoke(
        app,
        [
            "render-all",
            str(snap_x),
            str(snap_y),
            "--layout",
            str(layout_small),
            "--layout",
            str(layout_big),
            "--out",
            str(out_dir),
        ],
    )
    assert result.exit_code == 0, result.output

    pairs = [
        ("snap_x__layout_small.png", _expected_grid_px(3, 3)),
        ("snap_x__layout_big.png", _expected_grid_px(5, 4)),
        ("snap_y__layout_small.png", _expected_grid_px(3, 3)),
        ("snap_y__layout_big.png", _expected_grid_px(5, 4)),
    ]
    for name, expected in pairs:
        png = out_dir / name
        assert png.exists(), f"missing {name}"
        with Image.open(png) as img:
            assert img.size == expected, f"{name} dims {img.size} != {expected}"


def test_render_without_output_path_errors_helpfully(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_fixture_mosaic(tmp_path)
    _write_layout_partial(tmp_path, "layout_a.yaml", 3, 3)
    cfg = _write_snapshot_only_config(tmp_path, "snap.yaml", "layout_a.yaml")

    result = CliRunner().invoke(app, ["render", str(cfg)])
    assert result.exit_code == 2
    assert "output_path" in result.output
