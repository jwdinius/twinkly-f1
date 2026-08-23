#!/usr/bin/env bash
# The project's test command — everything CI runs, in one place.
#
# Two suites, because the mosaic frame transform has two implementations
# (ADR-0004): Python for the renderer and JavaScript for the tracer, bound by a
# Python-generated golden fixture. Running only one of them would let the pair
# drift, which is the exact failure the fixture exists to prevent — so they live
# behind a single command rather than relying on anyone remembering the second.
#
#   scripts/check.sh              # both suites
#   scripts/check.sh -k centerline  # extra args go to pytest
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== python =="
uv run pytest "$@"

echo
echo "== javascript =="
node --test tests/js/*.test.js
