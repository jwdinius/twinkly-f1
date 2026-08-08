# Context — twinkly-f1

Glossary of the domain language for the LEGO MCL39 + Twinkly Squares Monaco
display. Definitions only — no implementation details. When code or docs name
one of these concepts, use the term exactly as defined here.

## Terms

### Centerline
A single `LineString` of lat/lon vertices running down the *middle* of the
circuit. Source: bacinger/f1-circuits (`configs/monaco_centerline.geojson`).
Used to derive snapshot poses (nearest-vertex position + tangent yaw). It is
**not** a track limit and is **not** the racing line.

### Track-limit KML
A pair of lat/lon `LineString`s — one **left** edge, one **right** edge —
each tracing a *physical* track boundary (white line / Armco). This is the
input `fastest-lap`'s circuit preprocessor requires (it has no centerline-only
mode). Authored in the in-repo tracer (`scripts/trace_track_limits.html`) over
the Mosaic — see [ADR-0003](docs/adr/0003-centerline-seeded-track-limits.md).
Distinct from the Centerline.

### Mosaic
A geo-registered raster satellite image of the circuit. Two roles: (1) the
image the tracer displays when authoring the Track-limit KML, and (2) the image
source the LED renderer crops/rotates/downsamples per frame. Produced raster,
never vector — it carries no track geometry.

### Sidecar
The registration record the LED renderer needs to project ENU meters ↔ mosaic
pixels: `origin_lat`, `origin_lon`, `origin_px`, `m_per_px`, `north_angle_deg`,
`utm_epsg`, `grid_scale`. Emitted directly by racetrack-mosaic from the UTM
raster's geotransform and SRS (center pixel as origin anchor) — no longer
hand-authored. Because the raster is UTM, its pixel axes are *grid* east/north:
`north_angle_deg` carries the grid convergence and `grid_scale` the point scale
factor that separate them from true east/north — see
[ADR-0004](docs/adr/0004-utm-grid-frame-in-sidecar.md).

### Optimal trajectory
The minimum-lap-time line + speed profile that `fastest-lap` computes for a
circuit given a vehicle model. Always call it the **optimal trajectory** — never
"fastest lap" — so it never reads as a *recorded* session best (real telemetry).
Recorded telemetry is out of scope for this phase. Consumed by the renderer as a
time-indexed `(t, x, y, yaw)` trajectory (mockup ENU frame, uniform `dt`).
