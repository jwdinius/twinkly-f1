"""Mosaic: geo-registered satellite image with ENU↔pixel projection.

Coordinate frame is local Cartesian (ENU) meters: `+x` east, `+y` north,
`yaw` in radians CCW from `+x`. This single frame is shared with the trajectory
schema and every snapshot config — see CLAUDE.md / the MVP PRD.

A `Mosaic` is loaded from a sidecar YAML emitted by racetrack-mosaic from the
warped UTM raster's geotransform and SRS. Because the raster is UTM, its pixel
axes are grid east/north, while ENU offsets are *true* east/north; the two
differ by the grid convergence and the point scale factor. `north_angle_deg`
and `grid_scale` carry that difference, and `utm_epsg` records the projection
they were derived in so it can be recomputed and checked — see ADR-0004.
"""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field

from .enu import FlatEnu

CLIP_VALUE: tuple[int, int, int] = (0, 0, 0)


class MosaicSidecar(BaseModel):
    """Geo-registration for a mosaic PNG, derived from its UTM geotransform.

    `north_angle_deg` is the rotation from true east/north to the raster's grid
    axes and `grid_scale` is the point scale factor of that projection at the
    origin. Both are read straight off `pyproj`'s factors at the origin —
    `meridian_convergence` and `meridional_scale`, no negation — which is the
    convention `tests/test_frame_registration.py` pins, because the sign is not
    safely settled by prose. The defaults (0°, unit scale) mean
    "treat pixel axes as true east/north", which is how every sidecar behaved
    before ADR-0004 — so a sidecar that omits them loads and renders unchanged.
    """

    model_config = ConfigDict(extra="forbid")

    path: Path
    origin_lat: float = Field(ge=-90.0, le=90.0)
    origin_lon: float = Field(ge=-180.0, le=180.0)
    origin_px: tuple[float, float]
    m_per_px: float = Field(gt=0.0)
    north_angle_deg: float = 0.0
    utm_epsg: int | None = None
    grid_scale: float = Field(default=1.0, gt=0.0)


