#!/usr/bin/env bash
# Rebuild configs/monaco_mosaic.png from satellite tiles.
#
# Two-stage pipeline:
#   1. submodules/racetrack-mosaic fetches Google Satellite tiles for a bbox
#      covering the full Monte Carlo F1 circuit and warps them into an
#      EPSG:4326 GeoTIFF.
#   2. gdalwarp reprojects to UTM Zone 32N (Monaco's zone) so the PNG has
#      isotropic meters-per-pixel — required by Mosaic's single m_per_px
#      sidecar field. gdal_translate then dumps the UTM raster to PNG.
#
# The sidecar configs/monaco_mosaic.yaml encodes the geo-registration that
# pairs with the PNG (image center → origin lat/lon, UTM pixel size as
# m_per_px). If you re-bbox or re-GSD the build, re-derive the sidecar.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$PWD"

SCRATCH="${SCRATCH:-/tmp/monaco_mosaic}"
mkdir -p "$SCRATCH"

# Stage 1: tile fetch + warp to EPSG:4326 GeoTIFF.
( cd "$SCRATCH" && uv run "$REPO_ROOT/submodules/racetrack-mosaic/racetrack_mosaic.py" \
    --sw 43.7320,7.4170 \
    --ne 43.7415,7.4300 \
    --gsd 25 \
    --name monaco \
    --quiet )

# Stage 2: reproject to UTM 32N + dump PNG.
gdalwarp -t_srs EPSG:32632 -r bilinear -overwrite -of GTiff \
    "$SCRATCH/monaco/monaco.tif" "$SCRATCH/monaco/monaco_utm.tif"
gdal_translate -of PNG -ot Byte \
    "$SCRATCH/monaco/monaco_utm.tif" configs/monaco_mosaic.png

echo "wrote configs/monaco_mosaic.png ($(du -h configs/monaco_mosaic.png | cut -f1))"
echo "sidecar: configs/monaco_mosaic.yaml — re-derive origin_px / m_per_px if bbox or GSD changed."
