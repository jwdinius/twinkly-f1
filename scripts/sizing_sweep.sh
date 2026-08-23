#!/usr/bin/env bash
# 3 × 3 sizing sweep: every Silverstone snapshot × every layout candidate.
# Produces 9 PNGs in out/sweep/ named <snapshot>__<layout>.png. Open them
# side-by-side to pick the layout to buy. See docs/prd/0001-mvp-monaco-mockup.md.
#
# The three snapshots are picked by crop contrast, not by corner reputation:
# Silverstone's 7 m half-width puts both painted edges outside the LEGO-scale
# 12x8 m viewport almost everywhere, so a centerline-centred sample is bare
# asphalt (median contrast σ=2.9 across the lap, against Monaco's 35.4). Club,
# Luffield and Abbey are the three places with anything in frame — see the
# rationale in scripts/derive_corner_poses.py.
set -euo pipefail

cd "$(dirname "$0")/.."

uv run twinkly-mockup render-all \
    configs/silverstone_club.yaml \
    configs/silverstone_luffield.yaml \
    configs/silverstone_abbey.yaml \
    --layout configs/layout_tight.yaml \
    --layout configs/layout_standard.yaml \
    --layout configs/layout_cinematic.yaml \
    --out out/sweep
