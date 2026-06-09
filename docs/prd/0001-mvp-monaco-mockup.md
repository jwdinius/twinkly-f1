# MVP: Twinkly Squares mockup for Monaco F1 LEGO display

## Problem Statement

I own a LEGO Technic MCL39 F1 car (61 × 25 cm) and a 6-tile Twinkly Squares starter kit. I want to wall-mount the car on a block protruding from the wall, surround it with a frame of Twinkly Squares tiles, and have the tiles display top-down satellite imagery of a famous F1 circuit that scrolls and rotates beneath the car to create the illusion the car is driving the lap.

Before buying any additional tiles (a 9×6 layout is ~$1,500 USD; a roomier 12×8 layout is ~$3,000+ USD), I need to honestly predict what the finished installation will look like. Specifically:

- How many tiles do I need to buy?
- What outer dimensions and center cutout should the layout have?
- Will the LED resolution actually render the Monaco circuit recognizably, or will the satellite imagery degrade into mud at the ~2.7 m-per-LED scale dictated by the car's 1:9.2 implied scale?

A static PNG mockup, faithful to what real Twinkly hardware would emit, is the cheapest possible answer to these questions.

## Solution

A Python CLI (`twinkly-mockup`) that, given a YAML config describing a tile layout, renders a single PNG showing the wall as it would appear with that layout displaying a chosen Monaco corner. The renderer composites:

1. A satellite mosaic of Monaco (pre-acquired by hand from Google Earth Pro, with a hand-annotated geo-registration sidecar) cropped + rotated around a snapshot point.
2. The cropped image area-weighted-downsampled to LED resolution and rendered as discrete dots on a black tile substrate (honest visual of Twinkly Squares).
3. A procedural top-down silhouette of an F1 car in McLaren papaya, scaled to 61 × 25 cm, composited into the central cutout where the wall-mounted LEGO sits.
4. Off-white wall background outside the LED frame.

Iterating on layout configs and viewing the output PNGs answers the sizing + cutout questions. Three Monaco snapshots — Massenet, Loews hairpin, Tabac → Swimming Pool — stress-test the layout across wide-context, tight-rotation, and walled-corridor visuals.

The MVP intentionally biases every choice toward *under*estimating LED fidelity (dot-on-black rendering, off-white wall background, no bloom, no color calibration) so that any sizing decision made from the mockup will look at least as good — never worse — on real hardware.

## User Stories

