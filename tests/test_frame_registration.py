"""Frame registration: the sidecar's UTM grid convergence and scale (ADR-0004).

Mosaics are reprojected to UTM so the PNG has isotropic metres per pixel, which
makes the pixel axes UTM **grid** east/north — but ENU offsets are **true**
east/north. `MosaicSidecar.north_angle_deg` and `grid_scale` carry the rotation
and scale between the two, and `scripts/emit_mosaic_sidecar.py` computes both
from the warped raster at build time.

`pyproj` is the authority here, deliberately. A flipped θ *doubles* the
registration error instead of removing it and looks entirely plausible at every
zoom level — which is how the original `north_angle_deg: 0.0` survived a full
extraction run, a debug overlay, and an export. So the sign is pinned by
evaluating the projection at points near the image corners, where a flip is
unmissable, and `test_flipped_convergence_sign_breaks_agreement` proves the
fixture has teeth.

Shipped sidecars are **discovered**, not listed. A new circuit whose sidecar was
hand-written, copied from another zone, or emitted against the wrong raster
fails here rather than being silently skipped — which is the whole point of the
guard.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest
import yaml

from twinkly_mockup.mosaic import Mosaic, MosaicSidecar

pyproj = pytest.importorskip("pyproj")

CONFIGS = Path(__file__).resolve().parent.parent / "configs"

# `example_mosaic.yaml` is a hand-authored 200×200 synthetic fixture — four flat
# colour quadrants at (0°, 0°), with no source raster and therefore no UTM zone
# to be registered against. It is the only sidecar exempt from the guard below;
# every other `*_mosaic.yaml` in `configs/` is a real circuit and must carry a
# projection it can be checked against.
SYNTHETIC_SIDECARS = {"example_mosaic.yaml"}


def shipped_sidecar_names() -> list[str]:
    return sorted(
        p.name for p in CONFIGS.glob("*_mosaic.yaml") if p.name not in SYNTHETIC_SIDECARS
    )


SHIPPED = shipped_sidecar_names()


def _load_shipped(name: str) -> MosaicSidecar:
    return MosaicSidecar.model_validate(yaml.safe_load((CONFIGS / name).read_text()))


def _mosaic(sidecar: MosaicSidecar) -> Mosaic:
    """A `Mosaic` over a stub image — projection never touches pixels."""
    return Mosaic(np.zeros((2, 2, 3), dtype=np.uint8), sidecar)


def _factors_at_origin(sidecar: MosaicSidecar):
    """`pyproj`'s projection factors at the sidecar's own origin and EPSG."""
    crs = pyproj.CRS.from_epsg(sidecar.utm_epsg)
    return pyproj.Proj(crs).get_factors(sidecar.origin_lon, sidecar.origin_lat)


def _image_corner_enu(sidecar: MosaicSidecar) -> list[tuple[float, float]]:
    """The four image corners as ENU metres from the origin.

    Every shipped sidecar anchors the origin at the raster's centre pixel, so the
    half-extent in metres is just `origin_px * m_per_px`.
    """
    half_e = sidecar.origin_px[0] * sidecar.m_per_px
    half_n = sidecar.origin_px[1] * sidecar.m_per_px
    return [(se * half_e, sn * half_n) for se in (-1.0, 1.0) for sn in (-1.0, 1.0)]


def _grid_offsets(sidecar: MosaicSidecar, enu: list[tuple[float, float]]) -> np.ndarray:
    """True-ENU offsets → UTM grid offsets from the origin, via `pyproj`.

    Each ENU offset is placed at its true geodesic distance and azimuth from the
    origin — the definition the rotation-plus-scale model approximates — then
    projected into the mosaic's UTM zone.
    """
    e = np.array([p[0] for p in enu], dtype=np.float64)
    n = np.array([p[1] for p in enu], dtype=np.float64)
    geod = pyproj.Geod(ellps="WGS84")
    lons, lats, _ = geod.fwd(
        np.full(e.shape, sidecar.origin_lon),
        np.full(e.shape, sidecar.origin_lat),
        np.degrees(np.arctan2(e, n)),  # azimuth: clockwise from north
        np.hypot(e, n),
    )
    to_utm = pyproj.Transformer.from_crs(
        "EPSG:4326", f"EPSG:{sidecar.utm_epsg}", always_xy=True
    )
    east0, north0 = to_utm.transform(sidecar.origin_lon, sidecar.origin_lat)
    east, north = to_utm.transform(lons, lats)
    return np.column_stack([east - east0, north - north0])


def _px_error_m(
    mosaic: Mosaic, enu: list[tuple[float, float]], grid: np.ndarray
) -> np.ndarray:
    """Distance, in metres, between `meters_to_px` and the `pyproj` truth."""
    sidecar = mosaic.sidecar
    ox, oy = sidecar.origin_px
    m = sidecar.m_per_px
    got = np.array([mosaic.meters_to_px(p) for p in enu], dtype=np.float64)
    # UTM grid offsets → pixels: east right, north up (image y increases down).
    want = np.column_stack([ox + grid[:, 0] / m, oy - grid[:, 1] / m])
    return np.hypot(*(got - want).T) * m


# --- schema -----------------------------------------------------------------


def test_sidecar_defaults_leave_pre_adr_sidecars_loading_unchanged() -> None:
    """A sidecar with neither new key still validates, and reads as unrotated."""
    sidecar = MosaicSidecar.model_validate(
        {
            "path": "x.png",
            "origin_lat": 0.0,
            "origin_lon": 0.0,
            "origin_px": [0, 0],
            "m_per_px": 1.0,
        }
    )
    assert sidecar.utm_epsg is None
    assert sidecar.grid_scale == 1.0


def test_grid_scale_must_be_positive() -> None:
    with pytest.raises(Exception):
        MosaicSidecar.model_validate(
            {
                "path": "x.png",
                "origin_lat": 0.0,
                "origin_lon": 0.0,
                "origin_px": [0, 0],
                "m_per_px": 1.0,
                "grid_scale": 0.0,
            }
        )


# --- every shipped circuit is registered, and registered correctly ----------


def test_discovery_finds_the_shipped_circuits() -> None:
    """The guard below is only worth anything if it actually sees the circuits."""
    assert set(SHIPPED) >= {"monaco_mosaic.yaml", "silverstone_mosaic.yaml"}


@pytest.mark.parametrize("name", SHIPPED)
def test_shipped_sidecar_declares_its_projection(name: str) -> None:
    """No circuit ships without the zone its pixel axes are defined in."""
    sidecar = _load_shipped(name)
    assert sidecar.utm_epsg is not None, (
        f"{name} has no utm_epsg — rebuild it with scripts/emit_mosaic_sidecar.py "
        f"so its convergence and scale can be checked"
    )
    assert pyproj.CRS.from_epsg(sidecar.utm_epsg).is_projected


@pytest.mark.parametrize("name", SHIPPED)
def test_stored_convergence_and_scale_match_pyproj(name: str) -> None:
    """Recompute both stored values from the sidecar's own origin and EPSG.

    This is the guard that stops a circuit shipping misregistered: the sidecar
    is checked against the projection it claims to be in, so a hand-typed angle,
    a stale value from before a re-bbox, or a zone copied from a neighbouring
    circuit all fail here.
    """
    sidecar = _load_shipped(name)
    factors = _factors_at_origin(sidecar)
    assert sidecar.north_angle_deg == pytest.approx(
        factors.meridian_convergence, abs=1e-6
    ), f"{name}: stored θ {sidecar.north_angle_deg} ≠ pyproj {factors.meridian_convergence}"
    assert sidecar.grid_scale == pytest.approx(factors.meridional_scale, abs=1e-9)


@pytest.mark.parametrize("name", SHIPPED)
def test_stored_convergence_is_not_zero(name: str) -> None:
    """`0.0` is the value that shipped for a year and cost 30 m at Silverstone."""
    assert abs(_load_shipped(name).north_angle_deg) > 1e-3


# --- invertibility ----------------------------------------------------------


@pytest.mark.parametrize("north_angle_deg", [0.0, 1.0902, -1.5647, -33.5])
@pytest.mark.parametrize("grid_scale", [1.0, 0.99982686, 1.25])
def test_meters_px_round_trip_under_convergence_and_scale(
    north_angle_deg: float, grid_scale: float
) -> None:
    """`meters_to_px` and `px_to_meters` stay exact inverses once both are on."""
    sidecar = MosaicSidecar.model_validate(
        {
            "path": "x.png",
            "origin_lat": 43.736872,
            "origin_lon": 7.423325,
            "origin_px": [2612.5, 2604.0],
            "m_per_px": 0.21566,
            "north_angle_deg": north_angle_deg,
            "grid_scale": grid_scale,
        }
    )
    mosaic = Mosaic(np.zeros((2, 2, 3), dtype=np.uint8), sidecar)
    for xy_m in [(0.0, 0.0), (563.0, 0.0), (0.0, -561.0), (-420.0, 461.0), (33.0, -19.0)]:
        assert mosaic.px_to_meters(mosaic.meters_to_px(xy_m)) == pytest.approx(xy_m, abs=1e-6)
    for px in [(0.0, 0.0), (2612.5, 2604.0), (5225.0, 12.0), (91.0, 5208.0)]:
        assert mosaic.meters_to_px(mosaic.px_to_meters(px)) == pytest.approx(px, abs=1e-6)


# --- the sign, pinned by pyproj ---------------------------------------------


@pytest.mark.parametrize("name", SHIPPED)
def test_projection_matches_pyproj_at_image_corners(name: str) -> None:
    """At the image corners the convergence has its longest lever arm."""
    sidecar = _load_shipped(name)
    corners = _image_corner_enu(sidecar)
    err = _px_error_m(_mosaic(sidecar), corners, _grid_offsets(sidecar, corners))
    assert err.max() < 0.05, f"{name}: worst corner error {err.max():.4f} m"


@pytest.mark.parametrize("name", SHIPPED)
def test_flipped_convergence_sign_breaks_agreement(name: str) -> None:
    """The fixture has teeth: a flipped θ doubles the error, it does not cancel.

    Passing under both signs would mean the test pins nothing — which is exactly
    the failure mode that let `north_angle_deg: 0.0` ship.
    """
    sidecar = _load_shipped(name)
    corners = _image_corner_enu(sidecar)
    grid = _grid_offsets(sidecar, corners)
    flipped = _px_error_m(
        _mosaic(sidecar.model_copy(update={"north_angle_deg": -sidecar.north_angle_deg})),
        corners,
        grid,
    )
    assert flipped.max() > 10.0, f"{name}: flipped θ only cost {flipped.max():.4f} m"
    # Roughly twice the *uncorrected* error — the signature of a sign flip rather
    # than of some unrelated drift. Uncorrected is the pre-ADR-0004 sidecar: no
    # rotation, unit scale.
    uncorrected = _px_error_m(
        _mosaic(sidecar.model_copy(update={"north_angle_deg": 0.0, "grid_scale": 1.0})),
        corners,
        grid,
    )
    assert flipped.max() == pytest.approx(2.0 * uncorrected.max(), rel=0.05)


@pytest.mark.parametrize("name", SHIPPED)
def test_affine_residual_over_full_image_is_subpixel(name: str) -> None:
    """Rotation-plus-scale is not an exact inverse transverse Mercator.

    Over a 21×21 grid spanning the whole raster its residual must stay under one
    pixel — two orders of magnitude below the precision of placing a vertex on
    satellite imagery, which is what buys us no browser TM series (ADR-0004).
    """
    sidecar = _load_shipped(name)
    half_e = sidecar.origin_px[0] * sidecar.m_per_px
    half_n = sidecar.origin_px[1] * sidecar.m_per_px
    es, ns = np.meshgrid(
        np.linspace(-half_e, half_e, 21), np.linspace(-half_n, half_n, 21)
    )
    samples = list(zip(es.ravel().tolist(), ns.ravel().tolist()))
    err = _px_error_m(_mosaic(sidecar), samples, _grid_offsets(sidecar, samples))
    assert err.max() < sidecar.m_per_px, (
        f"{name}: residual {err.max():.4f} m exceeds one pixel ({sidecar.m_per_px:.4f} m)"
    )


def _lonlat_px_error_m(mosaic: Mosaic, lons, lats) -> np.ndarray:
    """Error of the whole lon/lat → pixel path, in metres, against `pyproj`."""
    sidecar = mosaic.sidecar
    ox, oy = sidecar.origin_px
    m = sidecar.m_per_px
    to_utm = pyproj.Transformer.from_crs(
        "EPSG:4326", f"EPSG:{sidecar.utm_epsg}", always_xy=True
    )
    east, north = (np.asarray(v) for v in to_utm.transform(lons, lats))
    east0, north0 = to_utm.transform(sidecar.origin_lon, sidecar.origin_lat)
    want = np.column_stack([ox + (east - east0) / m, oy - (north - north0) / m])
    got = np.array(
        [mosaic.lonlat_to_px(lon, lat) for lon, lat in zip(lons, lats, strict=True)]
    )
    return np.hypot(*(got - want).T) * m


@pytest.mark.parametrize("name", SHIPPED)
def test_lonlat_to_px_is_subpixel_at_image_corners(name: str) -> None:
    """The *whole* projection, corner to corner — flat-ENU and the grid affine.

    `test_projection_matches_pyproj_at_image_corners` above checks only the
    second half, fed true-ENU offsets. What the tracer and the renderer actually
    run is lon/lat straight through to pixels, and until #23 the first half was
    a sphere of radius `a`: that stretched east by +0.16% and squeezed north by
    −0.19%, an *anisotropic* scale which ADR-0004's rotation-plus-scale affine
    cannot absorb. It reached the corners as ~1.4 m — 7 px — while the pixel math
    in isolation still measured sub-pixel.

    With the prime-vertical and meridional radii at the origin latitude the same
    corners come in under one pixel, so "sub-pixel" is now a property of the
    pipeline rather than of one half of it.
    """
    sidecar = _load_shipped(name)
    mosaic = _mosaic(sidecar)
    lonlat = [mosaic.enu.to_lonlat(e, n) for e, n in _image_corner_enu(sidecar)]
    err = _lonlat_px_error_m(
        mosaic, [float(p[0]) for p in lonlat], [float(p[1]) for p in lonlat]
    )
    assert err.max() < sidecar.m_per_px, (
        f"{name}: lon/lat→px error {err.max():.4f} m "
        f"exceeds one pixel ({sidecar.m_per_px:.4f} m)"
    )


@pytest.mark.parametrize("name", SHIPPED)
def test_lonlat_px_round_trip_closes(name: str) -> None:
    """`px_to_lonlat` is the exact inverse of `lonlat_to_px`, corners included."""
    sidecar = _load_shipped(name)
    mosaic = _mosaic(sidecar)
    for e, n in _image_corner_enu(sidecar) + [(0.0, 0.0), (17.0, -430.0)]:
        lon, lat = (float(v) for v in mosaic.enu.to_lonlat(e, n))
        back = mosaic.px_to_lonlat(mosaic.lonlat_to_px(lon, lat))
        assert back == pytest.approx((lon, lat), abs=1e-12)


# --- grid_scale reaches the sampling affine ---------------------------------


def _scale_probe(grid_scale: float) -> np.ndarray:
    """Sample a landmark that only lands centred if `sample` applies `grid_scale`."""
    sidecar = MosaicSidecar.model_validate(
        {
            "path": "x.png",
            "origin_lat": 0.0,
            "origin_lon": 0.0,
            "origin_px": [100.0, 100.0],
            "m_per_px": 1.0,
            "north_angle_deg": 20.0,
            "grid_scale": grid_scale,
        }
    )
    landmark_xy_m = (40.0, 0.0)
    truth = Mosaic(np.zeros((200, 200, 3), dtype=np.uint8), sidecar.model_copy(update={"grid_scale": 1.25}))
    lx, ly = (round(v) for v in truth.meters_to_px(landmark_xy_m))
    image = np.zeros((200, 200, 3), dtype=np.uint8)
    image[ly - 2 : ly + 3, lx - 2 : lx + 3] = (220, 60, 60)
    return Mosaic(image, sidecar).sample(
        center_xy_m=landmark_xy_m,
        yaw_rad=0.0,
        viewport_m=(20.0, 20.0),
        output_px=(20, 20),
    )


def test_sample_applies_grid_scale() -> None:
    """The composed `sample` affine carries `grid_scale`, not just `meters_to_px`.

    The landmark is placed where a `grid_scale` of 1.25 puts it; a `sample` that
    dropped the scale would look 9 px away, off the landmark entirely.
    """
    assert tuple(_scale_probe(1.25)[10, 10]) == (220, 60, 60)
    assert tuple(_scale_probe(1.0)[10, 10]) == (0, 0, 0)


# --- the shipped snapshot, pinned against the re-registered frame ------------

# Recaptured twice in quick succession: once when the Monaco sidecar was
# re-emitted from the UTM geotransform (#16), and again when flat-ENU moved off
# a sphere of radius `a` onto the radii of curvature (#23). Both legitimately
# move the Massenet crop. The digest is the pin that a *later* change does not
# move it again unnoticed.
MONACO_MASSENET_SAMPLE_SHA256 = (
    "446c1406b8496a986f88d9e29fac02cbe3be9162051b83fcb825973e89d6658e"
)


@pytest.mark.skipif(
    not (CONFIGS / "monaco_mosaic.png").exists(),
    reason="monaco_mosaic.png is build output (gitignored); rebuild via scripts/build_monaco_mosaic.sh",
)
def test_monaco_snapshot_sample_is_byte_identical() -> None:
    from twinkly_mockup.config import load_config

    cfg = load_config(CONFIGS / "monaco_massenet.yaml")
    mosaic = Mosaic.load(CONFIGS / "monaco_mosaic.yaml")
    frame = mosaic.sample(
        center_xy_m=(cfg.snapshot.x_m, cfg.snapshot.y_m),
        yaw_rad=cfg.snapshot.yaw_rad,
        viewport_m=cfg.viewport_m(),
        output_px=(320, 240),
    )
    assert hashlib.sha256(frame.tobytes()).hexdigest() == MONACO_MASSENET_SAMPLE_SHA256
