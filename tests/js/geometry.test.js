// Fold detection for the tracer's editing surface (ADR-0003).
//
// The app highlights where a seeded edge crosses itself and deliberately does
// not repair it — an automatic un-folding would pick a geometry nobody
// reviewed and hand it to a solver that cannot tell it was guessed. So the
// detector is the whole safety property: a fold it misses is a fold that
// reaches `fastest-lap` looking exactly like a track edge.
//
// Two kinds of case here. Synthetic shapes pin the primitives, where the right
// answer is known exactly. Then `tests/fixtures/seed_boundary.json` — real
// centerlines seeded by the Python seeder — pins the two things that matter in
// practice: seeding a shipped circuit produces no folds at all, and a widened
// seed produces folds exactly where the circuit is tight.
//
// That second half is why the fixture has a deliberately over-wide Monaco in
// it. Against the shipped half-widths alone, a detector that always returned
// `[]` would pass every assertion in this file.
//
//   node --test tests/js/

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, it } from "node:test";

import {
  segmentCount,
  segmentMidpoint,
  segmentsIntersect,
  selfIntersections,
} from "../../scripts/tracer/geometry.js";

const FIXTURE = JSON.parse(
  readFileSync(fileURLToPath(new URL("../fixtures/seed_boundary.json", import.meta.url)), "utf8"),
);

const shipped = FIXTURE.cases.filter((c) => c.shipped);
const wide = FIXTURE.cases.find((c) => !c.shipped);

const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);

/** Index of the centerline vertex nearest a point, and how far away it is. */
function nearestVertex(points, p) {
  let best = { i: 0, d: Infinity };
  points.forEach((q, i) => {
    const d = dist(q, p);
    if (d < best.d) best = { i, d };
  });
  return best;
}

/** How far apart two segments are along a closed ring, in segments. */
function span(hit, n) {
  return Math.min(hit.b - hit.a, n - (hit.b - hit.a));
}

describe("segment helpers", () => {
  const square = [[0, 0], [10, 0], [10, 10], [0, 10]];

  it("counts the closing segment only on a closed ring", () => {
    assert.equal(segmentCount(square, true), 4);
    assert.equal(segmentCount(square, false), 3);
    assert.equal(segmentCount([[0, 0]], true), 0);
  });

  it("puts an inserted vertex exactly on the existing line", () => {
    // The criterion is exactness, not closeness: inserting must not itself move
    // the boundary, or every insert is an unrequested nudge the operator then
    // has to notice and undo.
    assert.deepEqual(segmentMidpoint(square, 0, true), [5, 0]);
    assert.deepEqual(segmentMidpoint(square, 3, true), [0, 5]);
  });

  it("takes the midpoint of the closing segment when closed", () => {
    assert.deepEqual(segmentMidpoint(square, 3, true), [0, 5]);
  });
});

describe("two segments", () => {
  it("finds a clean crossing", () => {
    assert.deepEqual(segmentsIntersect([0, 0], [10, 10], [0, 10], [10, 0]), [5, 5]);
  });

  it("reports nothing for segments that miss", () => {
    assert.equal(segmentsIntersect([0, 0], [1, 0], [0, 5], [1, 5]), null);
  });

  it("reports nothing for parallel segments", () => {
    assert.equal(segmentsIntersect([0, 0], [10, 0], [0, 1], [10, 1]), null);
  });

  it("catches a vertex dragged exactly onto another segment", () => {
    // Measure-zero, and reachable: a snapped or hand-typed vertex lands here.
    assert.deepEqual(segmentsIntersect([5, 0], [5, 5], [0, 0], [10, 0]), [5, 0]);
  });
});

