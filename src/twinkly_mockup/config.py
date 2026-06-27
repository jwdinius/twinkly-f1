"""Configuration models loaded from YAML."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

LEDS_PER_TILE = 6


class Layout(BaseModel):
    """Tile-frame layout in whole tiles.

    The frame is an `outer_tiles_w × outer_tiles_h` rectangle of tiles with a
    rectangular cutout of `cutout_tiles_w × cutout_tiles_h` tiles whose
    top-left tile is at grid index `(cutout_offset_x, cutout_offset_y)`.
    """

    model_config = ConfigDict(extra="forbid")

    outer_tiles_w: int = Field(ge=1)
    outer_tiles_h: int = Field(ge=1)
    cutout_tiles_w: int = Field(ge=0)
    cutout_tiles_h: int = Field(ge=0)
    cutout_offset_x: int = Field(ge=0)
    cutout_offset_y: int = Field(ge=0)

    @model_validator(mode="after")
    def _cutout_fits_inside_outer(self) -> "Layout":
        if self.cutout_offset_x + self.cutout_tiles_w > self.outer_tiles_w:
            raise ValueError(
                "cutout extends past outer frame on x: "
                f"{self.cutout_offset_x} + {self.cutout_tiles_w} > {self.outer_tiles_w}"
            )
        if self.cutout_offset_y + self.cutout_tiles_h > self.outer_tiles_h:
            raise ValueError(
                "cutout extends past outer frame on y: "
                f"{self.cutout_offset_y} + {self.cutout_tiles_h} > {self.outer_tiles_h}"
            )
        return self


class Render(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scale_px_per_led: int = Field(default=10, ge=1)
    wall_color: tuple[int, int, int] = (240, 240, 235)


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layout: Layout
    render: Render = Field(default_factory=Render)
    output_path: Path


def load_config(path: Path) -> Config:
    """Load and validate a YAML config from disk."""
    with Path(path).open("r") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"config at {path} must be a YAML mapping, got {type(raw).__name__}")
    return Config.model_validate(raw)
