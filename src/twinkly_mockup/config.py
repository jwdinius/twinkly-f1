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
    mosaic_oversample: int = Field(default=8, ge=1)


class Snapshot(BaseModel):
    """Where the camera is looking, in the project's ENU frame.

    `mosaic` is the path to a mosaic sidecar YAML (see `mosaic.MosaicSidecar`),
    resolved relative to the directory of the config that referenced it.
    """

    model_config = ConfigDict(extra="forbid")

    mosaic: Path
    x_m: float
    y_m: float
    yaw_rad: float = 0.0
    viewport_m: tuple[float, float]


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layout: Layout
    render: Render = Field(default_factory=Render)
    snapshot: Snapshot
    output_path: Path


def load_config(path: Path) -> Config:
    """Load and validate a YAML config from disk.

    Relative paths inside the config (today: `snapshot.mosaic`) are resolved
    against the config file's directory so configs are portable.
    """
    cfg_path = Path(path)
    with cfg_path.open("r") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"config at {path} must be a YAML mapping, got {type(raw).__name__}")
    cfg = Config.model_validate(raw)
    if not cfg.snapshot.mosaic.is_absolute():
        cfg = cfg.model_copy(
            update={
                "snapshot": cfg.snapshot.model_copy(
                    update={"mosaic": (cfg_path.parent / cfg.snapshot.mosaic).resolve()}
                ),
            }
        )
    return cfg