1. As Joe, I want to render a single PNG showing what a specific Twinkly tile layout would look like on my wall with my MCL39 LEGO car at a chosen Monaco corner, so I can decide whether the layout reads as "car on a track."
2. As Joe, I want to compare multiple layout configs (e.g., 7×5 vs 9×6 vs 12×8 outer tiles) side by side, so I can pick the smallest layout that still looks credible before committing money to extension packs.
3. As Joe, I want the mockup to use a top-down satellite mosaic of the Monaco circuit at known scale, so my sizing decision is grounded in real-world geometry, not stylized art.
4. As Joe, I want the mockup to render LEDs as discrete dots on a black tile substrate, not as filled cells, so the mockup honestly reflects what Twinkly hardware emits and I don't overpay for tiles whose perceived resolution won't deliver.
5. As Joe, I want all three Monaco corner snapshots (Massenet, Loews hairpin, Tabac → Swimming Pool) renderable for any candidate layout, so I can stress-test the layout across wide-context, tight-rotation, and walled-corridor visuals before deciding.
6. As Joe, I want a procedural F1 silhouette in McLaren papaya rendered at MCL39 dimensions (61 × 25 cm) in the central cutout, so I can judge whether the LED frame around the car looks proportional.
7. As Joe, I want the area outside the LED frame rendered as off-white, so I see the LEDs in honest contrast against a typical wall — not flattered by a dark background.
8. As Joe, I want each snapshot's parameters in a YAML config file under version control, so I can keep a history of layouts I've tried.
9. As Joe, I want a single CLI command (`render`) to render one config to a PNG, so my iteration loop is "edit YAML, re-run, view, repeat."
10. As Joe, I want a CLI command (`render-all`) to render multiple configs in one invocation, so I can produce all three Monaco snapshots for a given layout in one step.
11. As Joe, I want the `Mosaic` module to project between local Cartesian meters and mosaic pixels using a hand-annotated YAML sidecar, so I can derive snapshot positions by eye off the Google Earth Pro export.
12. As Joe, I want the `LedGrid` module to support a frame layout with a central rectangular cutout, so it matches the physical setup where no LEDs sit behind the car mount block.
13. As Joe, I want margin parameters (outer tile count, cutout tile count, cutout offset) exposed as config knobs — not constants — so I can sweep multiple layouts without code changes.
14. As Joe, I want the renderer to use area-weighted downsampling (e.g., OpenCV `INTER_AREA`), so colors honestly represent what real LEDs would output without aliasing artifacts.
15. As Joe, I want LED dots rendered at ~50% of pitch as squares, on a black tile substrate, with one LED-pitch's worth of black gap between adjacent tiles, so the mockup approximates what Twinkly Squares look like viewed from 2–3 m away.
16. As Joe, I want output rendering at a configurable scale (default 10 px per LED), so I can produce PNGs that fit on-screen at 1:1 yet show individual LEDs clearly.
17. As Joe, I want each rendered PNG to embed the source config name in its filename, so I don't lose track of which output came from which layout.
18. As Joe, I want the trajectory CSV schema (`t, x, y, yaw` with `yaw` in radians CCW from +x and uniform `dt`) defined and validated from day one — even though the MVP renders only hard-coded snapshots — so the contract with the future lap optimizer (fastest-lap.dev) is enforced before any code is shared across the boundary.
19. As Joe, I want the `Trajectory` module loadable from a CSV file, so phase-2 streaming will plug in by swapping data, not code.
20. As Joe, I want `uv` as the package manager and `pyproject.toml` as the build descriptor, so installs are fast and lock-file-deterministic.
21. As Joe, I want `typer` for the CLI, so new subcommands (later: `stream`, `push`) are typed Python functions without argparse boilerplate.
22. As Joe, I want the package laid out under `src/`, so development imports do not silently mask packaging bugs.
23. As Joe, I want `pydantic` models for the YAML configs, so a typo in a config key is caught at load time with a useful error.
24. As Joe, I want automated tests for the `Mosaic` projection methods — a `px → meters → px` round-trip and a synthetic-distinctive-pixel sampling test — so I catch sign, scale, y-axis-flip, and yaw-sign bugs before they silently corrupt every frame.
25. As Joe, I want automated tests for the `LedGrid` module — solid-color source rendering to expected per-LED colors, cutout region rendering as wall-colored (no LEDs) — so layout and downsample bugs surface in CI rather than during visual inspection.
26. As Joe, I want automated tests for the `Trajectory` module — load + sample at exact and interpolated times, plus invalid-schema fixtures raise expected errors — so the contract holds before phase 2 begins.
27. As Joe, I want an integration test that runs the `render` CLI on a fixture config and asserts the output PNG exists, has expected dimensions, and is non-empty, so the full pipeline stays green as modules evolve.
28. As a future maintainer, I want each Python module to mirror one pipeline stage (`config`, `mosaic`, `led`, `car`, `compose`, `cli`), so the dataflow reads top-to-bottom in `cli.py` in the same order the design was discussed.
29. As a future maintainer, I want all geometry computations to flow through the `Mosaic` module's projection methods, so adding a second circuit later does not require duplicating coordinate-frame math.
30. As a reviewer, I want to view rendered PNGs side by side, so I can give an informed opinion on which layout Joe should commit to.

## Implementation Decisions

### Hardware scale + layout

- LEGO Technic MCL39 dimensions: **61 × 25 cm**, implying a **1:9.2 scale** vs. a real F1 car (5.6 m long).
- Twinkly Squares tile constants: **16 cm tile pitch**, **6 × 6 LEDs per tile**, **~26.7 mm LED pitch**. Per-LED ground coverage at 1:9.2 ≈ **2.67 m of real track**.
- Physical layout is a **frame with a central rectangular cutout** (no LEDs behind the car mount). Cutout is dimensioned in whole tiles. Wall-mounted; camera follows the car with **heading rotation** (car visually static, satellite imagery rotates and translates beneath).
- Margin (outer tile count, cutout tile count, cutout offset) is a **config knob**, not a code constant. The MVP's primary deliverable is to enable sweeping over multiple values to pick a final layout.

### Coordinate frame