describe("self-intersection", () => {
  it("finds the crossing in a bowtie", () => {
    const bowtie = [[0, 0], [10, 0], [0, 10], [10, 10]];
    const hits = selfIntersections(bowtie, true);
    assert.equal(hits.length, 1);
    assert.deepEqual(hits[0].point, [5, 5]);
  });

  it("reports nothing for a simple ring", () => {
    assert.deepEqual(selfIntersections([[0, 0], [10, 0], [10, 10], [0, 10]], true), []);
  });

  it("does not report neighbouring segments meeting at their shared vertex", () => {
    // Every polyline does this everywhere; reporting it would bury the folds.
    assert.deepEqual(selfIntersections([[0, 0], [10, 0], [10, 10]], false), []);
  });

  it("does not report the closing segment meeting segment 0", () => {
    assert.deepEqual(selfIntersections([[0, 0], [10, 0], [10, 10], [0, 10]], true), []);
  });

  it("finds a fold that an open polyline only has when closed", () => {
    const open = [[0, 0], [10, 0], [10, 10], [5, -5]];
    assert.deepEqual(selfIntersections(open, false), []);
    assert.equal(selfIntersections(open, true).length, 1);
  });

  it("clears as soon as the offending vertex moves back", () => {
    // The live-update property: an operator dragging a fold out gets feedback
    // while dragging, not after letting go.
    const folded = [[0, 0], [10, 0], [0, 10], [10, 10]];
    assert.equal(selfIntersections(folded, true).length, 1);
    const fixed = folded.slice();
    fixed[2] = [10, 10];
    fixed[3] = [0, 10];
    assert.deepEqual(selfIntersections(fixed, true), []);
  });
});

describe("seeded circuits", () => {
  for (const c of shipped) {
    it(`${c.circuit} seeds clean at its manifest half-width (${c.half_width_m} m)`, () => {
      assert.deepEqual(selfIntersections(c.left_m, c.closed), []);
      assert.deepEqual(selfIntersections(c.right_m, c.closed), []);
    });
  }

  it("a widened Monaco folds on both edges", () => {
    assert.ok(wide, "the fixture has no wide case to detect anything in");
    assert.ok(selfIntersections(wide.left_m, wide.closed).length > 0);
    assert.ok(selfIntersections(wide.right_m, wide.closed).length > 0);
  });

  it("every fold sits a half-width off the centerline, where the offset is", () => {
    // A fold is the *inner* offset crossing itself, so it lands at roughly the
    // offset distance from the line it came from. Anything elsewhere would be
    // the detector reporting something other than a fold.
    const n = wide.centerline_m.length;
    for (const side of ["left_m", "right_m"]) {
      for (const hit of selfIntersections(wide[side], wide.closed)) {
        const near = nearestVertex(wide.centerline_m, hit.point);
        assert.ok(
          Math.abs(near.d - wide.half_width_m) < 2.0,
          `${side} fold at ${near.d.toFixed(1)} m from the centerline, ` +
            `expected ~${wide.half_width_m} m`,
        );
        assert.ok(near.i >= 0 && near.i < n);
      }
    }
  });

  it("folds between nearby segments land at the circuit's tightest corners", () => {
    // Tightness measured by the seeder's own miter factor, so the claim is
    // "folds happen where the turn is", not "folds happen where I looked".
    const n = wide.centerline_m.length;
    const tightest = new Set(
      [...wide.miter_factors.keys()]
        .sort((a, b) => wide.miter_factors[b] - wide.miter_factors[a])
        .slice(0, Math.ceil(n * 0.15)),
    );
    let checked = 0;
    for (const side of ["left_m", "right_m"]) {
      for (const hit of selfIntersections(wide[side], wide.closed)) {
        if (span(hit, segmentCount(wide[side], wide.closed)) > 4) continue;
        const { i } = nearestVertex(wide.centerline_m, hit.point);
        assert.ok(
          [-2, -1, 0, 1, 2].some((k) => tightest.has((i + k + n) % n)),
          `${side}: fold near centerline vertex ${i} is not at a tight corner`,
        );
        checked += 1;
      }
    }
    assert.ok(checked >= 4, `only ${checked} local folds to check`);
  });

  it("also finds a fold between two distant parts of the lap", () => {
    // Monaco doubles back on itself, so a wide enough offset overlaps sections
    // that are far apart along the lap. That is a different failure from a
    // miter fold-over and a detector that only compared neighbouring segments
    // would miss it entirely.
    const hits = selfIntersections(wide.left_m, wide.closed);
    const n = segmentCount(wide.left_m, wide.closed);
    assert.ok(
      hits.some((h) => span(h, n) > 10),
      "no long-range fold found; the detector may only be checking neighbours",
    );
  });
});
