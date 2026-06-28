"""Car photo silhouette + compose tests.

Covers the masked-photo's pixel dimensions, the wood-grain background
removal, the CCW orientation convention, and the compose step's placement
inside the cutout.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from twinkly_mockup.car import make_car
from twinkly_mockup.compose import _paste_car
from twinkly_mockup.config import (
    LED_PITCH_M,
    CarSpec,
    Config,
    Layout,
    Render,
    Snapshot,
)
from twinkly_mockup.led import cutout_pixel_rect

WALL = (240, 240, 235)


def _config(
    *,
    outer_w: int = 9,
    outer_h: int = 6,
    cutout_w: int = 4,
    cutout_h: int = 2,
    cutout_x: int = 2,
    cutout_y: int = 2,
    scale: int = 10,
    car_dim_cm: tuple[float, float] = (61.0, 25.0),
    orientation_deg: float = 0.0,
) -> Config:
    return Config(
        layout=Layout(
            outer_tiles_w=outer_w,
            outer_tiles_h=outer_h,
            cutout_tiles_w=cutout_w,
            cutout_tiles_h=cutout_h,
            cutout_offset_x=cutout_x,
            cutout_offset_y=cutout_y,
        ),
        render=Render(scale_px_per_led=scale, wall_color=WALL),
        snapshot=Snapshot(
            mosaic="unused.yaml", x_m=0.0, y_m=0.0, viewport_m=(10.0, 10.0)
        ),
        car=CarSpec(dimensions_cm=car_dim_cm, orientation_deg=orientation_deg),
        output_path="ignored.png",
    )


def _wall_frame(cfg: Config) -> Image.Image:
    """A wall-colored canvas the size of the LED frame (no LED dots)."""
    from twinkly_mockup.led import grid_pixel_size

    w, h = grid_pixel_size(cfg.layout, cfg.render)
    return Image.new("RGB", (w, h), WALL)


def test_make_car_pixel_dimensions_match_cm_and_scale() -> None:
    px_per_m = 10 / LED_PITCH_M  # 375 px/m at default scale
    img = make_car(length_cm=61.0, width_cm=25.0, px_per_meter=px_per_m)
    assert img.mode == "RGBA"
    # width_cm/100 * 375 = 93.75 → 94; length_cm/100 * 375 = 228.75 → 229
    assert img.size == (94, 229)


def test_make_car_masks_wood_background() -> None:
    """The output has substantial fully-transparent area (wood was masked out)
    AND substantial fully-opaque area (the car silhouette survived)."""
    img = make_car(length_cm=61.0, width_cm=25.0, px_per_meter=375.0)
    alpha = np.array(img)[..., 3]
    fully_transparent = (alpha == 0).mean()
    fully_opaque = (alpha == 255).mean()
    assert fully_transparent > 0.10, (
        f"expected >10% fully-transparent (background) pixels, got {fully_transparent:.2f}"
    )
    assert fully_opaque > 0.50, (
        f"expected >50% fully-opaque (car) pixels, got {fully_opaque:.2f}"
    )


def test_make_car_has_opaque_silhouette_in_center() -> None:
    """The car body fills the image's middle column → mostly opaque pixels there."""
    img = make_car(length_cm=61.0, width_cm=25.0, px_per_meter=375.0)
    alpha = np.array(img)[..., 3]
    h, w = alpha.shape
    middle = alpha[:, w // 2 - 2 : w // 2 + 2]
    opaque_fraction = (middle == 255).mean()
    assert opaque_fraction > 0.85, (
        f"expected the center column to be mostly opaque car body, got {opaque_fraction:.2f}"
    )


def test_make_car_opaque_area_is_a_realistic_silhouette_fraction() -> None:
    """After cropping to the silhouette bbox the masked car fills most but
    not all of the image — sanity check that the bbox crop happened and
    didn't accidentally trim the alpha."""
    img = make_car(length_cm=61.0, width_cm=25.0, px_per_meter=375.0)
    alpha = np.array(img)[..., 3]
    frac = (alpha > 0).mean()
    assert 0.60 < frac < 0.95, f"opaque fraction {frac:.2f} outside plausible range"


def test_orientation_deg_90_swaps_dimensions() -> None:
    """Rotating 90° with expand=True swaps width and height — the photo follows
    the same convention as the procedural silhouette did."""
    car = make_car(length_cm=61.0, width_cm=25.0, px_per_meter=375.0)
    rotated = car.rotate(90.0, resample=Image.BICUBIC, expand=True)
    assert rotated.size == (car.size[1], car.size[0])


def test_compose_centers_car_on_cutout_center_pixel() -> None:
    cfg = _config(car_dim_cm=(20.0, 10.0))  # smaller car to fit any cutout
    frame = _wall_frame(cfg)
    composed = _paste_car(frame, cfg)
    left, top, right, bottom = cutout_pixel_rect(cfg.layout, cfg.render.scale_px_per_led)
    cx = (left + right + 1) // 2
    cy = (top + bottom + 1) // 2
    # The center pixel of the cutout should sit on an opaque car pixel — the
    # body — not the wall background.
    center_rgb = composed.getpixel((cx, cy))
    assert center_rgb != WALL, f"expected car pixel at cutout center ({cx},{cy}), got {center_rgb}"


def test_rotated_car_pixels_stay_within_cutout() -> None:
    """At a non-trivial orientation, no opaque car pixels leak past the cutout.

    Verified by comparing the composed frame against the wall-only frame: any
    pixel that changed must be inside the cutout rect.
    """
    cfg = _config(car_dim_cm=(20.0, 10.0), orientation_deg=37.0)
    frame = _wall_frame(cfg)
    composed = _paste_car(frame, cfg)
    base = np.array(frame)
    comp = np.array(composed.convert("RGB"))
    changed = (comp != base).any(axis=-1)
    assert changed.any(), "expected the car to be present in the composed frame"

    left, top, right, bottom = cutout_pixel_rect(cfg.layout, cfg.render.scale_px_per_led)
    outside_mask = np.ones_like(changed, dtype=bool)
    outside_mask[top : bottom + 1, left : right + 1] = False
    assert not (changed & outside_mask).any(), (
        "rotated car spilled past the cutout pixel range"
    )


def test_car_visible_in_saved_png(tmp_path) -> None:
    """End-to-end visual smoke: render a frame + composed car, confirm pixels."""
    cfg = _config(car_dim_cm=(40.0, 20.0))
    frame = _wall_frame(cfg)
    composed = _paste_car(frame, cfg)
    out = tmp_path / "car_visual.png"
    composed.save(out, format="PNG")
    arr = np.array(Image.open(out).convert("RGB"))
    # At least some pixels must differ from the wall color — i.e. the car is
    # visible in the saved file, not just an empty wall.
    not_wall = (arr != np.array(WALL)).any(axis=-1)
    assert not_wall.sum() > 100, "expected visible car pixels in the saved PNG"
