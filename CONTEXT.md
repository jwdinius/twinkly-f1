# Context — twinkly-f1

Glossary of the domain language for the LEGO MCL39 + Twinkly Squares circuit
display. Definitions only — no implementation details. When code or docs name
one of these concepts, use the term exactly as defined here.

## The circuit under validation

**Silverstone.** Monaco was the original target and is retained only as a
regression fixture — a second circuit that keeps the Circuit manifest honest.
It is not a validation target and no further work goes into it: Monaco runs
through the Portier-to-Tabac tunnel, and a Mosaic is satellite imagery, so a
fifth of the lap is roof. There is no Mosaic of a road under a building, which
means the LED renderer cannot show that stretch and the Track-limit KML cannot
be traced across it.

Silverstone costs something in exchange. It is 5.89 km against Monaco's 3.34 km
and roughly 15 m wide against Monaco's 10 m, so at the LEGO-scale viewport both
of its painted edges fall outside the crop nearly everywhere and a snapshot
centred on the Centerline samples bare asphalt. Measured over every Centerline
vertex, crop contrast has median σ=2.9 against Monaco's σ=35.4. Corner picks for
the layout sweep are therefore made by contrast, not by corner reputation.

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

Its **download region** is derived, not chosen: `scripts/centerline_bbox.py`
takes the Centerline's own lat/lon extent and grows it by a margin in metres
(100 m by default, `MARGIN_M` in the build scripts). That is the definition of
"big enough" here — every Centerline vertex inside the imagery with room for
the track limits and run-off — and of "no bigger", since imagery the Centerline
never reaches is tiles fetched and warped for nothing. A hand-typed bbox drifts
from the GeoJSON silently; a derived one cannot.

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

### Native frame
`fastest-lap`'s own local-Cartesian frame, the one every solver artifact is
expressed in: **`x` east, `y` south, `z` down**, anchored at the solver's own
origin with its own earth radius, all three recorded in the circuit XML's
`GPS_parameters`. Yaw is CCW about that down axis — which is *clockwise* seen
from above.

It is **not** the Mockup ENU frame, and the difference is a north–south mirror
plus a reversed sense of yaw, not a scale. Say which frame a coordinate is in
whenever both are in play: an unqualified "x, y" is what let the two be
conflated once already, mirroring every imported lap about the solver's origin
latitude. Nothing but `import-lap` may speak this frame; it is the last thing
crossed out of before a trajectory reaches the renderer.

### Mockup ENU frame
The frame everything downstream of `import-lap` uses: metres east and north of
the Sidecar's origin, yaw CCW from `+x` (east). The Trajectory, the snapshot
poses, and the renderer are all in it. Distinct from the Native frame above and
from the Sidecar's *grid* east/north, which the UTM convergence separates from
true east/north (ADR-0004).

### Optimal trajectory
The minimum-lap-time line + speed profile that `fastest-lap` computes for a
circuit given a vehicle model. Always call it the **optimal trajectory** — never
"fastest lap" — so it never reads as a *recorded* session best (real telemetry).
Recorded telemetry is out of scope for this phase. Consumed by the renderer as a
time-indexed `(t, x, y, yaw)` trajectory (Mockup ENU frame, uniform `dt`).

### Lap sequence
The wall rendered as a *movie* of the optimal trajectory rather than a still.
The LEGO car is physically mounted and never moves, so the **camera is rigidly
attached to the car** and the track translates and rotates beneath it — the
fixed-car, moving-world view. A lap sequence is the still-frame path
(`compose.compose_frame`) driven over every sample of a Trajectory; sampling
rate and playback rate are the same number, so playback is real time. Say "lap
sequence", not "animation", to keep it tied to a specific trajectory.

### Camera yaw
The heading the mosaic sampler puts on the frame's image-**up** axis — *not*
the car's heading. The car silhouette is drawn nose-up then rotated CCW by
`car.orientation_deg`, so its nose points along `camera_yaw + orientation_deg`.
Aiming the nose along a trajectory heading therefore needs
`camera_yaw = heading − orientation_deg` (`sequence.camera_yaw_for_heading`),
which for the shipped `-90°` mounting is the familiar `heading + π/2`. Both the
still-frame authoring script and the lap sequence go through that one function;
stating the bias twice is how a mirrored lap gets in.

### Wall view scale
The viewport is **derived, not chosen**: `WALL_TILE_VIEW_M = TILE_PITCH_M /
LEGO_SCALE ≈ 1.344 m` per tile, so the wall shares the LEGO car's 1:8.4 scale
and the asphalt around the model is geometrically consistent with the model.
The standard 9×6 layout therefore sees ~12.1 × 8.1 m of track. The known cost:
Silverstone's half-width is 7 m, so a racing-line-centred crop is often bare
asphalt with both painted edges outside the view — about a fifth of the optimal
lap. That is a consequence of the scale constraint, not a rendering fault.

`snapshot.viewport_m` overrides the derivation, trading scale fidelity for
coverage. Measure that trade in **ground sample distance** — the track distance
one LED covers, `viewport_width / led_count` (0.224 m at the derived scale).
Doubling GSD halves the effective scale to 1:16.8, so the mounted model reads
~2× oversized, and it halves the car's footprint in tiles: the 4×2 cutout
becomes 2×1. Viewport, cutout, and `car.dimensions_cm` all move together — a
config that changes one without the others puts a wrongly-sized silhouette on
the wall. `configs/silverstone_lap_double_gsd.yaml` is the worked example.
