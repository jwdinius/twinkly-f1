// The JS half of the mosaic frame transform, checked against Python (ADR-0004).
//
// `scripts/tracer/frame.js` exists because the tracer cannot round-trip to
// Python per drag frame. The price of a second implementation is that it can
// drift, and this suite is what stops it: Python emits
// `tests/fixtures/frame_transform.json` and every pair in it must come back out
// of the JS unchanged.
//
// The tolerance is sub-pixel by requirement but the two implementations
// evaluate the *same* formula in the same float64, so anything above ~1e-9 px
// is a real divergence rather than rounding. Being far tighter than one pixel is
// the point: a bar set at "roughly right" would have passed the flat model this
// replaced, and would pass a flipped convergence sign at every zoom the operator
// is likely to look at.
//
//   node --test tests/js/

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, it } from "node:test";

import {
  createFrame,
  lonLatToPx,
  pxToLonLat,
  flatEnuScales,
} from "../../scripts/tracer/frame.js";

const FIXTURE = JSON.parse(
  readFileSync(fileURLToPath(new URL("../fixtures/frame_transform.json", import.meta.url)), "utf8"),
);

// Sub-pixel by a wide margin — see the note above.
const PX_TOLERANCE = 1e-6;
// Round-tripping through lon/lat and back accumulates float noise proportional
// to the pixel magnitude — a few nanopixels at the far corner of a 5000 px
// raster. Same sub-pixel bar; nothing here should be read as a precision claim
// beyond "the inverse is the inverse".
const ROUND_TRIP_PX_TOLERANCE = 1e-6;

/** Worst |Δ| in pixels between the JS projection and the fixture. */
function worstPxError(frame, samples) {
  let worst = 0;
  for (const s of samples) {
    const [px, py] = lonLatToPx(frame, s.lon, s.lat);
    worst = Math.max(worst, Math.abs(px - s.px), Math.abs(py - s.py));
  }
  return worst;
}

describe("frame transform vs the Python golden fixture", () => {
  it("covers every shipped circuit", () => {
    assert.ok(FIXTURE.circuits.length >= 2, "fixture must span both shipped circuits");
    const names = FIXTURE.circuits.map((c) => c.name);
    assert.ok(names.includes("monaco"));
    assert.ok(names.includes("silverstone"));
  });

  for (const circuit of FIXTURE.circuits) {
    describe(circuit.name, () => {
      const frame = createFrame(circuit.sidecar);

      it("reproduces every lon/lat -> pixel pair", () => {
        assert.ok(
          worstPxError(frame, circuit.samples) < PX_TOLERANCE,
          `worst error ${worstPxError(frame, circuit.samples)} px`,
        );
      });

      it("includes the image corners, where a sign error is unmissable", () => {
        // origin_px is the raster centre, so the extent is twice it.
        const [ox, oy] = circuit.sidecar.origin_px;
        const corners = [
          [0, 0],
          [2 * ox, 0],
          [0, 2 * oy],
          [2 * ox, 2 * oy],
        ];
        for (const [cx, cy] of corners) {
          const hit = circuit.samples.some(
            (s) => Math.abs(s.px - cx) < 1e-3 && Math.abs(s.py - cy) < 1e-3,
          );
          assert.ok(hit, `fixture has no sample at corner (${cx}, ${cy})`);
        }
      });

      it("reproduces every pixel -> lon/lat pair", () => {
        for (const s of circuit.samples) {
          const [lon, lat] = pxToLonLat(frame, s.px, s.py);
          // Compare in metres so the bar is the same one the pixel test uses.
          const de = (lon - s.lon) * (Math.PI / 180) * frame.mPerRadEast;
          const dn = (lat - s.lat) * (Math.PI / 180) * frame.mPerRadNorth;
          assert.ok(
            Math.hypot(de, dn) < PX_TOLERANCE * circuit.sidecar.m_per_px,
            `pxToLonLat off by ${Math.hypot(de, dn)} m at (${s.px}, ${s.py})`,
          );
        }
      });

      it("round-trips px -> lon/lat -> px", () => {
        for (const s of circuit.samples) {
          const [lon, lat] = pxToLonLat(frame, s.px, s.py);
          const [px, py] = lonLatToPx(frame, lon, lat);
          assert.ok(
            Math.abs(px - s.px) < ROUND_TRIP_PX_TOLERANCE &&
              Math.abs(py - s.py) < ROUND_TRIP_PX_TOLERANCE,
            `round trip drifted at (${s.px}, ${s.py})`,
          );
        }
      });

      it("fails loudly on a flipped convergence sign", () => {
        // Not assumed — measured. A flipped theta does not cancel, it doubles:
        // the error lands at roughly twice what dropping the correction entirely
        // would cost, which is why it looks plausible and why only a fixture
        // evaluated at the corners can catch it.
        const flipped = createFrame({
          ...circuit.sidecar,
          north_angle_deg: -circuit.sidecar.north_angle_deg,
        });
        const uncorrected = createFrame({
          ...circuit.sidecar,
          north_angle_deg: 0.0,
          grid_scale: 1.0,
        });
        const flippedErr = worstPxError(flipped, circuit.samples);
        const uncorrectedErr = worstPxError(uncorrected, circuit.samples);

        assert.ok(
          flippedErr > 50,
          `flipped theta only cost ${flippedErr} px — the fixture has no teeth`,
        );
        assert.ok(
          Math.abs(flippedErr / uncorrectedErr - 2.0) < 0.1,
          `flipped/uncorrected ratio ${flippedErr / uncorrectedErr}, expected ~2`,
        );
      });

      it("fails loudly on the spherical flat-ENU model it replaced", () => {
        // The other half of the transform. Scaling both axes by the semi-major
        // axis stretches east and squeezes north; that anisotropy is what the
        // rotation-plus-scale cannot absorb.
        const spherical = { ...frame };
        const a = 6378137.0;
        spherical.mPerRadEast = a * Math.cos((circuit.sidecar.origin_lat * Math.PI) / 180);
        spherical.mPerRadNorth = a;
        assert.ok(worstPxError(spherical, circuit.samples) > 1.0);
      });
    });
  }
});

describe("flat-ENU scales", () => {
  it("uses two different radii of curvature, neither of them the semi-major axis", () => {
    const { mPerRadEast, mPerRadNorth } = flatEnuScales(43.736871113);
    const a = 6378137.0;
    assert.notEqual(mPerRadNorth, a);
    assert.ok(Math.abs(mPerRadEast - a * Math.cos((43.736871113 * Math.PI) / 180)) > 1000);
  });
});
