# The mosaic sidecar frame carries UTM grid convergence and scale

## Status

accepted

## Context

Mosaics are reprojected to UTM (`gdalwarp -t_srs EPSG:326xx`) so the PNG has
isotropic metres per pixel — the property the LED renderer's crops and the track
geometry both rely on. That makes the pixel axes UTM **grid** east/north.

Every consumer, however, converted lon/lat with a flat equirectangular model —
`(lon − lon0)·cos(lat0)·R` — which assumes pixel axes are **true** east/north.
The two differ by the grid convergence, and the error is not small:

| | flat-model error over the image | grid convergence |
|---|---|---|
| Silverstone | max **29.9 m**, rms 6.4 m | −1.565° |
| Monaco | max **16.6 m**, rms 3.2 m | +1.090° |

The track is ~12 m wide, so the centerline guide sat up to 2.5 track widths off
the asphalt at the image extremes, and every derived coordinate inherited it.
`configs/monaco_mosaic.yaml` asserted the convergence was *"sub-degree —
negligible"*; it is 1.09°, which over a 750 m lever arm is 14 m.

`src/twinkly_mockup/mosaic.py` was never wrong — `MosaicSidecar.north_angle_deg`
already drives `cos_t`/`sin_t` through `meters_to_px` and `sample`. It was being
fed `0.0`.

## Decision

The sidecar records the projection it was built in. `north_angle_deg` carries the
grid convergence (θ = −γ), and two keys are added: `utm_epsg` and `grid_scale`.
Pixel math becomes

```
px = origin_px.x + grid_scale · ( cos θ · e − sin θ · n) / m_per_px
py = origin_px.y + grid_scale · (−sin θ · e − cos θ · n) / m_per_px
```

over flat-ENU offsets `e`,`n` from the origin. Both values are computed at
mosaic-build time from the GDAL geotransform and SRS — never hand-typed.

A rotation-plus-scale affine is used rather than an exact inverse transverse
Mercator: fitted over each full image its residual is **≤ 0.105 m** (Silverstone)
and **≤ 0.05 m** (Monaco) — sub-pixel, and two orders of magnitude below the
precision of placing a vertex on satellite imagery.

## Consequences

- No new dependency in the browser and no hand-rolled TM series, which is the
  main thing the exact alternative would have cost.
- The transform now has two implementations (Python for the renderer, JS for the
  tracer, deliberately — a round-trip per drag frame is not viable). They are
  bound by a Python-generated golden fixture asserted from `node --test`.
- **The sign convention must be pinned by that fixture, not by argument.** A
  flipped θ doubles the error instead of removing it and looks entirely
  plausible at every zoom level — which is how the original `0.0` survived a
  full extraction run, a debug overlay, and an export.
- `tests/test_frame_registration.py` recomputes convergence and scale from each
  shipped sidecar's origin and `utm_epsg` and asserts the stored values match, so
  a new circuit cannot ship misregistered. `pyproj` joins the dev dependencies as
  the authority for that computation.
- The Monaco snapshot poses were derived against the misregistered frame. The
  three corner hints driving the layout sweep were chosen to compensate for an
  error that is now gone, and must be re-picked. Tracked separately.
