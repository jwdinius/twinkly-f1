#!/usr/bin/env bash
# Rebuild configs/silverstone_mosaic.png from satellite tiles (see
# build_monaco_mosaic.sh for the two-stage rationale). Silverstone is in
# UTM zone 30N (EPSG:32630).
set -euo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$PWD"
SCRATCH="${SCRATCH:-/tmp/silverstone_mosaic}"
mkdir -p "$SCRATCH"

# The download bbox is the centerline's extent plus MARGIN_M metres of apron,
# derived rather than hand-typed so it cannot drift from the GeoJSON.
MARGIN_M="${MARGIN_M:-100}"
CENTERLINE="configs/silverstone_centerline.geojson"
eval "$(uv run scripts/centerline_bbox.py "$CENTERLINE" \
    --margin-m "$MARGIN_M" --format shell)"
BBOX="$SW -> $NE"
echo "bbox from $CENTERLINE + ${MARGIN_M} m: $BBOX"

( cd "$SCRATCH" && uv run "$REPO_ROOT/submodules/racetrack-mosaic/racetrack_mosaic.py" \
    --sw "$SW" \
    --ne "$NE" \
    --gsd 25 --name silverstone --yes --quiet )
gdalwarp -t_srs EPSG:32630 -r bilinear -overwrite -of GTiff \
    "$SCRATCH/silverstone/silverstone.tif" "$SCRATCH/silverstone/silverstone_utm.tif"
gdal_translate -of PNG -ot Byte \
    "$SCRATCH/silverstone/silverstone_utm.tif" configs/silverstone_mosaic.png
echo "wrote configs/silverstone_mosaic.png ($(du -h configs/silverstone_mosaic.png | cut -f1))"

# The sidecar is read off the UTM raster's geotransform + SRS, never hand-typed
# (ADR-0004), so the Mosaic and its Sidecar are always in step.
uv run scripts/emit_mosaic_sidecar.py "$SCRATCH/silverstone/silverstone_utm.tif" \
    --png silverstone_mosaic.png \
    --out configs/silverstone_mosaic.yaml \
    --title "Silverstone GP circuit — top-down satellite mosaic." \
    --note "Built by scripts/build_silverstone_mosaic.sh: submodules/racetrack-mosaic fetches Google Satellite tiles for bbox $BBOX — $CENTERLINE's extent plus ${MARGIN_M} m on every side, derived by scripts/centerline_bbox.py — at a requested GSD of 25 cm/px, then gdalwarp reprojects to UTM Zone 30N for isotropic metres per pixel."
