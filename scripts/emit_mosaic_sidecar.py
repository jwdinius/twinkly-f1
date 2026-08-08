#!/usr/bin/env python3
"""Emit a mosaic Sidecar YAML from a warped UTM raster's geotransform + SRS.

ADR-0004 requires that `north_angle_deg` and `grid_scale` be computed at
mosaic-build time from the GDAL geotransform and SRS — never hand-typed. This
script is that step: `scripts/build_*_mosaic.sh` runs it right after
`gdal_translate`, so producing a Mosaic produces its Sidecar in the same breath.

Everything is read off the raster:

* `origin_px`  — the raster's exact geometric centre, `(width/2, height/2)`
* `m_per_px`   — the geotransform's pixel size (square + north-up is enforced)
* `origin_lat` / `origin_lon` — that centre pixel unprojected to WGS84
* `utm_epsg`   — the raster's SRS
* `north_angle_deg` / `grid_scale` — `pyproj`'s meridian convergence and point
  scale factor evaluated **at the origin**

**Sign convention.** `north_angle_deg` is `pyproj`'s `meridian_convergence` at
the origin, stored with no negation. ADR-0004 writes the same quantity as
`θ = −γ` against a `γ` column of the opposite sign, so read the number, not the
prose: the authority is `tests/test_frame_registration.py`, which recomputes θ
here and checks the projection against `pyproj` at the image corners, where a
flipped sign doubles the error instead of removing it.

GDAL is used through `gdalinfo -json` rather than the Python bindings, which are
not part of this project's dependency set (the CLI already is — the build
scripts shell out to `gdalwarp` and `gdal_translate`).

    scripts/emit_mosaic_sidecar.py /tmp/monaco/monaco_utm.tif \\
        --png monaco_mosaic.png --out configs/monaco_mosaic.yaml \\
        --title "Monaco GP circuit — top-down satellite mosaic for the layout sweep." \\
        --note "Google Satellite, bbox 43.7320,7.4170 -> 43.7415,7.4300, GSD 25 cm/px."
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pyproj

# The geotransform must be axis-aligned and square within this fraction of a
# pixel; `gdalwarp` to a UTM zone produces exactly that, and anything else means
# the "one isotropic m_per_px" the Sidecar schema assumes has stopped holding.
SQUARENESS_TOL = 1e-9


class EmitError(RuntimeError):
    """The raster cannot back a Sidecar."""


@dataclass(frozen=True)
class Registration:
    """Everything the Sidecar records, derived from one raster."""

    width: int
    height: int
    m_per_px: float
    epsg: int
    origin_px: tuple[float, float]
    origin_lat: float
    origin_lon: float
    north_angle_deg: float
    grid_scale: float


def gdalinfo(raster: Path) -> dict:
    try:
        out = subprocess.run(
            ["gdalinfo", "-json", str(raster)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except FileNotFoundError as e:  # pragma: no cover - environment problem
        raise EmitError("gdalinfo is not on PATH; install GDAL") from e
    except subprocess.CalledProcessError as e:
        raise EmitError(f"gdalinfo failed on {raster}: {e.stderr.strip()}") from e
    return json.loads(out)


def registration_from_gdalinfo(info: dict, raster: Path) -> Registration:
    """Derive the Sidecar's geometry from one `gdalinfo -json` payload."""
    gt = info.get("geoTransform")
    if gt is None:
        raise EmitError(
            f"{raster} carries no geotransform — is it the warped UTM raster "
            f"rather than the plain PNG?"
        )
    ox, sx, ry, oy, rx, sy = gt
    if abs(rx) > SQUARENESS_TOL or abs(ry) > SQUARENESS_TOL:
        raise EmitError(f"{raster} is rotated (geotransform skew {rx}, {ry}); expected north-up")
    if abs(sx + sy) > SQUARENESS_TOL * abs(sx) or sx <= 0.0 or sy >= 0.0:
        raise EmitError(
            f"{raster} has non-square or flipped pixels ({sx} x {sy}); the "
            f"Sidecar carries a single isotropic m_per_px"
        )

    epsg = (info.get("stac") or {}).get("proj:epsg")
    if epsg is None:
        raise EmitError(f"{raster} has no EPSG-identifiable SRS; warp it to a UTM zone first")
    crs = pyproj.CRS.from_epsg(int(epsg))
    if not crs.is_projected:
        raise EmitError(f"{raster} is in geographic CRS EPSG:{epsg}; warp it to a UTM zone first")

    width, height = (int(v) for v in info["size"])
    # The geotransform addresses pixel *corners*, so (w/2, h/2) is the raster's
    # exact geometric centre — the anchor every shipped Sidecar uses.
    origin_px = (width / 2.0, height / 2.0)
    east = ox + origin_px[0] * sx
    north = oy + origin_px[1] * sy
    lon, lat = pyproj.Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(east, north)

    factors = pyproj.Proj(crs).get_factors(lon, lat)
    # Transverse Mercator is conformal, so the two axis scales coincide; if they
    # ever don't, a single `grid_scale` is the wrong model and we should know.
    if not math.isclose(factors.meridional_scale, factors.parallel_scale, rel_tol=1e-9):
        raise EmitError(
            f"EPSG:{epsg} is not conformal at the origin "
            f"({factors.meridional_scale} vs {factors.parallel_scale}); "
            f"a single grid_scale cannot represent it"
        )

    return Registration(
        width=width,
        height=height,
        m_per_px=float(sx),
        epsg=int(epsg),
        origin_px=origin_px,
        origin_lat=float(lat),
        origin_lon=float(lon),
        north_angle_deg=float(factors.meridian_convergence),
        grid_scale=float(factors.meridional_scale),
    )


