"""Top-down photo of the LEGO Technic MCL39, masked to its silhouette.

Loads `img/IMG_5979.jpeg` once per process, isolates the car from the wood-grain
backdrop via OpenCV GrabCut (seeded with a tight bbox around the wheels and
wings), then resizes the masked RGBA to the model's physical pixel dimensions
at the rendered LED pitch. Output is axis-aligned with the nose pointing
image-up — `compose._paste_car` applies the configured CCW rotation when
dropping it into the cutout.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

PHOTO_PATH = Path(__file__).resolve().parents[2] / "img" / "IMG_5979.jpeg"

# Tight rect around the LEGO car in IMG_5979.jpeg (3024×4032). Chosen so the
# wheels and wings sit comfortably inside; everything outside is guaranteed
# wood-grain and seeds GrabCut as definite background.
_GRABCUT_RECT = (500, 20, 2000, 3992)  # (x, y, w, h) in original-photo px

# GrabCut runs on a downsampled copy for speed; the result is upscaled and
# binarised. Five iterations is enough at quarter resolution.
_GRABCUT_SCALE = 0.25
_GRABCUT_ITERS = 8


@lru_cache(maxsize=1)
def _masked_car_photo() -> Image.Image:
    """Load the LEGO car photo and return it RGBA with the wood-grain alpha'd out.

    Cached for the process lifetime — GrabCut takes a few seconds and the
    masked photo is the same for every render.
    """
    bgr = cv2.imread(str(PHOTO_PATH))
    if bgr is None:
        raise FileNotFoundError(f"car photo not found at {PHOTO_PATH}")
    h, w = bgr.shape[:2]

    small = cv2.resize(bgr, (0, 0), fx=_GRABCUT_SCALE, fy=_GRABCUT_SCALE)
    sm_rect = tuple(int(c * _GRABCUT_SCALE) for c in _GRABCUT_RECT)
    sm_mask = np.zeros(small.shape[:2], np.uint8)
    bgd = np.zeros((1, 65), np.float64)
    fgd = np.zeros((1, 65), np.float64)
    cv2.grabCut(small, sm_mask, sm_rect, bgd, fgd, _GRABCUT_ITERS, cv2.GC_INIT_WITH_RECT)
    sm_fg = ((sm_mask == cv2.GC_FGD) | (sm_mask == cv2.GC_PR_FGD)).astype(np.uint8) * 255

    fg = cv2.resize(sm_fg, (w, h), interpolation=cv2.INTER_LINEAR)
    fg = cv2.GaussianBlur(fg, (9, 9), 0)
    fg = (fg > 128).astype(np.uint8) * 255

    # Drop stray specks: keep only the largest connected component.
    n, lbls, stats, _ = cv2.connectedComponentsWithStats(fg, 8)
    if n > 1:
        largest = 1 + int(stats[1:, cv2.CC_STAT_AREA].argmax())
        fg = (lbls == largest).astype(np.uint8) * 255

    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    rgba = np.dstack([rgb, fg])

    # Crop to the silhouette's bbox so the wheels span the full output width
    # after resize — otherwise the wood-grain padding on either side of the
    # car shrinks the rendered silhouette inside the cutout.
    ys, xs = np.where(fg > 0)
    rgba = rgba[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    return Image.fromarray(rgba, mode="RGBA")


def make_car(length_cm: float, width_cm: float, px_per_meter: float) -> Image.Image:
    """Return the LEGO car photo masked to its silhouette and resized.

    Output image dimensions are `(round(width_cm/100 * px_per_meter),
    round(length_cm/100 * px_per_meter))`, axis-aligned with the nose pointing
    image-up.
    """
    width_px = max(1, round(width_cm / 100.0 * px_per_meter))
    length_px = max(1, round(length_cm / 100.0 * px_per_meter))
    return _masked_car_photo().resize((width_px, length_px), Image.LANCZOS)
