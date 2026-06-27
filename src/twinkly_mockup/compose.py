"""Compose: final canvas assembly + PNG write."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from .config import Config
from .led import render_frame


def render_to_png(config: Config) -> Path:
    """Render the frame for `config` and write it to `config.output_path`.

    Later slices will overlay the car silhouette and any wall margin here;
    the walking skeleton just persists the LED frame.
    """
    img: Image.Image = render_frame(config)
    out_path = Path(config.output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG")
    return out_path
