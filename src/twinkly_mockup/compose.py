"""Compose: final canvas assembly + PNG write."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image

from .car import make_car
from .config import LED_PITCH_M, Config
from .led import cutout_pixel_rect, led_grid_size, render_frame
from .mosaic import Mosaic


def compose_frame(
    config: Config,
    mosaic: Mosaic,
    center_xy_m: tuple[float, float],
    camera_yaw_rad: float,
) -> Image.Image:
    """Render one wall frame looking at `center_xy_m` along `camera_yaw_rad`.

    Samples an oriented oversampled crop, hands it to `LedGrid` for
    area-weighted downsampling + dot rendering, then composites the LEGO car
    silhouette into the cutout. The pose is a parameter rather than being read
    off `config.snapshot`, so the same path serves a still frame and every frame
    of a lap (`sequence.render_lap`); `mosaic` is passed in so a sequence loads
    it once instead of once per frame.
    """
    width_leds, height_leds = led_grid_size(config.layout)
    over = config.render.mosaic_oversample
    source = mosaic.sample(
        center_xy_m=center_xy_m,
        yaw_rad=camera_yaw_rad,
        viewport_m=config.viewport_m(),
        output_px=(width_leds * over, height_leds * over),
    )
    return _paste_car(render_frame(config, source), config)


def render_to_png(config: Config) -> Path:
    """Render the frame for `config`'s snapshot and write it to `output_path`."""
    mosaic = Mosaic.load(config.snapshot.mosaic)
    composed = compose_frame(
        config,
        mosaic,
        center_xy_m=(config.snapshot.x_m, config.snapshot.y_m),
        camera_yaw_rad=config.snapshot.yaw_rad,
    )

    out_path = Path(config.output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    composed.save(out_path, format="PNG")
    return out_path


@lru_cache(maxsize=4)
def _car_sprite(
    length_cm: float, width_cm: float, px_per_meter: float, orientation_deg: float
) -> Image.Image:
    """The mounted car silhouette, ready to paste.

    Cached because the LEGO car is rigidly mounted: its sprite is identical for
    every frame of a lap, and rebuilding it per frame would dominate the
    sequence render.
    """
    car = make_car(length_cm=length_cm, width_cm=width_cm, px_per_meter=px_per_meter)
    if orientation_deg != 0.0:
        car = car.rotate(orientation_deg, resample=Image.BICUBIC, expand=True)
    return car


def _paste_car(frame: Image.Image, config: Config) -> Image.Image:
    """Composite the LEGO car silhouette over the cutout center."""
    length_cm, width_cm = config.car.dimensions_cm
    px_per_meter = config.render.scale_px_per_led / LED_PITCH_M
    car = _car_sprite(length_cm, width_cm, px_per_meter, config.car.orientation_deg)

    left, top, right, bottom = cutout_pixel_rect(config.layout, config.render.scale_px_per_led)
    cutout_cx = (left + right + 1) // 2
    cutout_cy = (top + bottom + 1) // 2
    paste_left = cutout_cx - car.width // 2
    paste_top = cutout_cy - car.height // 2

    canvas = frame.copy()
    canvas.paste(car, (paste_left, paste_top), mask=car)
    return canvas
