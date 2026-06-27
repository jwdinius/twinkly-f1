"""Compose: final canvas assembly + PNG write."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from .config import Config
from .led import led_grid_size, render_frame
from .mosaic import Mosaic


def render_to_png(config: Config) -> Path:
    """Render the frame for `config` and write it to `config.output_path`.

    Loads the snapshot's mosaic, samples an oriented oversampled crop, and
    hands it to `LedGrid` for area-weighted downsampling + dot rendering.
    Later slices will overlay the car silhouette and any wall margin here.
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
    img: Image.Image = render_frame(config, source)
    out_path = Path(config.output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG")
    return out_path
