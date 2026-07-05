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
mode). Hand-digitized in Google Earth Pro over a registered imagery backdrop.
Distinct from the Centerline.

### Mosaic
A geo-registered raster satellite image of the circuit. Two roles: (1) the
**backdrop** traced over to author the Track-limit KML, and (2) the image
source the LED renderer crops/rotates/downsamples per frame. Produced raster,
never vector — it carries no track geometry.

### Backdrop
The EPSG:4326 KMZ (SuperOverlay) that racetrack-mosaic emits for dropping into
Google Earth Pro. Its sole role is to be traced over when authoring the
Track-limit KML. Contrast the display **Mosaic**, which is UTM/metric.

### Sidecar
The registration record the LED renderer needs to project ENU meters ↔ mosaic
pixels: `origin_lat`, `origin_lon`, `origin_px`, `m_per_px`, `north_angle_deg`.
Emitted directly by racetrack-mosaic from the UTM raster's geotransform (center
pixel as origin anchor) — no longer hand-authored.

### Optimal trajectory
The minimum-lap-time line + speed profile that `fastest-lap` computes for a
circuit given a vehicle model. Always call it the **optimal trajectory** — never
"fastest lap" — so it never reads as a *recorded* session best (real telemetry).
Recorded telemetry is out of scope for this phase. Consumed by the renderer as a
time-indexed `(t, x, y, yaw)` trajectory (mockup ENU frame, uniform `dt`).
