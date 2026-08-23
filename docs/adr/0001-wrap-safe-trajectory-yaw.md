# Wrap-safe yaw interpolation in the trajectory schema

## Status

accepted

## Context

The MVP `Trajectory` schema (`src/twinkly_mockup/trajectory.py`) required `yaw`
to be an **unwound** sequence spanning **at most one revolution** (`max − min ≤
2π`), so `sample_at` could linearly interpolate `yaw` without wrap handling.
That invariant was fine for the MVP's sub-revolution corner snapshots, but the
next phase feeds it a **full closed Monaco lap**: heading nets a full 2π over
the lap and *backtracks* through the Loews hairpin and swimming-pool chicanes,
so the unwound `max − min` exceeds 2π and trips validation — the very
"validated CSV" that is this phase's deliverable would fail to load.

## Decision

Interpolate heading as a **unit vector** — linearly interpolate `cos(yaw)` and
`sin(yaw)`, then `atan2` — and **drop** the unwound / `≤ 2π` span constraint.
`sample_at` becomes wrap-safe by construction; `import-lap` emits `yaw` in
`[−π, π]` with no global unwinding.

## Consequences

- A full closed lap is representable without the producer walking a tightrope.
- Eliminates a class of **seam bugs** for the later movie phase: a looping lap
  only needs start/end heading consistent mod 2π, which unit-vector
  interpolation handles for free.
- Reverses the MVP contract wording ("producers must emit an unwound
  sequence"); the trajectory CSV is a contract shared across the `fastest-lap`
  boundary, so this is recorded rather than changed silently.
- Unit-vector interpolation of two nearly-antipodal headings is ill-conditioned,
  but that only occurs across a ~180° step between adjacent knots — far outside
  the near-uniform, densely-sampled lap trajectories `import-lap` produces.
