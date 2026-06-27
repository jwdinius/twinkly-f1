"""Car silhouette + compose tests.

Covers the procedural silhouette's shape + dimensions, the CCW orientation
convention, and the compose step's placement inside the cutout.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from twinkly_mockup.car import DARK_GREY, PAPAYA, make_car
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
BLACK = (0, 0, 0)


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


def test_make_car_contains_papaya_body_and_dark_wings() -> None:
    img = make_car(length_cm=61.0, width_cm=25.0, px_per_meter=375.0)
    arr = np.array(img)
    rgb = arr[..., :3]
    alpha = arr[..., 3]
    papaya_mask = (rgb == np.array(PAPAYA[:3])).all(axis=-1) & (alpha == 255)
    grey_mask = (rgb == np.array(DARK_GREY[:3])).all(axis=-1) & (alpha == 255)
    assert papaya_mask.sum() > 1000, "expected a clearly visible papaya body"
    assert grey_mask.sum() > 50, "expected dark grey wings"


def test_make_car_has_four_wheels_at_body_corners() -> None:
    img = make_car(length_cm=61.0, width_cm=25.0, px_per_meter=375.0)
    arr = np.array(img)
    rgb = arr[..., :3]
    alpha = arr[..., 3]
    opaque_black = (rgb == np.array([0, 0, 0])).all(axis=-1) & (alpha == 255)

    h, w = arr.shape[:2]
    # Split the image into four corner quadrants of the *body rect* region
    # (body spans rows [0.10h, 0.90h] × cols [0.225w, 0.775w]).
    body_top, body_bottom = int(0.10 * h), int(0.90 * h)
    body_left, body_right = int(0.225 * w), int(0.775 * w)
    quads = [
        opaque_black[body_top - 20 : body_top + 20, body_left - 20 : body_left + 20],
        opaque_black[body_top - 20 : body_top + 20, body_right - 20 : body_right + 20],
        opaque_black[body_bottom - 20 : body_bottom + 20, body_left - 20 : body_left + 20],
        opaque_black[body_bottom - 20 : body_bottom + 20, body_right - 20 : body_right + 20],
    ]
    for i, q in enumerate(quads):
        assert q.sum() > 50, f"expected wheel pixels in body corner {i}"


def test_orientation_deg_90_rotates_ccw() -> None:
    """A pixel near image-top in the unrotated car lands near image-left after CCW 90°.

    The front wing is a dark grey strip near the top of the unrotated image.
    After CCW 90°, the strip should appear on the LEFT side of the rotated image.
    """
    car = make_car(length_cm=61.0, width_cm=25.0, px_per_meter=375.0)
    arr = np.array(car)
    is_dark = (arr[..., :3] == np.array(DARK_GREY[:3])).all(axis=-1) & (arr[..., 3] == 255)
    # Front wing is well within the top fifth of the unrotated image (top ≈ rows 5-21).
    top_fifth = is_dark[: arr.shape[0] // 5].sum()
    bottom_fifth = is_dark[-arr.shape[0] // 5 :].sum()
    assert top_fifth > bottom_fifth, "front wing should dominate top of unrotated car"

    rotated = car.rotate(90.0, resample=Image.BICUBIC, expand=True)
    rot_arr = np.array(rotated)
    is_dark_rot = (rot_arr[..., :3] == np.array(DARK_GREY[:3])).all(axis=-1) & (
        rot_arr[..., 3] == 255
    )
    left_fifth = is_dark_rot[:, : rot_arr.shape[1] // 5].sum()
    right_fifth = is_dark_rot[:, -rot_arr.shape[1] // 5 :].sum()
    assert left_fifth > right_fifth, (
        "after CCW 90°, the front wing should dominate the LEFT side"
    )


def test_compose_centers_car_on_cutout_center_pixel() -> None:
    cfg = _config(car_dim_cm=(20.0, 10.0))  # smaller car to fit any cutout
    frame = _wall_frame(cfg)
    composed = _paste_car(frame, cfg)
    left, top, right, bottom = cutout_pixel_rect(cfg.layout, cfg.render.scale_px_per_led)
    cx = (left + right + 1) // 2
    cy = (top + bottom + 1) // 2
    # The center pixel of the cutout should sit on an opaque car pixel — the
    # body or halo, not the wall background.
    center_rgb = composed.getpixel((cx, cy))
    assert center_rgb != WALL, f"expected car pixel at cutout center ({cx},{cy}), got {center_rgb}"


def test_rotated_car_pixels_stay_within_cutout() -> None:
    """At a non-trivial orientation, no car-specific colors leak past the cutout."""
    cfg = _config(car_dim_cm=(20.0, 10.0), orientation_deg=37.0)
    frame = _wall_frame(cfg)
    composed = _paste_car(frame, cfg)
    arr = np.array(composed.convert("RGB"))
    papaya_mask = (arr == np.array(PAPAYA[:3])).all(axis=-1)
    grey_mask = (arr == np.array(DARK_GREY[:3])).all(axis=-1)
    car_mask = papaya_mask | grey_mask
    assert car_mask.any(), "expected the car to be present in the composed frame"

    left, top, right, bottom = cutout_pixel_rect(cfg.layout, cfg.render.scale_px_per_led)
    outside_mask = np.ones_like(car_mask, dtype=bool)
    outside_mask[top : bottom + 1, left : right + 1] = False
    assert not (car_mask & outside_mask).any(), (
        "rotated car silhouette spilled past the cutout pixel range"
    )


def test_car_visible_in_saved_png(tmp_path) -> None:
    """End-to-end visual smoke: render a frame + composed car, confirm pixels."""
    cfg = _config(car_dim_cm=(40.0, 20.0))
    frame = _wall_frame(cfg)
    composed = _paste_car(frame, cfg)
    out = tmp_path / "car_visual.png"
    composed.save(out, format="PNG")
    arr = np.array(Image.open(out).convert("RGB"))
    assert (arr == np.array(PAPAYA[:3])).all(axis=-1).any()
    assert (arr == np.array(DARK_GREY[:3])).all(axis=-1).any()
    assert (arr == np.array(BLACK)).all(axis=-1).any()