- **Local Cartesian (ENU)** in meters, with per-circuit origin given as `(origin_lat, origin_lon)`.
- `+x` = east, `+y` = north. `yaw` in **radians, CCW from +x** (standard math convention; matches what `numpy.atan2(dy, dx)` returns).
- This frame is shared by the mosaic geo-registration, the trajectory schema, and every snapshot config — there is no other coordinate frame in the project.

### Mosaic acquisition (out of code, in process)

- Mosaic PNG acquired manually from **Google Earth Pro**: tilt = 0, heading = 0, View → Scale Legend on, File → Save Image at max resolution. The exported scale legend gives `m_per_px`; one recognizable landmark gives `(origin_lat, origin_lon)` and its `origin_px`. Sidecar YAML captures these as five fields: `path`, `origin_lat`, `origin_lon`, `origin_px`, `m_per_px`, `north_angle_deg` (= 0 for north-up exports).
- The scripted tile-fetcher alternative (Web Mercator stitching from ESRI/Mapbox) is deferred — the schema is forward-compatible by introducing additional `source:` types later.

### Trajectory schema

- CSV with columns `t, x, y, yaw` (later columns like `vx, vy, omega` are optional and ignored by the renderer).
- `t` monotone increasing at uniform `dt` (e.g., 10 ms). Sampling at non-knot times is linear interpolation (and clamping at endpoints).
- Validation rejects non-monotone `t`, non-uniform `dt`, missing columns, out-of-range `yaw`.
- For MVP, the renderer accepts a direct `(x, y, yaw)` from config (no CSV); trajectory loading is implemented and validated but invoked only via test fixtures.

### Modules

Three deep modules and four shallow ones, each mirroring a pipeline stage:

**Deep modules.**

- **`Mosaic`** — geo-registered satellite image.
  - Hides: PNG load, sidecar YAML parse, ENU↔pixel projection, crop-window calculation, rotation, edge clipping.
  - Interface (conceptually): `Mosaic.load(yaml_path) → Mosaic`; `mosaic.sample(center_xy_m, yaw_rad, viewport_m, output_px) → ndarray`.
- **`LedGrid`** — frame-shaped LED downsampler + dot-on-black renderer.
  - Hides: area-weighted resample, frame-vs-rectangle layout (cutout), per-LED dot rendering at 50% pitch, inter-tile gap, off-white wall background.
  - Interface: `LedGrid(layout_spec) → LedGrid`; `grid.render(source_image) → PIL.Image` covering the wall region of interest.
- **`Trajectory`** — time-indexed `(x, y, yaw)` lookup.
  - Hides: CSV parse, schema validation, uniform-`dt` assertion, time-clamping at endpoints, linear interpolation.
  - Interface: `Trajectory.load(csv_path) → Trajectory`; `trajectory.sample_at(t) → (x, y, yaw)`.

**Shallow modules.**

- **`Config`** — pydantic models for the YAML, loader entrypoint.
- **`car`** — single function returning a procedural F1 silhouette as a transparent PIL image at given dimensions. Shape composed of: rounded body rect (papaya `#FF8000`, ~80% length × 55% width), front + rear wing rects (full + 85% width respectively, dark grey), small dark cockpit/halo, four black wheel circles at corners. Drawn axis-aligned with nose pointing up; the composer rotates the whole result if the layout's `car.orientation_deg` is nonzero.
- **`compose`** — final canvas assembly: places the `LedGrid` output, drops in the car silhouette over the cutout region, fills the surrounding wall region with off-white (configurable). Writes PNG.
- **`cli`** — typer commands. MVP: `render <config>` (one config → one PNG) and `render-all <config>...` (many configs → many PNGs to a directory). Stubs for `stream` and `push` are not part of MVP scope but the CLI structure should not foreclose them.

### Rendering pipeline (per-frame)

1. Load `Mosaic` from its sidecar.
2. Sample the mosaic at the snapshot's `(x, y, yaw)` with viewport = outer tile rectangle in meters and output px = oversampled raster (e.g., 5–10× LED grid resolution) — this is the "source image" presented to the LED layer.
3. Pass to `LedGrid`, which area-weighted-downsamples to per-LED colors, draws dots on the black tile substrate, blanks the cutout region to wall color, and renders the inter-tile gaps.
4. `compose` overlays the procedural car silhouette into the cutout and writes the final PNG.

