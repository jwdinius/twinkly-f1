"""Integration test: `twinkly-mockup render` end-to-end via the typer runner."""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from typer.testing import CliRunner

from twinkly_mockup.cli import app
from twinkly_mockup.led import PLACEHOLDER_LED

WALL = (240, 240, 235)
BLACK = (0, 0, 0)


def _write_fixture_config(tmp_path: Path, output_relative: str = "out/fixture.png") -> Path:
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
        f"output_path: {output_relative}\n"
    )
    return cfg


def test_render_produces_png_at_configured_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cfg_path = _write_fixture_config(tmp_path)

    runner = CliRunner()
    result = runner.invoke(app, ["render", str(cfg_path)])
    assert result.exit_code == 0, result.output

    png = tmp_path / "out" / "fixture.png"
    assert png.exists()

    with Image.open(png) as img:
        img = img.convert("RGB")
        # 3*6*10 + 2*10 = 200 px
        assert img.size == (200, 200)
        colors = {img.getpixel((x, y)) for x in range(img.width) for y in range(img.height)}
    assert colors == {BLACK, PLACEHOLDER_LED, WALL}, (
        f"expected wall + black + grey palette, got {colors}"
    )


def test_help_works() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "render" in result.output
