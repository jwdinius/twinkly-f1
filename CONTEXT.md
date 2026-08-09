# Context — twinkly-f1

Glossary of the domain language for the LEGO MCL39 + Twinkly Squares Monaco
display. Definitions only — no implementation details. When code or docs name
one of these concepts, use the term exactly as defined here.

## Terms

### Centerline
A single `LineString` of lat/lon vertices running down the *middle* of the
circuit. Source: bacinger/f1-circuits, one file per circuit under `configs/`,
bound to that circuit by the Circuit manifest. Used to derive snapshot poses
(nearest-vertex position + tangent yaw) and to seed the Track-limit KML. It is
**not** a track limit and is **not** the racing line.

### Circuit manifest
The single declaration of a circuit: `configs/circuits.json` binds a name to
its Mosaic, its Sidecar, its Centerline, and the constant half-width the traced
edges start out offset by. Adding a circuit means adding an entry — a circuit
is described nowhere else, and one that is absent from the manifest is not
traceable.

### Seed boundary
Both track edges as first generated: the Centerline's vertices offset, one for
one, along their angle-bisector normals at the circuit's constant half-width.
It is **not** a Track-limit KML. It is a constant-width oval, and a real
circuit is neither constant-width nor bounded by an offset of its middle — an
undragged seed is a fiction, and exporting one would hand the solver that
oval. What it *is* is a starting point that already has the two properties a
pair of hand-traced edges keeps getting wrong: one shared direction and one
shared index 0, inherited from the single Centerline both came from. A seed
becomes a Track-limit KML only by being dragged onto the painted lines.

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
pixels. Seven fields, all read off the warped UTM raster at mosaic-build time
and none hand-authored: `path` (the Mosaic PNG), `origin_px` (the raster's
center pixel, the anchor), `origin_lat` / `origin_lon` (that pixel unprojected
to WGS84), `m_per_px` (the geotransform's pixel size), `utm_epsg` (the raster's
SRS), and `north_angle_deg` / `grid_scale`. Because the raster is UTM its pixel
axes are *grid* east/north: `north_angle_deg` carries the grid convergence at
the origin and `grid_scale` the point scale factor, the two quantities that
separate grid axes from true east/north — see
[ADR-0004](docs/adr/0004-utm-grid-frame-in-sidecar.md).

### Optimal trajectory
The minimum-lap-time line + speed profile that `fastest-lap` computes for a
circuit given a vehicle model. Always call it the **optimal trajectory** — never
"fastest lap" — so it never reads as a *recorded* session best (real telemetry).
Recorded telemetry is out of scope for this phase. Consumed by the renderer as a
time-indexed `(t, x, y, yaw)` trajectory (mockup ENU frame, uniform `dt`).
