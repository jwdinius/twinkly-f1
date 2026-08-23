"""Smoke tests for the YAMLs shipped under `configs/`.

These configs anchor the 3 × 3 sizing sweep that picks the MVP layout. They
are validated end-to-end (pydantic + composition) so that a typo blocking the
sweep surfaces in CI rather than during the visual review.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from twinkly_mockup.solver import circuit_xml_name, load_solver_config
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

# Silverstone is the sweep the layout decision now rests on; Monaco's three are
# kept so the cross-product still covers two circuits' sidecars.
SILVERSTONE_SNAPSHOTS = [
    "silverstone_club.yaml",
    "silverstone_luffield.yaml",
    "silverstone_abbey.yaml",
]
MONACO_SNAPSHOTS = ["monaco_massenet.yaml", "monaco_loews.yaml", "monaco_tabac.yaml"]
SNAPSHOTS = SILVERSTONE_SNAPSHOTS + MONACO_SNAPSHOTS


@pytest.mark.parametrize(("name", "ow", "oh", "cw", "ch"), LAYOUTS)
def test_layout_sweep_yaml_validates(name: str, ow: int, oh: int, cw: int, ch: int) -> None:
    lc = load_layout_config(CONFIGS / name)
    assert isinstance(lc, LayoutConfigFile)
    assert lc.layout.outer_tiles_w == ow
    assert lc.layout.outer_tiles_h == oh
    assert lc.layout.cutout_tiles_w == cw
    assert lc.layout.cutout_tiles_h == ch


@pytest.mark.parametrize("name", SNAPSHOTS)
def test_snapshot_composes_with_default_layout(name: str) -> None:
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
    # Car: MCL39 dimensions, mounted nose-right (orientation_deg=-90) so the
    # silhouette lies along the 4×2 cutout's long axis with the front facing
    # into the wider wall area. The snapshot's yaw_rad is biased by +π/2 vs
    # the raw track tangent so image-RIGHT ends up aligned with the tangent —
    # see scripts/derive_corner_poses.py.
    assert cfg.car.dimensions_cm == (61.0, 25.0)
    assert cfg.car.orientation_deg == -90.0


@pytest.mark.parametrize("snap", SNAPSHOTS)
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


# --- lap-sequence walls ------------------------------------------------------

LAP_CONFIGS = ["silverstone_lap.yaml", "silverstone_lap_double_gsd.yaml"]


@pytest.mark.parametrize("name", LAP_CONFIGS)
def test_lap_config_is_the_standard_wall(name: str) -> None:
    """Both lap walls are the 9 × 6 layout, differing only in scale."""
    cfg = load_config(CONFIGS / name)
    assert cfg.layout.outer_tiles_w == 9
    assert cfg.layout.outer_tiles_h == 6
    assert cfg.car.orientation_deg == -90.0


def test_scale_faithful_lap_wall_derives_its_viewport() -> None:
    """silverstone_lap.yaml keeps the LEGO car's 1:8.4 scale, so it sets no viewport."""
    cfg = load_config(CONFIGS / "silverstone_lap.yaml")
    assert cfg.snapshot.viewport_m is None
    assert cfg.viewport_m() == (9 * WALL_TILE_VIEW_M, 6 * WALL_TILE_VIEW_M)
    assert (cfg.layout.cutout_tiles_w, cfg.layout.cutout_tiles_h) == (4, 2)


def test_double_gsd_lap_wall_doubles_the_view_and_halves_the_car() -> None:
    """Doubling ground sample distance halves the car's footprint in tiles.

    The car is a fixed real-world object, so at 2× GSD it spans 2 × 1 tiles
    instead of 4 × 2 — the two numbers move together, and a viewport that
    doubled without the cutout following (or vice versa) would put a
    wrongly-sized silhouette on the wall. Pinned so they cannot drift apart.
    """
    scale_faithful = load_config(CONFIGS / "silverstone_lap.yaml")
    doubled = load_config(CONFIGS / "silverstone_lap_double_gsd.yaml")

    base_w, base_h = scale_faithful.viewport_m()
    wide_w, wide_h = doubled.viewport_m()
    assert (wide_w, wide_h) == pytest.approx((2 * base_w, 2 * base_h))

    assert (doubled.layout.cutout_tiles_w, doubled.layout.cutout_tiles_h) == (2, 1)
    assert doubled.car.dimensions_cm == pytest.approx(
        tuple(d / 2 for d in scale_faithful.car.dimensions_cm)
    )


# --- solver requests ---------------------------------------------------------

SOLVER_CONFIGS = ["silverstone_solver.yaml", "monaco_solver.yaml"]


@pytest.mark.parametrize("name", SOLVER_CONFIGS)
def test_solver_config_validates_and_names_its_circuit(name: str) -> None:
    """A shipped solve request must load, and its `circuit` must match its name.

    The circuit names the emitted XML, so a mismatch here is how an
    `artifacts/` dir ends up with a `monaco.xml` produced from Silverstone
    track limits.
    """
    config = load_solver_config(CONFIGS / name)
    assert name == f"{config.circuit}_solver.yaml"
    assert circuit_xml_name(config) == f"{config.circuit}.xml"
    assert config.left_kml == CONFIGS / f"{config.circuit}_left.kml"
    assert config.right_kml == CONFIGS / f"{config.circuit}_right.kml"
    assert config.is_closed


def test_silverstone_solve_request_has_its_track_limits() -> None:
    """Silverstone is the validation target, so its KMLs must actually exist.

    Monaco's deliberately do not — it was never traced, and a top-down mosaic
    cannot show the tunnel — so this is asserted for Silverstone only.
    """
    config = load_solver_config(CONFIGS / "silverstone_solver.yaml")
    assert config.left_kml.is_file()
    assert config.right_kml.is_file()
