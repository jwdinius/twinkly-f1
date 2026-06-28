#!/usr/bin/env bash
# 3 × 3 sizing sweep: every Monaco snapshot × every layout candidate.
# Produces 9 PNGs in out/sweep/ named <snapshot>__<layout>.png. Open them
# side-by-side to pick the layout to buy. See docs/prd/0001-mvp-monaco-mockup.md.
set -euo pipefail

cd "$(dirname "$0")/.."

uv run twinkly-mockup render-all \
    configs/monaco_massenet.yaml \
    configs/monaco_loews.yaml \
    configs/monaco_tabac.yaml \
    --layout configs/layout_tight.yaml \
    --layout configs/layout_standard.yaml \
    --layout configs/layout_cinematic.yaml \
    --out out/sweep