class Mosaic:
    """Geo-registered satellite mosaic.

    Exposes ENU↔mosaic-pixel projection and an oriented `sample` that crops +
    rotates around a snapshot point for downstream `LedGrid` consumption.
    """

    def __init__(self, image: np.ndarray, sidecar: MosaicSidecar) -> None:
        self._image = image
        self._sidecar = sidecar
        theta = math.radians(sidecar.north_angle_deg)
        self._cos_theta = math.cos(theta)
        self._sin_theta = math.sin(theta)
        self._grid_scale = sidecar.grid_scale
        self._enu = FlatEnu.at(sidecar.origin_lat, sidecar.origin_lon)

    @classmethod
    def load(cls, sidecar_path: Path) -> "Mosaic":
        sidecar_path = Path(sidecar_path)
        with sidecar_path.open("r") as f:
            raw = yaml.safe_load(f)
        if not isinstance(raw, dict):
            raise ValueError(
                f"sidecar at {sidecar_path} must be a YAML mapping, "
                f"got {type(raw).__name__}"
            )
        sidecar = MosaicSidecar.model_validate(raw)
        png_path = (
            sidecar.path
            if sidecar.path.is_absolute()
            else sidecar_path.parent / sidecar.path
        )
        bgr = cv2.imread(str(png_path), cv2.IMREAD_COLOR)
        if bgr is None:
            raise FileNotFoundError(f"could not read mosaic PNG at {png_path}")
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        return cls(rgb, sidecar)

    @property
    def sidecar(self) -> MosaicSidecar:
        return self._sidecar

    @property
    def image(self) -> np.ndarray:
        return self._image

    @property
    def enu(self) -> FlatEnu:
        """The flat-ENU frame this mosaic's metre coordinates are expressed in."""
        return self._enu

    def lonlat_to_px(self, lon: float, lat: float) -> tuple[float, float]:
        """Project WGS84 lon/lat straight to mosaic pixel coords.

        The full path — flat-ENU about the sidecar origin, then the grid
        rotation and scale of `meters_to_px`. This is the composition the tracer
        reimplements in JavaScript and the golden fixture pins (ADR-0004).
        """
        e, n = self._enu.to_enu(lat, lon)
        return self.meters_to_px((float(e), float(n)))

    def px_to_lonlat(self, px: tuple[float, float]) -> tuple[float, float]:
        """Inverse of `lonlat_to_px`, returning `(lon, lat)` in degrees."""
        lon, lat = self._enu.to_lonlat(*self.px_to_meters(px))
        return float(lon), float(lat)

    def meters_to_px(self, xy_m: tuple[float, float]) -> tuple[float, float]:
        """Project an ENU position (meters from origin) to mosaic pixel coords.

        True-ENU offsets are rotated by θ and scaled by `grid_scale` into the
        raster's UTM grid axes before the pixel divide (ADR-0004).
        """
        e, n = xy_m
        k = self._grid_scale
        dx = k * (e * self._cos_theta - n * self._sin_theta)
        dy = k * (-e * self._sin_theta - n * self._cos_theta)
        ox, oy = self._sidecar.origin_px
        m = self._sidecar.m_per_px
        return ox + dx / m, oy + dy / m

    def px_to_meters(self, px: tuple[float, float]) -> tuple[float, float]:
        """Project a mosaic pixel back to ENU meters.

        Exact inverse of `meters_to_px`: the θ rotation matrix is its own
        inverse (a reflection), so only `grid_scale` needs undoing.
        """
        px_x, px_y = px
        ox, oy = self._sidecar.origin_px
        m = self._sidecar.m_per_px
        k = self._grid_scale
        dx = (px_x - ox) * m / k
        dy = (px_y - oy) * m / k
        e = self._cos_theta * dx - self._sin_theta * dy
        n = -self._sin_theta * dx - self._cos_theta * dy
        return e, n

    def sample(
        self,
        center_xy_m: tuple[float, float],
        yaw_rad: float,
        viewport_m: tuple[float, float],
        output_px: tuple[int, int],
    ) -> np.ndarray:
        """Sample an oriented crop around `center_xy_m`.

        Output is shaped `(output_px[1], output_px[0], 3)` uint8 RGB. The
        output image-up axis is aligned with the car's heading (`yaw_rad` CCW
        from `+x`), so the car appears stationary and the world rotates beneath
        it. Out-of-mosaic destination pixels are filled with `CLIP_VALUE`.
        """
        cx, cy = center_xy_m
        view_w_m, view_h_m = viewport_m
        out_w, out_h = output_px
        m_per_out_x = view_w_m / out_w
        m_per_out_y = view_h_m / out_h
        cos_y, sin_y = math.cos(yaw_rad), math.sin(yaw_rad)
        cos_t, sin_t = self._cos_theta, self._sin_theta
        ox, oy = self._sidecar.origin_px
        # ENU metres → mosaic pixels: scale into UTM grid metres, then divide.
        px_per_m = self._grid_scale / self._sidecar.m_per_px

        # Output pixel (u, v) maps to ENU via:
        #   u_m       =  (u + 0.5 - out_w/2) * m_per_out_x   (right axis)
        #   v_m_up    =  (out_h/2 - v - 0.5) * m_per_out_y   (up axis)
        #   e_off     =  u_m * sin yaw + v_m_up * cos yaw
        #   n_off     = -u_m * cos yaw + v_m_up * sin yaw
        # then ENU→mosaic-px via meters_to_px. Composed into a single 2×3 dst→src
        # affine for cv2.warpAffine (with WARP_INVERSE_MAP).
        de_du = sin_y * m_per_out_x
        de_dv = -cos_y * m_per_out_y
        dn_du = -cos_y * m_per_out_x
        dn_dv = -sin_y * m_per_out_y

        dsx_du = (cos_t * de_du - sin_t * dn_du) * px_per_m
        dsx_dv = (cos_t * de_dv - sin_t * dn_dv) * px_per_m
        dsy_du = (-sin_t * de_du - cos_t * dn_du) * px_per_m
        dsy_dv = (-sin_t * de_dv - cos_t * dn_dv) * px_per_m

        u0 = 0.5 - out_w / 2.0
        v0 = out_h / 2.0 - 0.5
        e0 = cx + u0 * m_per_out_x * sin_y + v0 * m_per_out_y * cos_y
        n0 = cy - u0 * m_per_out_x * cos_y + v0 * m_per_out_y * sin_y
        sx0 = ox + (cos_t * e0 - sin_t * n0) * px_per_m
        sy0 = oy + (-sin_t * e0 - cos_t * n0) * px_per_m

        affine = np.array(
            [[dsx_du, dsx_dv, sx0], [dsy_du, dsy_dv, sy0]],
            dtype=np.float64,
        )
        return cv2.warpAffine(
            self._image,
            affine,
            (out_w, out_h),
            flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=list(CLIP_VALUE),
        )
