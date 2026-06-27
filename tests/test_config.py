"""Config validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from twinkly_mockup.config import Config, load_config


VALID_RAW = {
    "layout": {
        "outer_tiles_w": 9,
        "outer_tiles_h": 6,
        "cutout_tiles_w": 4,
        "cutout_tiles_h": 2,
        "cutout_offset_x": 2,
        "cutout_offset_y": 2,
    },
    "render": {"scale_px_per_led": 10},
    "snapshot": {
        "mosaic": "mosaic.yaml",
        "x_m": 0.0,
        "y_m": 0.0,
        "yaw_rad": 0.0,
        "viewport_m": [100.0, 60.0],
    },
    "output_path": "out/example.png",
}


def test_valid_config_parses() -> None:
    cfg = Config.model_validate(VALID_RAW)
    assert cfg.layout.outer_tiles_w == 9
    assert cfg.render.scale_px_per_led == 10
    assert cfg.snapshot.viewport_m == (100.0, 60.0)
    assert cfg.output_path == Path("out/example.png")


def test_unknown_top_level_key_rejected() -> None:
    bad = {**VALID_RAW, "garbage_key": True}
    with pytest.raises(ValidationError) as exc:
        Config.model_validate(bad)
    assert "garbage_key" in str(exc.value)


def test_unknown_layout_key_rejected() -> None:
    bad = {**VALID_RAW, "layout": {**VALID_RAW["layout"], "weird_field": 5}}
    with pytest.raises(ValidationError) as exc:
        Config.model_validate(bad)
    assert "weird_field" in str(exc.value)


def test_unknown_snapshot_key_rejected() -> None:
    bad = {**VALID_RAW, "snapshot": {**VALID_RAW["snapshot"], "fps": 30}}
    with pytest.raises(ValidationError) as exc:
        Config.model_validate(bad)
    assert "fps" in str(exc.value)


def test_cutout_must_fit_inside_outer() -> None:
    bad = {
        **VALID_RAW,
        "layout": {**VALID_RAW["layout"], "cutout_offset_x": 6, "cutout_tiles_w": 4},
    }
    with pytest.raises(ValidationError) as exc:
        Config.model_validate(bad)
    assert "cutout" in str(exc.value).lower()


def test_snapshot_required() -> None:
    bad = {k: v for k, v in VALID_RAW.items() if k != "snapshot"}
    with pytest.raises(ValidationError) as exc:
        Config.model_validate(bad)
    assert "snapshot" in str(exc.value).lower()


def test_load_resolves_snapshot_mosaic_relative_to_config_dir(tmp_path: Path) -> None:
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(
        "layout:\n"
        "  outer_tiles_w: 3\n"
        "  outer_tiles_h: 3\n"
        "  cutout_tiles_w: 1\n"
        "  cutout_tiles_h: 1\n"
        "  cutout_offset_x: 1\n"
        "  cutout_offset_y: 1\n"
        "snapshot:\n"
        "  mosaic: side/m.yaml\n"
        "  x_m: 0.0\n"
        "  y_m: 0.0\n"
        "  viewport_m: [10.0, 10.0]\n"
        "output_path: out/x.png\n"
    )
    cfg = load_config(cfg_path)
    assert cfg.render.scale_px_per_led == 10  # default
    assert cfg.snapshot.mosaic == (tmp_path / "side" / "m.yaml").resolve()


def _write_layout_partial(dir_: Path, name: str, outer_w: int) -> Path:
    path = dir_ / name
    path.write_text(
        "layout:\n"
        f"  outer_tiles_w: {outer_w}\n"
        "  outer_tiles_h: 3\n"
        "  cutout_tiles_w: 1\n"
        "  cutout_tiles_h: 1\n"
        "  cutout_offset_x: 1\n"
        "  cutout_offset_y: 1\n"
    )
    return path


def _write_snapshot_only(dir_: Path, name: str, layout_ref: str | None) -> Path:
    cfg = dir_ / name
    body = ""
    if layout_ref is not None:
        body += f"layout_config: {layout_ref}\n"
    body += (
        "snapshot:\n"
        "  mosaic: m.yaml\n"
        "  x_m: 0.0\n"
        "  y_m: 0.0\n"
    )
    cfg.write_text(body)
    return cfg


def test_layout_config_reference_fills_missing_layout(tmp_path: Path) -> None:
    _write_layout_partial(tmp_path, "layout_a.yaml", outer_w=5)
    snap = _write_snapshot_only(tmp_path, "snap.yaml", layout_ref="layout_a.yaml")
    cfg = load_config(snap)
    assert cfg.layout.outer_tiles_w == 5


def test_layout_override_replaces_in_yaml_reference(tmp_path: Path) -> None:
    _write_layout_partial(tmp_path, "layout_a.yaml", outer_w=5)
    layout_b = _write_layout_partial(tmp_path, "layout_b.yaml", outer_w=7)
    snap = _write_snapshot_only(tmp_path, "snap.yaml", layout_ref="layout_a.yaml")
    cfg = load_config(snap, layout_override=layout_b)
    assert cfg.layout.outer_tiles_w == 7


def test_inline_layout_takes_priority_over_reference(tmp_path: Path) -> None:
    _write_layout_partial(tmp_path, "layout_ref.yaml", outer_w=5)
    snap = tmp_path / "snap.yaml"
    snap.write_text(
        "layout_config: layout_ref.yaml\n"
        "layout:\n"
        "  outer_tiles_w: 9\n"
        "  outer_tiles_h: 3\n"
        "  cutout_tiles_w: 1\n"
        "  cutout_tiles_h: 1\n"
        "  cutout_offset_x: 4\n"
        "  cutout_offset_y: 1\n"
        "snapshot:\n"
        "  mosaic: m.yaml\n"
        "  x_m: 0.0\n"
        "  y_m: 0.0\n"
    )
    cfg = load_config(snap)
    assert cfg.layout.outer_tiles_w == 9  # inline wins


def test_layout_override_replaces_even_inline_layout(tmp_path: Path) -> None:
    layout_override_path = _write_layout_partial(tmp_path, "layout_ovr.yaml", outer_w=7)
    snap = tmp_path / "snap.yaml"
    snap.write_text(
        "layout:\n"
        "  outer_tiles_w: 9\n"
        "  outer_tiles_h: 3\n"
        "  cutout_tiles_w: 1\n"
        "  cutout_tiles_h: 1\n"
        "  cutout_offset_x: 4\n"
        "  cutout_offset_y: 1\n"
        "snapshot:\n"
        "  mosaic: m.yaml\n"
        "  x_m: 0.0\n"
        "  y_m: 0.0\n"
    )
    cfg = load_config(snap, layout_override=layout_override_path)
    assert cfg.layout.outer_tiles_w == 7  # override wins


def test_layout_partial_rejects_unknown_keys(tmp_path: Path) -> None:
    bad_layout = tmp_path / "bad.yaml"
    bad_layout.write_text(
        "layout:\n"
        "  outer_tiles_w: 3\n"
        "  outer_tiles_h: 3\n"
        "  cutout_tiles_w: 1\n"
        "  cutout_tiles_h: 1\n"
        "  cutout_offset_x: 1\n"
        "  cutout_offset_y: 1\n"
        "weird_top_level: true\n"
    )
    snap = _write_snapshot_only(tmp_path, "snap.yaml", layout_ref="bad.yaml")
    with pytest.raises(ValidationError) as exc:
        load_config(snap)
    assert "weird_top_level" in str(exc.value)
