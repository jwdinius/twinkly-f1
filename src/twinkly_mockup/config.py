"""Configuration models loaded from YAML."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

LEDS_PER_TILE = 6
TILE_PITCH_M = 0.16
LED_PITCH_M = TILE_PITCH_M / LEDS_PER_TILE

# Scale ratio between the LEGO Technic MCL39 and a real F1 car, anchored to
# car width: 25 cm LEGO vs 2.10 m real → 0.25 / 2.10 ≈ 1 : 8.4.
LEGO_CAR_WIDTH_M = 0.25
REAL_F1_CAR_WIDTH_M = 2.10
LEGO_SCALE = LEGO_CAR_WIDTH_M / REAL_F1_CAR_WIDTH_M  # ≈ 0.119

# Real-world track meters mapped to one Twinkly tile.
# Derived so the wall view shares the LEGO car's 1:8.4 scale — i.e. each cm of
# wall represents 8.4 cm of real track, so the asphalt around the car is
# geometrically consistent with the model on top of it. Used when a snapshot
# omits viewport_m so layout sweeps render layout-appropriate coverage instead
# of the same crop at varying LED densities.
WALL_TILE_VIEW_M = TILE_PITCH_M / LEGO_SCALE  # ≈ 1.344 m per tile


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


class CarSpec(BaseModel):
    """Physical dimensions and on-wall orientation of the LEGO model car.

    `dimensions_cm` is `(length, width)` of the model in centimeters; the
    silhouette is drawn axis-aligned with the nose pointing image-up.
    `orientation_deg` rotates the silhouette CCW by that many degrees when
    `compose` drops it into the cutout — matches the project's ENU yaw
    convention (CCW from +x).
    """

    model_config = ConfigDict(extra="forbid")

    dimensions_cm: tuple[float, float] = (61.0, 25.0)
    orientation_deg: float = 0.0


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
    viewport_m: tuple[float, float] | None = None


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layout: Layout
    render: Render = Field(default_factory=Render)
    snapshot: Snapshot
    car: CarSpec = Field(default_factory=CarSpec)
    output_path: Path | None = None

    def viewport_m(self) -> tuple[float, float]:
        """Resolved viewport: snapshot value if set, else derived from layout."""
        if self.snapshot.viewport_m is not None:
            return self.snapshot.viewport_m
        return (
            self.layout.outer_tiles_w * WALL_TILE_VIEW_M,
            self.layout.outer_tiles_h * WALL_TILE_VIEW_M,
        )


class LayoutConfigFile(BaseModel):
    """Standalone layout-sweep YAML: only `layout` and `render`.

    Snapshot YAMLs reference one of these via `layout_config:`, and the
    `render-all` CLI can override the choice per invocation via `--layout`.
    """

    model_config = ConfigDict(extra="forbid")

    layout: Layout
    render: Render = Field(default_factory=Render)


def load_layout_config(path: Path) -> LayoutConfigFile:
    """Load and validate a standalone layout-sweep YAML."""
    raw = _load_yaml_mapping(Path(path))
    return LayoutConfigFile.model_validate(raw)


def load_config(path: Path, *, layout_override: Path | None = None) -> Config:
    """Load and validate a YAML config from disk.

    Snapshot YAMLs may delegate their `layout` and `render` sections to a
    standalone layout file via a `layout_config:` field (resolved relative to
    the snapshot YAML's directory). `layout_override`, when provided, replaces
    the layout reference unconditionally — used by `render-all --layout` to
    sweep one snapshot across multiple candidate layouts.

    Relative paths inside the config (`snapshot.mosaic`, `layout_config`) are
    resolved against the config file's directory so configs are portable.
    """
    cfg_path = Path(path)
    raw = _load_yaml_mapping(cfg_path)

    layout_ref = raw.pop("layout_config", None)
    if layout_ref is not None:
        ref_path = Path(layout_ref)
        if not ref_path.is_absolute():
            ref_path = (cfg_path.parent / ref_path).resolve()
        _merge_layout_partial(raw, ref_path, overwrite=False)

    if layout_override is not None:
        _merge_layout_partial(raw, Path(layout_override), overwrite=True)

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


def _load_yaml_mapping(path: Path) -> dict:
    with path.open("r") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"config at {path} must be a YAML mapping, got {type(raw).__name__}")
    return raw


def _merge_layout_partial(raw: dict, layout_path: Path, *, overwrite: bool) -> None:
    """Merge `layout` and `render` from a layout YAML into `raw` in place.

    With `overwrite=False`, only fills keys missing from `raw` (used for an
    in-YAML `layout_config:` reference, where inline values take priority).
    With `overwrite=True`, replaces existing keys (used for a CLI override).
    """
    layout_raw = _load_yaml_mapping(layout_path)
    LayoutConfigFile.model_validate(layout_raw)
    for key in ("layout", "render"):
        if key in layout_raw and (overwrite or key not in raw):
            raw[key] = layout_raw[key]
