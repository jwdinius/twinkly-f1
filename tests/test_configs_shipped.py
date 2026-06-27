"""Smoke tests for the YAMLs shipped under `configs/`.

These configs anchor the 3 × 3 sizing sweep that picks the MVP layout. They
are validated end-to-end (pydantic + composition) so that a typo blocking the
sweep surfaces in CI rather than during the visual review.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from twinkly_mockup.config import (
    LayoutConfigFile,
    WALL_TILE_VIEW_M,
    load_config,
    load_layout_config,
)

CONFIGS = Path(__file__).resolve().parent.parent / "configs"

LAYOUTS = [
    ("layout_tight.yaml", 7, 5, 4, 2),
    ("layout_standard.yaml", 9, 6, 4, 2),
    ("layout_cinematic.yaml", 12, 8, 4, 2),
]

MONACO_SNAPSHOTS = ["monaco_massenet.yaml", "monaco_loews.yaml", "monaco_tabac.yaml"]


@pytest.mark.parametrize(("name", "ow", "oh", "cw", "ch"), LAYOUTS)
def test_layout_sweep_yaml_validates(name: str, ow: int, oh: int, cw: int, ch: int) -> None:
    lc = load_layout_config(CONFIGS / name)
    assert isinstance(lc, LayoutConfigFile)
    assert lc.layout.outer_tiles_w == ow
    assert lc.layout.outer_tiles_h == oh
    assert lc.layout.cutout_tiles_w == cw
    assert lc.layout.cutout_tiles_h == ch


@pytest.mark.parametrize("name", MONACO_SNAPSHOTS)
def test_monaco_snapshot_composes_with_default_layout(name: str) -> None:
    cfg = load_config(CONFIGS / name)
    # Default reference is the standard sweep candidate.
    assert cfg.layout.outer_tiles_w == 9
    assert cfg.layout.outer_tiles_h == 6
    # viewport is unset in-YAML and derives from layout.
    raw = yaml.safe_load((CONFIGS / name).read_text())
    assert "viewport_m" not in raw.get("snapshot", {})
    assert cfg.viewport_m() == (
        9 * WALL_TILE_VIEW_M,
        6 * WALL_TILE_VIEW_M,
    )
    # Car defaults: MCL39 dimensions + 90° rotation so it lies across the cutout.
    assert cfg.car.dimensions_cm == (61.0, 25.0)
    assert cfg.car.orientation_deg == 90.0


@pytest.mark.parametrize("snap", MONACO_SNAPSHOTS)
@pytest.mark.parametrize(("name", "ow", "oh", "cw", "ch"), LAYOUTS)
def test_sweep_cross_product_composes(
    snap: str, name: str, ow: int, oh: int, cw: int, ch: int
) -> None:
    """Every snapshot × layout pair the sweep targets must compose cleanly."""
    cfg = load_config(CONFIGS / snap, layout_override=CONFIGS / name)
    assert cfg.layout.outer_tiles_w == ow
    assert cfg.layout.outer_tiles_h == oh
    assert cfg.viewport_m() == (
        ow * WALL_TILE_VIEW_M,
        oh * WALL_TILE_VIEW_M,
    )