def render_sidecar(reg: Registration, png: str, title: str, notes: list[str]) -> str:
    """Render the Sidecar YAML, header comment included."""
    lines = [f"# {title}"] if title else []
    for note in notes:
        lines += [f"# {line}" for line in textwrap.wrap(note, 74)]
    lines += [
        "#",
        "# GENERATED by scripts/emit_mosaic_sidecar.py from the warped UTM raster's",
        "# geotransform and SRS (ADR-0004) — do not hand-edit; rebuild instead.",
        f"# Raster: {reg.width} x {reg.height} px, EPSG:{reg.epsg}, origin at the centre pixel.",
        "# north_angle_deg is pyproj's meridian convergence at the origin and grid_scale",
        "# its point scale factor: together they carry the rotation and scale between",
        "# true east/north (ENU) and the raster's UTM grid axes.",
        f"path: {png}",
        f"origin_lat: {reg.origin_lat:.9f}",
        f"origin_lon: {reg.origin_lon:.9f}",
        f"origin_px: [{reg.origin_px[0]!r}, {reg.origin_px[1]!r}]",
        f"m_per_px: {reg.m_per_px:.9f}",
        f"north_angle_deg: {reg.north_angle_deg:.9f}",
        f"utm_epsg: {reg.epsg}",
        f"grid_scale: {reg.grid_scale:.9f}",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("raster", type=Path, help="the warped UTM raster (GeoTIFF)")
    p.add_argument("--png", required=True, help="`path:` value — the mosaic PNG, relative to the sidecar")
    p.add_argument("--out", required=True, type=Path, help="sidecar YAML to write")
    p.add_argument("--title", default="", help="first header comment line")
    p.add_argument("--note", action="append", default=[], help="extra header comment paragraph (repeatable)")
    args = p.parse_args(argv)

    reg = registration_from_gdalinfo(gdalinfo(args.raster), args.raster)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_sidecar(reg, args.png, args.title, args.note))
    print(
        f"wrote {args.out} — EPSG:{reg.epsg}, {reg.m_per_px:.6f} m/px, "
        f"θ={reg.north_angle_deg:+.6f}°, k={reg.grid_scale:.9f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
