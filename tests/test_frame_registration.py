"""Frame registration: the sidecar's UTM grid convergence and scale (ADR-0004).

Mosaics are reprojected to UTM so the PNG has isotropic metres per pixel, which
makes the pixel axes UTM **grid** east/north — but ENU offsets are **true**
east/north. `MosaicSidecar.north_angle_deg` and `grid_scale` carry the rotation
and scale between the two.

`pyproj` is the authority here, deliberately. A flipped θ *doubles* the
registration error instead of removing it and looks entirely plausible at every
zoom level — which is how the original `north_angle_deg: 0.0` survived a full
extraction run, a debug overlay, and an export. So the sign is pinned by
evaluating the projection at points near the image corners, where a flip is
unmissable, and `test_flipped_convergence_sign_breaks_agreement` proves the
fixture has teeth.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np
import pytest
import yaml

from twinkly_mockup.mosaic import Mosaic, MosaicSidecar

pyproj = pytest.importorskip("pyproj")

CONFIGS = Path(__file__).resolve().parent.parent / "configs"

# Shipped sidecars bound to the UTM zone their mosaic was warped into. The
# EPSG lives here rather than in the YAMLs because this slice deliberately does
# not touch any shipped sidecar's values — re-registering them is the next one.
CIRCUITS = [
    ("monaco_mosaic.yaml", 32632),  # UTM 32N
    ("silverstone_mosaic.yaml", 32630),  # UTM 30N
]


def _load_shipped(name: str) -> MosaicSidecar:
    return MosaicSidecar.model_validate(yaml.safe_load((CONFIGS / name).read_text()))


def _registered(sidecar: MosaicSidecar, epsg: int, *, flip: bool = False) -> Mosaic:
    """The same sidecar, re-registered from `pyproj` instead of hand-typed.

    θ is the meridian convergence at the origin and `grid_scale` the point scale
    factor of the projection there. `flip` negates θ to exercise the sign check;
    the image is a stub because projection never touches pixels.
    """
    factors = pyproj.Proj(pyproj.CRS.from_epsg(epsg)).get_factors(
        sidecar.origin_lon, sidecar.origin_lat
    )
    theta = -factors.meridian_convergence if flip else factors.meridian_convergence
    registered = sidecar.model_copy(
        update={
            "utm_epsg": epsg,
            "north_angle_deg": theta,
            "grid_scale": factors.meridional_scale,
        }
    )
    return Mosaic(np.zeros((2, 2, 3), dtype=np.uint8), registered)


def _image_corner_enu(sidecar: MosaicSidecar) -> list[tuple[float, float]]:
    """The four image corners as ENU metres from the origin.

    Both shipped sidecars anchor the origin at the raster's centre pixel, so the
    half-extent in metres is just `origin_px * m_per_px`.
    """
    half_e = sidecar.origin_px[0] * sidecar.m_per_px
    half_n = sidecar.origin_px[1] * sidecar.m_per_px
    return [(se * half_e, sn * half_n) for se in (-1.0, 1.0) for sn in (-1.0, 1.0)]


def _grid_offsets(
    sidecar: MosaicSidecar, epsg: int, enu: list[tuple[float, float]]
) -> np.ndarray:
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
    to_utm = pyproj.Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    east0, north0 = to_utm.transform(sidecar.origin_lon, sidecar.origin_lat)
    east, north = to_utm.transform(lons, lats)
    return np.column_stack([east - east0, north - north0])


def _px_error_m(mosaic: Mosaic, enu: list[tuple[float, float]], grid: np.ndarray) -> np.ndarray:
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


@pytest.mark.parametrize(("name", "epsg"), CIRCUITS)
def test_shipped_sidecars_still_load(name: str, epsg: int) -> None:
    """This slice changes the schema, not any shipped value."""
    sidecar = _load_shipped(name)
    assert sidecar.grid_scale == 1.0
    assert sidecar.utm_epsg is None


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


@pytest.mark.parametrize(("name", "epsg"), CIRCUITS)
def test_projection_matches_pyproj_at_image_corners(name: str, epsg: int) -> None:
    """At the image corners the convergence has its longest lever arm."""
    sidecar = _load_shipped(name)
    corners = _image_corner_enu(sidecar)
    err = _px_error_m(
        _registered(sidecar, epsg), corners, _grid_offsets(sidecar, epsg, corners)
    )
    assert err.max() < 0.05, f"{name}: worst corner error {err.max():.4f} m"


@pytest.mark.parametrize(("name", "epsg"), CIRCUITS)
def test_flipped_convergence_sign_breaks_agreement(name: str, epsg: int) -> None:
    """The fixture has teeth: a flipped θ doubles the error, it does not cancel.

    Passing under both signs would mean the test pins nothing — which is exactly
    the failure mode that let `north_angle_deg: 0.0` ship.
    """
    sidecar = _load_shipped(name)
    corners = _image_corner_enu(sidecar)
    grid = _grid_offsets(sidecar, epsg, corners)
    flipped = _px_error_m(_registered(sidecar, epsg, flip=True), corners, grid)
    assert flipped.max() > 10.0, f"{name}: flipped θ only cost {flipped.max():.4f} m"
    # Roughly twice the uncorrected error — the signature of a sign flip rather
    # than of some unrelated drift.
    uncorrected = _px_error_m(
        Mosaic(np.zeros((2, 2, 3), dtype=np.uint8), sidecar), corners, grid
    )
    assert flipped.max() == pytest.approx(2.0 * uncorrected.max(), rel=0.05)


@pytest.mark.parametrize(("name", "epsg"), CIRCUITS)
def test_affine_residual_over_full_image_is_subpixel(name: str, epsg: int) -> None:
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
    err = _px_error_m(
        _registered(sidecar, epsg), samples, _grid_offsets(sidecar, epsg, samples)
    )
    assert err.max() < sidecar.m_per_px, (
        f"{name}: residual {err.max():.4f} m exceeds one pixel ({sidecar.m_per_px:.4f} m)"
    )


@pytest.mark.parametrize(("name", "epsg"), CIRCUITS)
def test_spherical_flat_enu_is_the_remaining_registration_error(
    name: str, epsg: int
) -> None:
    """Characterisation, not endorsement — pins an error this slice does not fix.

    `centerline.py` / `import_lap.py` build ENU as `(lon−lon0)·cos(lat0)·a` and
    `(lat−lat0)·a`, which stretches east and squeezes north by ~±0.17% relative
    to true distances. That anisotropy is not a rotation-plus-scale, so it
    survives ADR-0004 intact: ~1.4 m at the image corners, versus the ~30 m the
    convergence correction removes. Well inside a 12 m track width, but no
    longer negligible next to the sub-pixel claim above.
    """
    sidecar = _load_shipped(name)
    corners = _image_corner_enu(sidecar)
    earth_radius_m = 6_378_137.0
    to_utm = pyproj.Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    east0, north0 = to_utm.transform(sidecar.origin_lon, sidecar.origin_lat)
    cos_lat0 = math.cos(math.radians(sidecar.origin_lat))
    e = np.array([p[0] for p in corners])
    n = np.array([p[1] for p in corners])
    east, north = to_utm.transform(
        sidecar.origin_lon + np.degrees(e / (earth_radius_m * cos_lat0)),
        sidecar.origin_lat + np.degrees(n / earth_radius_m),
    )
    err = _px_error_m(
        _registered(sidecar, epsg),
        corners,
        np.column_stack([east - east0, north - north0]),
    )
    assert 0.5 < err.max() < 3.0, f"{name}: spherical-ENU residual {err.max():.4f} m"


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


# --- no shipped output moves -------------------------------------------------

MONACO_MASSENET_SAMPLE_SHA256 = (
    "f7a0c2c19f216fdd48fb43c4bed78adcc6ba45de86cafb3d7fb7962f6e3d4c94"
)


@pytest.mark.skipif(
    not (CONFIGS / "monaco_mosaic.png").exists(),
    reason="monaco_mosaic.png is build output (gitignored); rebuild via scripts/build_monaco_mosaic.sh",
)
def test_monaco_snapshot_sample_is_byte_identical() -> None:
    """Schema and math changed; shipped sidecar values did not, so nothing moves.

    The digest was captured from the Massenet snapshot before ADR-0004 landed.
    """
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
