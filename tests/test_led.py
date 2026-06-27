"""LedGrid rendering tests."""

from __future__ import annotations

import numpy as np
from PIL import Image

from twinkly_mockup.config import LEDS_PER_TILE, Config, Layout, Render, Snapshot
from twinkly_mockup.led import grid_pixel_size, led_grid_size, render_frame


WALL = (240, 240, 235)
BLACK = (0, 0, 0)
LIT = (200, 50, 50)  # a distinctive source-image color


def _config(
    *,
    outer_w: int = 3,
    outer_h: int = 3,
    cutout_w: int = 1,
    cutout_h: int = 1,
    cutout_x: int = 1,
    cutout_y: int = 1,
    scale: int = 10,
) -> Config:
    return Config(
        layout=Layout(
            outer_tiles_w=outer_w,
            outer_tiles_h=outer_h,
            cutout_tiles_w=cutout_w,
            cutout_tiles_h=cutout_h,
            cutout_offset_x=cutout_x,
            cutout_offset_y=cutout_y,
        ),
        render=Render(scale_px_per_led=scale, wall_color=WALL),
        snapshot=Snapshot(
            mosaic="unused.yaml", x_m=0.0, y_m=0.0, viewport_m=(10.0, 10.0)
        ),
        output_path="ignored.png",
    )


def _solid_source(cfg: Config, color: tuple[int, int, int]) -> np.ndarray:
    """A source raster sized to the LED grid filled with `color`."""
    w_leds, h_leds = led_grid_size(cfg.layout)
    return np.full((h_leds, w_leds, 3), color, dtype=np.uint8)


def _color_at(img: Image.Image, x: int, y: int) -> tuple[int, int, int]:
    return img.convert("RGB").getpixel((x, y))


def test_grid_pixel_size_formula() -> None:
    # 9 × 6 outer at 10 px/LED: 9*6*10 + 8*10 = 620; 6*6*10 + 5*10 = 410
    w, h = grid_pixel_size(_config(outer_w=9, outer_h=6).layout, Render(scale_px_per_led=10))
    assert (w, h) == (620, 410)


def test_output_image_dimensions_match_formula() -> None:
    cfg = _config(outer_w=9, outer_h=6, cutout_w=4, cutout_h=2, cutout_x=2, cutout_y=2)
    img = render_frame(cfg, _solid_source(cfg, LIT))
    assert img.size == (620, 410)


def test_cutout_region_is_wall_colored_and_has_no_led_dots() -> None:
    cfg = _config(outer_w=9, outer_h=6, cutout_w=4, cutout_h=2, cutout_x=2, cutout_y=2)
    img = render_frame(cfg, _solid_source(cfg, LIT)).convert("RGB")
    scale = cfg.render.scale_px_per_led
    pitch = (LEDS_PER_TILE + 1) * scale  # 70

    left = cfg.layout.cutout_offset_x * pitch
    top = cfg.layout.cutout_offset_y * pitch
    right = left + cfg.layout.cutout_tiles_w * LEDS_PER_TILE * scale + (cfg.layout.cutout_tiles_w - 1) * scale
    bottom = top + cfg.layout.cutout_tiles_h * LEDS_PER_TILE * scale + (cfg.layout.cutout_tiles_h - 1) * scale

    pixels = img.load()
    for x in range(left, right):
        for y in range(top, bottom):
            assert pixels[x, y] != LIT, f"unexpected LED dot at ({x},{y}) inside cutout"
            assert pixels[x, y] == WALL


def test_led_dot_takes_source_color_on_black_substrate() -> None:
    """The first LED of the first non-cutout tile sits centered in its cell and
    takes the source color."""
    cfg = _config(outer_w=3, outer_h=3, cutout_w=0, cutout_h=0, cutout_x=0, cutout_y=0, scale=10)
    img = render_frame(cfg, _solid_source(cfg, LIT))
    # First tile origin = (0, 0). First LED cell = (0..10, 0..10).
    # Dot is 5 px wide (50% of 10) with 2 px offset → covers (2..7, 2..7).
    assert _color_at(img, 0, 0) == BLACK  # substrate corner
    assert _color_at(img, 1, 1) == BLACK
    assert _color_at(img, 2, 2) == LIT  # dot top-left
    assert _color_at(img, 6, 6) == LIT  # dot bottom-right (inclusive)
    assert _color_at(img, 7, 7) == BLACK
    assert _color_at(img, 9, 9) == BLACK


def test_inter_tile_gap_is_one_led_pitch_of_black() -> None:
    cfg = _config(outer_w=3, outer_h=1, cutout_w=0, cutout_h=0, scale=10)
    img = render_frame(cfg, _solid_source(cfg, LIT))
    # First tile spans x=0..59 (6*10=60 px); gap x=60..69; second tile starts at x=70.
    for x in range(60, 70):
        for y in (0, 5, 30, 59):
            assert _color_at(img, x, y) == BLACK, f"expected black gap at ({x},{y})"


def test_no_cutout_solid_source_yields_black_plus_source_color() -> None:
    cfg = _config(outer_w=2, outer_h=2, cutout_w=0, cutout_h=0)
    img = render_frame(cfg, _solid_source(cfg, LIT)).convert("RGB")
    colors = {img.getpixel((x, y)) for x in range(img.width) for y in range(img.height)}
    assert colors == {BLACK, LIT}


def test_per_led_color_comes_from_corresponding_source_cell() -> None:
    """Two halves of the source raster paint two halves of the LED frame in
    distinguishable colors — catches downsample-axis swaps."""
    cfg = _config(outer_w=2, outer_h=2, cutout_w=0, cutout_h=0, scale=10)
    w_leds, h_leds = led_grid_size(cfg.layout)
    src = np.zeros((h_leds, w_leds, 3), dtype=np.uint8)
    src[:, : w_leds // 2] = (255, 0, 0)   # left half red
    src[:, w_leds // 2 :] = (0, 0, 255)   # right half blue
    img = render_frame(cfg, src).convert("RGB")
    # First tile (left) should have red dots; second tile (right) should have blue.
    # First LED of first tile is at pixel (2, 2) per dot geometry.
    assert _color_at(img, 2, 2) == (255, 0, 0)
    # First tile spans cols 0..59; gap 60..69; second tile starts at col 70.
    assert _color_at(img, 72, 2) == (0, 0, 255)
