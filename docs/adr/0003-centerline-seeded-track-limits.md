# Track limits are authored in-repo from a centerline-seeded boundary

## Status

accepted

## Context

`fastest-lap`'s `circuit_preprocessor` needs two lat/lon `LineString`s — one per
physical track edge. The documented workflow
(`submodules/racetrack-mosaic/docs/tracing-track-limits.md` §2) is to hand-trace
both in Google Earth Pro over a KMZ SuperOverlay. That doc's own troubleshooting
table names the two worst failure modes: edges traced in **opposite directions**,
and edges starting at **far-apart points** on the lap. Both warp the reference
line the optimizer fits, both are invisible until the solver runs, and both are
inherent to tracing two polylines independently by hand.

An automated alternative was prototyped and rejected: marching normals outward
from the centerline until sustained green (asphalt-vs-grass) detects the *paved
run-off* boundary rather than the painted limit at precisely the corners where
precision matters — its own docstring conceded this — and it emitted ~2,900
vertices per edge, an editing surface nobody can drag.

Meanwhile `bacinger/f1-circuits` already ships centerlines whose vertices are
**dense through corners and sparse on straights** (135 at Silverstone, 160 at
Monaco) — the exact distribution the optimizer wants and the tracing doc asks a
human to reproduce by hand.

## Decision

Author track limits in an in-repo web app (`scripts/trace_track_limits.html`,
served by `scripts/trace.py`), not in Google Earth Pro. Circuits come from a
`configs/circuits.json` manifest that binds each mosaic to its centerline.

Seed each edge by offsetting the centerline's vertices **as-is** — one seed
vertex per source vertex — along miter (angle-bisector) normals at a constant
per-circuit half-width, clamped to 3× half-width at hairpins. Refine by
**dragging only**: move a vertex, insert one at a segment's midpoint on
double-click, delete on right-click. There is no append. Working state persists
as lon/lat; export writes KML straight into `configs/`.

## Consequences

- Both edges inherit the source centerline's direction and its index 0, so the
  two failure modes above are eliminated **by construction** rather than by
  checklist.
- Vertex density is inherited for free, and stays small enough (~135) that every
  vertex is reachable by hand — the property the rejected extractor destroyed.
- **Backdrop** leaves the domain language: this repo already builds PNG + Sidecar
  (`gdalwarp` → `gdal_translate`) and never consumed the KMZ. **Seed boundary**
  enters it, defined explicitly as *not* a track limit — an undragged seed is a
  fiction, and exporting one would feed the solver a constant-width oval.
- Where the offset self-intersects at tight corners, the app detects and
  highlights it; it does not repair it. That is the operator's job.
- KML import is retained as the only durable resume path, since localStorage is
  the working store and a cache clear would otherwise discard hours of work.
- `racetrack-mosaic` keeps its Google Earth path unchanged — it is a
  general-purpose tool and its users may have no centerline prior. It gains one
  cross-reference line pointing here.
- Centerline geometry is MIT-licensed © Tomislav Bacinger; attribution appears
  in-app, per-manifest-entry, and as a comment in every exported KML — the copy
  most likely to be separated from the license.