### Renderer parameters (pinned)

- Downsampling: **area-weighted mean** (`cv2.resize(..., INTER_AREA)` or equivalent).
- LED active region: **~50% of pitch**, drawn as a square.
- Inter-tile gap: **one LED-pitch's worth of black**.
- Wall background: **off-white** (configurable).
- Output scale: **10 px per LED** (configurable).
- Gamma/color calibration: **none** for MVP. Linear sRGB. The mock-to-hardware color delta is a phase-3 problem.

### Project structure + tooling

- **Package manager:** `uv`.
- **Build descriptor:** `pyproject.toml`.
- **Layout:** `src/twinkly_mockup/` with one Python module per pipeline stage.
- **CLI library:** `typer`. Console script entry point installed as `twinkly-mockup`.
- **Config validation:** `pydantic` models.
- **Imaging:** `Pillow` for compositing/drawing, `numpy` for arrays, `opencv-python` for `INTER_AREA` downsampling. Alternatively, Pillow's `LANCZOS`/`BOX` resamplers may substitute for OpenCV if a smaller dependency set is preferred — decision deferred to the implementer, with a note that `INTER_AREA` is the well-tested area-weighted choice.
- **Output convention:** PNG named after the source config (e.g., `out/monaco_loews.png` for `configs/monaco_loews.yaml`).

### Initial layout configs to ship as fixtures

Three Monaco snapshots, each with placeholder `(x, y, yaw)` values that the user will populate once the mosaic is acquired:

- `configs/monaco_massenet.yaml` — wide-context corner.
- `configs/monaco_loews.yaml` — slowest hairpin in F1; stress-tests yaw rotation and tight margins.
- `configs/monaco_tabac.yaml` — narrow walled corridor; stress-tests high-contrast edges.

Suggested first sizing-sweep layouts to render against all three snapshots:

- Tight: outer 7 × 5 tiles, cutout 4 × 2 tiles → 28 tiles (~$900).
- **Standard (suggested default): outer 9 × 6, cutout 4 × 2 → 46 tiles (~$1,500).**
- Cinematic: outer 12 × 8, cutout 4 × 2 → 88 tiles (~$2,900).

## Testing Decisions

A good test for this project exercises a module's external behavior — what callers observe — not its internal data flow. Specifically: tests should not assert on intermediate ndarray shapes inside a module, but they should assert that calling the module's documented interface with documented inputs produces the documented output (correct pixel values, correct types, correct errors). The renderer's *visual* quality is verified by the developer looking at the PNG; the tests are there to catch the silent-corruption class of bugs (wrong sign, wrong scale, wrong axis flip) that look fine to the eye until they don't.

There is no prior art in this repository — this PRD scaffolds the project from scratch.

### Tests to write

1. **`Mosaic` projection round-trip.** Construct a minimal mosaic (a small PIL image and a sidecar with known `origin_lat/lon/px, m_per_px`). Assert `meters_to_px(px_to_meters(px)) == px` and the reverse for several points across the image. Catches sign errors and scale-factor bugs that silently misalign every frame.
2. **`Mosaic` sampling correctness.** Construct a synthetic mosaic with a single distinctive pixel at a known `(lat, lon)`. Call `mosaic.sample(center_xy_m, yaw_rad=0, viewport_m, output_px)` with parameters that should place the distinctive pixel at a known output pixel. Assert that pixel matches and surrounding pixels are background. Repeat with `yaw_rad = π/2` to verify rotation direction. Catches y-axis-flip and yaw-sign bugs.
3. **`LedGrid` solid-color downsampling.** Construct a solid-color source image. Render through `LedGrid` and assert every "lit LED" position has the source color and every "off" position (between LEDs, inter-tile gap) is black, and the cutout region is wall-colored.
4. **`LedGrid` cutout placement.** Construct a layout with a known cutout (e.g., 4 × 2 tiles centered) and verify no LED dots appear within the cutout pixel range.
5. **`Trajectory` load + sample.** Load a small valid CSV. Sample at a knot time (exact match) and at a mid-knot time (linear interpolation). Assert values are within numerical tolerance. Cover the `yaw` interpolation wrap-around edge case if relevant (cleanly: avoid by asserting trajectories cover a single revolution).
6. **`Trajectory` schema rejection.** Three invalid fixture CSVs: non-monotone `t`, non-uniform `dt`, wrong column set. Each must raise a validation error with a useful message.
7. **Integration test: `render` CLI.** With the fixtures (a synthetic mini-mosaic + a config pointing at it + a snapshot at the mosaic origin), invoke `twinkly-mockup render` and assert the output PNG file exists, has the expected pixel dimensions for the configured layout + render scale, and contains non-trivial color variation (i.e., is not a uniform image).

