"""The build-time Sidecar emitter (ADR-0004).

`scripts/emit_mosaic_sidecar.py` is what makes "never hand-typed" true rather
than aspirational, so its refusals matter as much as its arithmetic: a raster
that is rotated, non-square, or unprojected cannot back a Sidecar whose whole
schema is one isotropic `m_per_px` plus one rotation angle.

`gdalinfo` payloads are synthesised here rather than shelled out to, so the
suite stays runnable without GDAL on PATH. The real end-to-end check is
`tests/test_frame_registration.py`, which recomputes the emitted values for
every shipped circuit.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

from twinkly_mockup.mosaic import MosaicSidecar

pytest.importorskip("pyproj")

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _emitter():
    spec = importlib.util.spec_from_file_location(
        "emit_mosaic_sidecar", SCRIPTS / "emit_mosaic_sidecar.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # `@dataclass` resolves annotations through `sys.modules[cls.__module__]`, so
    # the module has to be registered before it executes, not after.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


emit = _emitter()

# The Monaco build's actual UTM 32N geotransform, as `gdalinfo -json` reports it.
MONACO_INFO = {
    "size": [4724, 5711],
    "geoTransform": [
        372745.36659342493,
        0.2155795909077048,
        0.0,
        4844467.671905782,
        0.0,
        -0.2155795909077048,
    ],
    "stac": {"proj:epsg": 32632},
}


def test_registration_reproduces_the_shipped_monaco_frame() -> None:
    """The emitter, run on Monaco's raster, is what `configs/` already carries."""
    reg = emit.registration_from_gdalinfo(MONACO_INFO, Path("monaco_utm.tif"))
    shipped = MosaicSidecar.model_validate(
        yaml.safe_load((SCRIPTS.parent / "configs" / "monaco_mosaic.yaml").read_text())
    )
    assert reg.origin_px == shipped.origin_px
    assert reg.epsg == shipped.utm_epsg
    assert reg.m_per_px == pytest.approx(shipped.m_per_px, abs=5e-10)
    assert reg.origin_lat == pytest.approx(shipped.origin_lat, abs=5e-10)
    assert reg.origin_lon == pytest.approx(shipped.origin_lon, abs=5e-10)
    assert reg.north_angle_deg == pytest.approx(shipped.north_angle_deg, abs=5e-10)
    assert reg.grid_scale == pytest.approx(shipped.grid_scale, abs=5e-10)


def test_origin_is_the_raster_centre_not_a_corner() -> None:
    reg = emit.registration_from_gdalinfo(MONACO_INFO, Path("monaco_utm.tif"))
    assert reg.origin_px == (MONACO_INFO["size"][0] / 2, MONACO_INFO["size"][1] / 2)


def test_emitted_yaml_round_trips_through_the_schema() -> None:
    """What the emitter writes must be exactly what `MosaicSidecar` accepts."""
    reg = emit.registration_from_gdalinfo(MONACO_INFO, Path("monaco_utm.tif"))
    text = emit.render_sidecar(reg, "monaco_mosaic.png", "title", ["a note"])
    sidecar = MosaicSidecar.model_validate(yaml.safe_load(text))
    assert sidecar.path == Path("monaco_mosaic.png")
    assert sidecar.utm_epsg == 32632
    assert sidecar.origin_px == (2362.0, 2855.5)
    assert "do not hand-edit" in text


def test_render_is_deterministic() -> None:
    """Re-emitting an unchanged raster must produce no diff."""
    reg = emit.registration_from_gdalinfo(MONACO_INFO, Path("monaco_utm.tif"))
    a = emit.render_sidecar(reg, "monaco_mosaic.png", "t", ["n"])
    b = emit.render_sidecar(reg, "monaco_mosaic.png", "t", ["n"])
    assert a == b


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ({"geoTransform": None}, "no geotransform"),
        # A skewed geotransform: pixel axes are no longer grid east/north.
        ({"geoTransform": [0.0, 0.2, 0.01, 0.0, 0.01, -0.2]}, "rotated"),
        # Anisotropic pixels: one `m_per_px` cannot describe them.
        ({"geoTransform": [0.0, 0.2, 0.0, 0.0, 0.0, -0.3]}, "non-square"),
        # Still in lon/lat — the flat-ENU model would be projecting into degrees.
        ({"stac": {"proj:epsg": 4326}}, "geographic CRS"),
        ({"stac": {}}, "no EPSG-identifiable SRS"),
    ],
)
def test_unusable_rasters_are_refused(mutation: dict, match: str) -> None:
    info = {**MONACO_INFO, **mutation}
    with pytest.raises(emit.EmitError, match=match):
        emit.registration_from_gdalinfo(info, Path("bad.tif"))
