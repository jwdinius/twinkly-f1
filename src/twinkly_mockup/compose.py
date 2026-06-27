"""Compose: final canvas assembly + PNG write."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from .car import make_car
from .config import LED_PITCH_M, Config
from .led import cutout_pixel_rect, led_grid_size, render_frame
from .mosaic import Mosaic


def render_to_png(config: Config) -> Path:
    """Render the frame for `config` and write it to `config.output_path`.

    Loads the snapshot's mosaic, samples an oriented oversampled crop, hands
    it to `LedGrid` for area-weighted downsampling + dot rendering, then
    composites the procedural car silhouette into the cutout.
    """
    mosaic = Mosaic.load(config.snapshot.mosaic)
    width_leds, height_leds = led_grid_size(config.layout)
    over = config.render.mosaic_oversample
    source = mosaic.sample(
        center_xy_m=(config.snapshot.x_m, config.snapshot.y_m),
        yaw_rad=config.snapshot.yaw_rad,
        viewport_m=config.snapshot.viewport_m,
        output_px=(width_leds * over, height_leds * over),
    )
    frame = render_frame(config, source)
    composed = _paste_car(frame, config)

    out_path = Path(config.output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    composed.save(out_path, format="PNG")
    return out_path


def _paste_car(frame: Image.Image, config: Config) -> Image.Image:
    """Composite the procedural car silhouette over the cutout center."""
    length_cm, width_cm = config.car.dimensions_cm
    px_per_meter = config.render.scale_px_per_led / LED_PITCH_M
    car = make_car(length_cm=length_cm, width_cm=width_cm, px_per_meter=px_per_meter)
    if config.car.orientation_deg != 0.0:
        car = car.rotate(
            config.car.orientation_deg,
            resample=Image.BICUBIC,
            expand=True,
        )

    left, top, right, bottom = cutout_pixel_rect(config.layout, config.render.scale_px_per_led)
    cutout_cx = (left + right + 1) // 2
    cutout_cy = (top + bottom + 1) // 2
    paste_left = cutout_cx - car.width // 2
    paste_top = cutout_cy - car.height // 2

    canvas = frame.copy()
    canvas.paste(car, (paste_left, paste_top), mask=car)
    return canvas