## Out of Scope

The following are explicitly **not** part of this PRD. Each becomes its own future PRD when the MVP confirms the sizing direction:

- **Phase 2 — streaming preview MP4.** Pre-rendered H.264 MP4 of a full Monaco lap at 30 fps, 0.5× playback speed, driven by a trajectory CSV produced by an external lap optimizer. The MVP's `Trajectory` module is the contract for this; the actual streaming pipeline is a follow-up.
- **Phase 3 — real Twinkly hardware push.** Twinkly movie upload via `xled`, tile layout discovery via the Twinkly mobile app's calibration feature, gated bringup using the existing 6-tile starter kit before any new tile purchase.
- **Lap optimizer integration** (`fastest-lap.dev`). The optimizer is a separate process; the MVP only locks the CSV schema it must emit.
- **Track boundary tracing tool.** A small CLI subcommand (`twinkly-mockup trace-boundary`) for clicking the Monaco track edges over the mosaic — needed by the optimizer, not by the MVP renderer.
- **Variable playback speed**, **HUD telemetry overlays**, **multiple circuits**, **bloom / "what does the LED look like in person" rendering**, **per-channel gamma calibration**.
- **Physical construction**: LEGO car mount mechanism, tile mounting on the wall, Twinkly power and daisy-chain topology.
- **Tile-sweep comparison sheet** (a `twinkly-mockup compare` command that lays out N rendered PNGs into a single side-by-side image). Useful but not required — running `render-all` and opening the PNGs in an image viewer suffices.

## Further Notes

### Phased plan and risk gates

The MVP exists inside a four-stage plan with explicit go/no-go gates, designed so no money is spent until the concept is validated:

| Gate | What's done | Success criterion | Money committed |
|------|-------------|-------------------|-----------------|
| **0. MVP (this PRD)** | Three Monaco snapshot PNGs across candidate layouts | "Looks recognizable as F1 corners" | $0 |
| 1. Phase 2 — preview MP4 | Full-lap MP4 from optimizer trajectory | "Watching the MP4 looks good enough to want on my wall" | $0 |
| 2. Phase 2.5 — hardware proof | Upload a downsized version of the MP4 to the existing 6-tile starter kit | "Real LEDs render Monaco recognizably; colors + flow look as expected" | $0 (already owned) |
| 3. Phase 3 — full purchase | Buy remaining tiles, mount per the sized plan from MVP, upload full-res movie | Final deliverable on the wall | ~$1,500–$3,600 |

The MVP's purpose is to make gate 0 cheaply passable so we can keep walking.

### Agent skills setup

This repo does **not** yet have the per-repo agent-skills configuration that the Matt Pocock skill family expects (no `CLAUDE.md`/`AGENTS.md`, no `docs/agents/`, no triage labels other than the default GitHub set). The `ready-for-agent` label was created as a one-off when this PRD was published. Before the next feature cycle, run `/setup-matt-pocock-skills` to scaffold the missing pieces (CONTEXT.md, triage label vocabulary documentation, etc.). This PRD's content does not depend on that scaffolding existing.

### Initial config to render first

Once the project skeleton is in place and the Monaco mosaic is acquired, the suggested first sizing-comparison run is: render all three Monaco snapshots against the three layout sizes (tight 7×5 / standard 9×6 / cinematic 12×8, all with a 4×2 cutout). That produces 9 PNGs which, viewed together, should make the layout decision obvious.

### Bias of every renderer choice

Each renderer parameter was chosen to err toward *under*estimating LED fidelity:

- Dot-on-black, not filled cells.
- Off-white wall background, not black.
- Linear sRGB without bloom or color calibration.
- Area-weighted mean (not median, which would falsely preserve more detail than LEDs can).

If the mockup says "this layout looks good," the real hardware will look at least as good. The opposite bias would lead to expensive mistakes.
