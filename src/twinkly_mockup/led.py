"""LedGrid: frame-shaped LED renderer.

Takes an oversampled source raster (typically from `Mosaic.sample`), area-
weighted-downsamples it to one color per LED, and draws each LED as a square
dot at 50% pitch on a black tile substrate with one-LED-pitch inter-tile gaps.
The central cutout is painted with the wall color and emits no LEDs.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageDraw

from .config import LEDS_PER_TILE, Config, Layout, Render

BLACK = (0, 0, 0)


def grid_pixel_size(layout: Layout, render: Render) -> tuple[int, int]:
    """Pixel dimensions of the LED frame canvas.

    Each tile is `LEDS_PER_TILE * scale` px on a side. Adjacent tiles are
    separated by one LED-pitch (`scale` px) of black inter-tile gap.
    """
    r = render.scale_px_per_led
    width = layout.outer_tiles_w * LEDS_PER_TILE * r + (layout.outer_tiles_w - 1) * r
    height = layout.outer_tiles_h * LEDS_PER_TILE * r + (layout.outer_tiles_h - 1) * r
    return width, height


def led_grid_size(layout: Layout) -> tuple[int, int]:
    """LED-count dimensions of the frame: (width_leds, height_leds)."""
    return layout.outer_tiles_w * LEDS_PER_TILE, layout.outer_tiles_h * LEDS_PER_TILE


def _tile_origin_px(tile_i: int, tile_j: int, scale: int) -> tuple[int, int]:
    """Top-left pixel of tile (tile_i, tile_j) inside the frame canvas."""
    pitch = (LEDS_PER_TILE + 1) * scale
    return tile_i * pitch, tile_j * pitch


def _cutout_pixel_rect(layout: Layout, scale: int) -> tuple[int, int, int, int]:
    """Inclusive pixel rectangle (left, top, right, bottom) covering the cutout."""
    left, top = _tile_origin_px(layout.cutout_offset_x, layout.cutout_offset_y, scale)
    width_tiles = layout.cutout_tiles_w
    height_tiles = layout.cutout_tiles_h
    right = left + width_tiles * LEDS_PER_TILE * scale + (width_tiles - 1) * scale - 1
    bottom = top + height_tiles * LEDS_PER_TILE * scale + (height_tiles - 1) * scale - 1
    return left, top, right, bottom


def _tile_is_in_cutout(tile_i: int, tile_j: int, layout: Layout) -> bool:
    return (
        layout.cutout_tiles_w > 0
        and layout.cutout_tiles_h > 0
        and layout.cutout_offset_x <= tile_i < layout.cutout_offset_x + layout.cutout_tiles_w
        and layout.cutout_offset_y <= tile_j < layout.cutout_offset_y + layout.cutout_tiles_h
    )


def downsample_to_leds(source: np.ndarray, layout: Layout) -> np.ndarray:
    """Area-weighted downsample of `source` to one RGB triple per LED.

    Returns a uint8 ndarray of shape `(height_leds, width_leds, 3)`.
    """
    width_leds, height_leds = led_grid_size(layout)
    return cv2.resize(source, (width_leds, height_leds), interpolation=cv2.INTER_AREA)


def render_frame(config: Config, source: np.ndarray) -> Image.Image:
    """Render the LED frame, coloring each non-cutout LED from `source`.

    Pipeline:
      1. Fill the canvas with black — paints both tile substrates and inter-tile gaps.
      2. Paint the cutout rectangle with the wall color.
      3. Area-weighted downsample `source` to one color per LED.
      4. Draw a square dot (~50% of pitch) at every non-cutout LED in its color.
    """
    layout = config.layout
    render = config.render
    scale = render.scale_px_per_led

    width, height = grid_pixel_size(layout, render)
    img = Image.new("RGB", (width, height), BLACK)
    draw = ImageDraw.Draw(img)

    if layout.cutout_tiles_w > 0 and layout.cutout_tiles_h > 0:
        draw.rectangle(_cutout_pixel_rect(layout, scale), fill=tuple(render.wall_color))

    led_colors = downsample_to_leds(source, layout)

    dot_size = max(1, scale // 2)
    dot_offset = (scale - dot_size) // 2

    for tile_j in range(layout.outer_tiles_h):
        for tile_i in range(layout.outer_tiles_w):
            if _tile_is_in_cutout(tile_i, tile_j, layout):
                continue
            tx, ty = _tile_origin_px(tile_i, tile_j, scale)
            for led_r in range(LEDS_PER_TILE):
                for led_c in range(LEDS_PER_TILE):
                    led_row = tile_j * LEDS_PER_TILE + led_r
                    led_col = tile_i * LEDS_PER_TILE + led_c
                    color = tuple(int(c) for c in led_colors[led_row, led_col])
                    x0 = tx + led_c * scale + dot_offset
                    y0 = ty + led_r * scale + dot_offset
                    draw.rectangle(
                        (x0, y0, x0 + dot_size - 1, y0 + dot_size - 1),
                        fill=color,
                    )

    return img
