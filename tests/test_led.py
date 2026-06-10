"""LedGrid rendering tests."""

from __future__ import annotations

from PIL import Image

from twinkly_mockup.config import LEDS_PER_TILE, Config, Layout, Render
from twinkly_mockup.led import PLACEHOLDER_LED, grid_pixel_size, render_frame


WALL = (240, 240, 235)
BLACK = (0, 0, 0)


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
        output_path="ignored.png",
    )


def _color_at(img: Image.Image, x: int, y: int) -> tuple[int, int, int]:
    return img.convert("RGB").getpixel((x, y))


def test_grid_pixel_size_formula() -> None:
    # 9 × 6 outer at 10 px/LED: 9*6*10 + 8*10 = 620; 6*6*10 + 5*10 = 410
    w, h = grid_pixel_size(_config(outer_w=9, outer_h=6).layout, Render(scale_px_per_led=10))
    assert (w, h) == (620, 410)


def test_output_image_dimensions_match_formula() -> None:
    cfg = _config(outer_w=9, outer_h=6, cutout_w=4, cutout_h=2, cutout_x=2, cutout_y=2)
    img = render_frame(cfg)
    assert img.size == (620, 410)


def test_cutout_region_has_no_led_dots() -> None:
    """No LED dot pixel should appear inside the cutout rectangle."""
    cfg = _config(outer_w=9, outer_h=6, cutout_w=4, cutout_h=2, cutout_x=2, cutout_y=2)
    img = render_frame(cfg).convert("RGB")
    scale = cfg.render.scale_px_per_led
    pitch = (LEDS_PER_TILE + 1) * scale  # 70

    left = cfg.layout.cutout_offset_x * pitch
    top = cfg.layout.cutout_offset_y * pitch
    right = left + cfg.layout.cutout_tiles_w * LEDS_PER_TILE * scale + (cfg.layout.cutout_tiles_w - 1) * scale
    bottom = top + cfg.layout.cutout_tiles_h * LEDS_PER_TILE * scale + (cfg.layout.cutout_tiles_h - 1) * scale

    pixels = img.load()
    for x in range(left, right):
        for y in range(top, bottom):
            assert pixels[x, y] != PLACEHOLDER_LED, (
                f"unexpected LED dot at ({x},{y}) inside cutout"
            )
            assert pixels[x, y] == WALL


def test_led_dot_is_50pct_of_pitch_square_on_black_substrate() -> None:
    """The first LED of the first non-cutout tile sits centered in its cell."""
    cfg = _config(outer_w=3, outer_h=3, cutout_w=0, cutout_h=0, cutout_x=0, cutout_y=0, scale=10)
    img = render_frame(cfg)
    # First tile origin = (0, 0). First LED cell = (0..10, 0..10).
    # Dot is 5 px wide (50% of 10) with 2 px offset → covers (2..7, 2..7).
    assert _color_at(img, 0, 0) == BLACK  # substrate corner
    assert _color_at(img, 1, 1) == BLACK  # still substrate
    assert _color_at(img, 2, 2) == PLACEHOLDER_LED  # dot top-left
    assert _color_at(img, 6, 6) == PLACEHOLDER_LED  # dot bottom-right (inclusive)
    assert _color_at(img, 7, 7) == BLACK  # back to substrate
    assert _color_at(img, 9, 9) == BLACK  # substrate corner of LED cell


def test_inter_tile_gap_is_one_led_pitch_of_black() -> None:
    """Between two adjacent LED tiles there must be `scale` px of pure black."""
    cfg = _config(outer_w=3, outer_h=1, cutout_w=0, cutout_h=0, scale=10)
    img = render_frame(cfg)
    # First tile spans x=0..59 (6*10=60 px); gap x=60..69; second tile starts at x=70.
    for x in range(60, 70):
        # Sample a few rows.
        for y in (0, 5, 30, 59):
            assert _color_at(img, x, y) == BLACK, f"expected black gap at ({x},{y})"


def test_no_cutout_when_zero_tiles() -> None:
    """A zero-sized cutout leaves the full frame intact."""
    cfg = _config(outer_w=2, outer_h=2, cutout_w=0, cutout_h=0)
    img = render_frame(cfg).convert("RGB")
    # The entire image should only contain BLACK and PLACEHOLDER_LED.
    colors = {img.getpixel((x, y)) for x in range(img.width) for y in range(img.height)}
    assert colors == {BLACK, PLACEHOLDER_LED}
