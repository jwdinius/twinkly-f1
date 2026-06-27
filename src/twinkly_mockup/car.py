"""Procedural top-down F1 silhouette.

Drawn axis-aligned with the nose pointing image-up. Sized so the model's
physical dimensions land at the rendered LED pitch — i.e. the silhouette
occupies the same fraction of the cutout that the real LEGO MCL39 will
occupy of the wall cutout.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

PAPAYA = (255, 128, 0, 255)
DARK_GREY = (40, 40, 40, 255)
HALO_BLACK = (15, 15, 15, 255)
WHEEL_BLACK = (0, 0, 0, 255)
TRANSPARENT = (0, 0, 0, 0)


def make_car(length_cm: float, width_cm: float, px_per_meter: float) -> Image.Image:
    """Return an RGBA F1 silhouette, axis-aligned with the nose pointing image-up.

    Output image dimensions are `(round(width_cm/100 * px_per_meter),
    round(length_cm/100 * px_per_meter))`.
    """
    width_px = max(1, round(width_cm / 100.0 * px_per_meter))
    length_px = max(1, round(length_cm / 100.0 * px_per_meter))

    img = Image.new("RGBA", (width_px, length_px), TRANSPARENT)
    draw = ImageDraw.Draw(img)

    cx = width_px / 2.0

    body_w = 0.55 * width_px
    body_h = 0.80 * length_px
    body_left = cx - body_w / 2.0
    body_right = cx + body_w / 2.0
    body_top = 0.10 * length_px
    body_bottom = body_top + body_h
    body_radius = max(1.0, 0.20 * body_w)
    draw.rounded_rectangle(
        (body_left, body_top, body_right, body_bottom),
        radius=body_radius,
        fill=PAPAYA,
    )

    fw_h = max(1.0, 0.07 * length_px)
    fw_top = 0.02 * length_px
    draw.rectangle(
        (0, fw_top, width_px, fw_top + fw_h),
        fill=DARK_GREY,
    )

    rw_w = 0.85 * width_px
    rw_h = max(1.0, 0.05 * length_px)
    rw_left = cx - rw_w / 2.0
    rw_right = cx + rw_w / 2.0
    rw_bottom = 0.98 * length_px
    rw_top = rw_bottom - rw_h
    draw.rectangle(
        (rw_left, rw_top, rw_right, rw_bottom),
        fill=DARK_GREY,
    )

    ck_w = 0.28 * width_px
    ck_h = 0.10 * length_px
    ck_top = 0.42 * length_px
    draw.ellipse(
        (cx - ck_w / 2.0, ck_top, cx + ck_w / 2.0, ck_top + ck_h),
        fill=HALO_BLACK,
    )

    wheel_r = max(1.0, 0.20 * width_px)
    for wy in (body_top, body_bottom):
        for wx in (body_left, body_right):
            draw.ellipse(
                (wx - wheel_r, wy - wheel_r, wx + wheel_r, wy + wheel_r),
                fill=WHEEL_BLACK,
            )

    return img
