// Editing geometry for the tracer: where an edge crosses itself, and where a
// new vertex goes when one is inserted.
//
// This lives in a module rather than in the page for the same reason
// `frame.js` does — so it can be asserted from `node --test` without a DOM.
// Unlike `frame.js` there is no Python counterpart to be bound to: detection
// has to run on every drag frame, and nothing else in the repo needs it.
//
// Self-intersection is detected and reported. It is deliberately **not**
// repaired. Where a miter folds over at a tight corner there is no single
// correct un-folding — an automatic one would silently choose a geometry
// nobody reviewed, and hand it to a solver that cannot tell it was guessed.
// The operator drags it out; this module's job is to make sure they can see
// that there is something to drag.

/** Number of segments in a polyline — one more when it closes back on itself. */
export function segmentCount(points, closed = true) {
  if (points.length < 2) return 0;
  return closed ? points.length : points.length - 1;
}

/** Segment `i` as `[start, end]`, wrapping at the seam when `closed`. */
export function segment(points, i, closed = true) {
  return [points[i], points[(i + 1) % points.length]];
}

/**
 * Midpoint of segment `i`.
 *
 * A vertex inserted here starts *on* the existing line, so inserting never
 * itself moves the boundary — which is what makes "insert then drag" a
 * strictly additive edit rather than a nudge the operator did not ask for.
 */
export function segmentMidpoint(points, i, closed = true) {
  const [a, b] = segment(points, i, closed);
  return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
}

const cross = (ox, oy, ax, ay, bx, by) => (ax - ox) * (by - oy) - (ay - oy) * (bx - ox);

function onSegment(px, py, qx, qy, rx, ry) {
  return (
    Math.min(px, qx) <= rx && rx <= Math.max(px, qx) &&
    Math.min(py, qy) <= ry && ry <= Math.max(py, qy)
  );
}

/**
 * Do segments `p1p2` and `p3p4` meet? Returns the crossing point or `null`.
 *
 * Handles the collinear-overlap case as well as a clean crossing: an operator
 * can drag a vertex exactly onto another segment, and a fold that happens to be
 * perfectly flat is still a fold.
 */
export function segmentsIntersect(p1, p2, p3, p4) {
  const d1 = cross(p3[0], p3[1], p4[0], p4[1], p1[0], p1[1]);
  const d2 = cross(p3[0], p3[1], p4[0], p4[1], p2[0], p2[1]);
  const d3 = cross(p1[0], p1[1], p2[0], p2[1], p3[0], p3[1]);
  const d4 = cross(p1[0], p1[1], p2[0], p2[1], p4[0], p4[1]);

  if (((d1 > 0) !== (d2 > 0)) && ((d3 > 0) !== (d4 > 0))) {
    const t = d3 / (d3 - d4);
    return [p3[0] + t * (p4[0] - p3[0]), p3[1] + t * (p4[1] - p3[1])];
  }
  // Collinear or endpoint-on-segment: report the touching point itself.
  if (d1 === 0 && onSegment(p3[0], p3[1], p4[0], p4[1], p1[0], p1[1])) return [p1[0], p1[1]];
  if (d2 === 0 && onSegment(p3[0], p3[1], p4[0], p4[1], p2[0], p2[1])) return [p2[0], p2[1]];
  if (d3 === 0 && onSegment(p1[0], p1[1], p2[0], p2[1], p3[0], p3[1])) return [p3[0], p3[1]];
  if (d4 === 0 && onSegment(p1[0], p1[1], p2[0], p2[1], p4[0], p4[1])) return [p4[0], p4[1]];
  return null;
}

/**
 * Every place an edge crosses itself, as `{a, b, point}` with `a < b`.
 *
 * Segments that share a vertex are skipped: consecutive segments always meet
 * at their shared endpoint, and on a closed edge the last and first do too.
 * Those are the polyline being a polyline, not a fold.
 *
 * O(n²) with a bounding-box reject, which at ~160 vertices is well under a
 * frame — and it has to be, because this runs live while a vertex is being
 * dragged so that resolving a fold clears the highlight as it happens.
 */
export function selfIntersections(points, closed = true) {
  const n = segmentCount(points, closed);
  const out = [];
  if (n < 4) return out;

  const segs = [];
  for (let i = 0; i < n; i++) {
    const [a, b] = segment(points, i, closed);
    segs.push({
      a, b,
      minX: Math.min(a[0], b[0]), maxX: Math.max(a[0], b[0]),
      minY: Math.min(a[1], b[1]), maxY: Math.max(a[1], b[1]),
    });
  }

  for (let i = 0; i < n; i++) {
    const s = segs[i];
    for (let j = i + 2; j < n; j++) {
      if (closed && i === 0 && j === n - 1) continue;   // they share vertex 0
      const t = segs[j];
      if (s.maxX < t.minX || t.maxX < s.minX || s.maxY < t.minY || t.maxY < s.minY) continue;
      const hit = segmentsIntersect(s.a, s.b, t.a, t.b);
      if (hit) out.push({ a: i, b: j, point: hit });
    }
  }
  return out;
}
