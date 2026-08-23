// The tracer's copy of the mosaic frame transform (ADR-0004).
//
// There are two implementations of this projection and that is deliberate:
// Python for the renderer, JavaScript for the tracer, because a round-trip to
// Python per drag frame is not viable. The cost of that choice is that the two
// can silently diverge, so this module is bound to the Python one by a
// Python-generated golden fixture asserted from `node --test` — see
// `tests/js/frame.test.js` and `scripts/gen_frame_fixture.py`.
//
// Python is the authority. If the two disagree, this file is wrong.
//
// The transform is two stages, matching `enu.FlatEnu` and `Mosaic.meters_to_px`:
//
//   1. lon/lat -> flat-ENU metres, scaled by the ellipsoid's radii of curvature
//      at the origin latitude (prime-vertical east, meridional north). Using one
//      spherical radius for both is wrong by an *anisotropic* ~0.17%, which
//      stage 2 cannot absorb.
//   2. ENU metres -> mosaic pixels, rotating by the grid convergence and scaling
//      by the point scale factor, because the raster is UTM and its pixel axes
//      are grid east/north rather than true east/north.
//
// The flat `(lon - lon0) * cos(lat0) * R` model this replaced assumed stage 2
// away entirely: it put the centerline guide up to 2.5 track widths off the
// asphalt at the image extremes.

const DEG2RAD = Math.PI / 180;
const RAD2DEG = 180 / Math.PI;

// WGS84 — the datum the mosaics and the bacinger centerlines are quoted against.
export const WGS84_A_M = 6378137.0;
export const WGS84_INVERSE_FLATTENING = 298.257223563;
export const WGS84_E2 =
  (2.0 - 1.0 / WGS84_INVERSE_FLATTENING) / WGS84_INVERSE_FLATTENING;

/**
 * Metres per radian of longitude and of latitude at `originLat`.
 *
 * `N(phi)` is the prime-vertical radius of curvature and `M(phi)` the
 * meridional one; they differ by ~0.35% at mid latitudes and neither is the
 * semi-major axis.
 */
export function flatEnuScales(originLat) {
  const phi = originLat * DEG2RAD;
  const sinPhi = Math.sin(phi);
  const w2 = 1.0 - WGS84_E2 * sinPhi * sinPhi;
  const primeVertical = WGS84_A_M / Math.sqrt(w2);
  const meridional = (WGS84_A_M * (1.0 - WGS84_E2)) / (w2 * Math.sqrt(w2));
  return {
    mPerRadEast: primeVertical * Math.cos(phi),
    mPerRadNorth: meridional,
  };
}

/**
 * Build a frame from a sidecar object, keyed exactly as the sidecar YAML is.
 *
 * `north_angle_deg` and `grid_scale` default the way `MosaicSidecar` defaults
 * them — no rotation, unit scale — so a pre-ADR-0004 sidecar loads and behaves
 * as it always did rather than throwing.
 */
export function createFrame(sidecar) {
  const theta = (sidecar.north_angle_deg ?? 0.0) * DEG2RAD;
  const [originPxX, originPxY] = sidecar.origin_px;
  return {
    originLat: sidecar.origin_lat,
    originLon: sidecar.origin_lon,
    originPxX,
    originPxY,
    mPerPx: sidecar.m_per_px,
    gridScale: sidecar.grid_scale ?? 1.0,
    cosTheta: Math.cos(theta),
    sinTheta: Math.sin(theta),
    ...flatEnuScales(sidecar.origin_lat),
  };
}

/** ENU metres from the origin -> mosaic pixel coords. */
export function metersToPx(frame, e, n) {
  const k = frame.gridScale;
  const dx = k * (e * frame.cosTheta - n * frame.sinTheta);
  const dy = k * (-e * frame.sinTheta - n * frame.cosTheta);
  return [frame.originPxX + dx / frame.mPerPx, frame.originPxY + dy / frame.mPerPx];
}

/**
 * Mosaic pixel coords -> ENU metres.
 *
 * Exact inverse of `metersToPx`: the theta matrix is its own inverse (it is a
 * reflection, not a rotation), so only `gridScale` needs undoing.
 */
export function pxToMeters(frame, px, py) {
  const m = frame.mPerPx / frame.gridScale;
  const dx = (px - frame.originPxX) * m;
  const dy = (py - frame.originPxY) * m;
  return [
    frame.cosTheta * dx - frame.sinTheta * dy,
    -frame.sinTheta * dx - frame.cosTheta * dy,
  ];
}

/** WGS84 lon/lat -> mosaic pixel coords. */
export function lonLatToPx(frame, lon, lat) {
  return metersToPx(
    frame,
    (lon - frame.originLon) * DEG2RAD * frame.mPerRadEast,
    (lat - frame.originLat) * DEG2RAD * frame.mPerRadNorth,
  );
}

/** Mosaic pixel coords -> WGS84 `[lon, lat]`. */
export function pxToLonLat(frame, px, py) {
  const [e, n] = pxToMeters(frame, px, py);
  return [
    frame.originLon + (e / frame.mPerRadEast) * RAD2DEG,
    frame.originLat + (n / frame.mPerRadNorth) * RAD2DEG,
  ];
}
