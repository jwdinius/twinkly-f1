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
    "output_path": "out/example.png",
}


def test_valid_config_parses() -> None:
    cfg = Config.model_validate(VALID_RAW)
    assert cfg.layout.outer_tiles_w == 9
    assert cfg.render.scale_px_per_led == 10
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


def test_cutout_must_fit_inside_outer() -> None:
    bad = {
        **VALID_RAW,
        "layout": {**VALID_RAW["layout"], "cutout_offset_x": 6, "cutout_tiles_w": 4},
    }
    with pytest.raises(ValidationError) as exc:
        Config.model_validate(bad)
    assert "cutout" in str(exc.value).lower()


def test_load_from_disk(tmp_path: Path) -> None:
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(
        "layout:\n"
        "  outer_tiles_w: 3\n"
        "  outer_tiles_h: 3\n"
        "  cutout_tiles_w: 1\n"
        "  cutout_tiles_h: 1\n"
        "  cutout_offset_x: 1\n"
        "  cutout_offset_y: 1\n"
        "output_path: out/x.png\n"
    )
    cfg = load_config(cfg_path)
    assert cfg.render.scale_px_per_led == 10  # default
