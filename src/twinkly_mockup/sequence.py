"""Lap sequence: the wall as a movie, with the car fixed and the track moving.

A still frame answers "what does the wall show with the car parked here?". A lap
sequence answers "what does the wall show while the car runs the optimal lap?",
and the difference is only *where the camera looks* — the LEGO car is physically
mounted to the wall and never moves, so the camera is rigidly attached to it and
the world rotates and translates beneath. `Mosaic.sample` already implements
exactly that, and `compose.compose_frame` already renders one such view; this
module is the loop that drives them along a `Trajectory`.

Two conventions meet here, and getting either backwards mirrors the lap:

* `Mosaic.sample(yaw_rad=…)` puts ENU direction `yaw_rad` on the output's
  image-**up** axis.
* The car silhouette is drawn nose-up and then rotated CCW by
  `car.orientation_deg`, so the nose points along image-up rotated CCW by that
  angle.

The nose therefore points along ENU direction `camera_yaw + orientation_deg`.
Setting that equal to the trajectory's heading gives `camera_yaw_for_heading`.
For the shipped `orientation_deg = -90` (nose image-right, into the wider half
of the asymmetric cutout) the bias is `+π/2` — the same `YAW_BIAS_RAD` that
`scripts/derive_corner_poses.py` applies when authoring still-frame snapshots.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .compose import compose_frame
from .config import CarSpec, Config
from .mosaic import Mosaic
from .trajectory import Trajectory

VIDEO_SUFFIX = ".mp4"
VIDEO_FOURCC = "mp4v"
FRAME_STEM = "frame"


def camera_yaw_for_heading(heading_rad: float, car: CarSpec) -> float:
    """Camera yaw that aims the mounted car's nose along `heading_rad` (ENU).

    See the module docstring: the nose points along `camera_yaw + orientation`,
    so the camera lags the heading by the car's mounting angle.
    """
    return heading_rad - math.radians(car.orientation_deg)


def frame_times(trajectory: Trajectory, fps: float) -> np.ndarray:
    """Playback times covering the whole lap at `fps`, endpoints included.

    Sampling rate and playback rate are the same number, so the movie runs in
    real time: a 85.44 s lap is 85.44 s of video at any `fps`. `Trajectory`
    interpolates between knots (wrap-safe in yaw, ADR-0001), so `fps` need not
    divide the trajectory's own `dt`.
    """
    if fps <= 0.0:
        raise ValueError(f"fps must be positive, got {fps}")
    t0 = float(trajectory.t[0])
    count = int(round(trajectory.duration * fps)) + 1
    return t0 + np.arange(count) / fps


@dataclass(frozen=True)
class LapRender:
    """What a lap render produced."""

    out_path: Path
    frame_count: int
    fps: float
    frame_size_px: tuple[int, int]

    @property
    def duration_s(self) -> float:
        """Playback duration — real time, since sampling rate == playback rate."""
        return self.frame_count / self.fps


def render_lap(
    config: Config,
    trajectory: Trajectory,
    fps: float,
    out_path: Path,
    *,
    progress: Callable[[int, int], None] | None = None,
) -> LapRender:
    """Render every frame of `trajectory` through `config`'s wall.

    Writes an MP4 when `out_path` ends in `.mp4`, otherwise a numbered PNG
    sequence in the directory `out_path`. `config.snapshot`'s pose is ignored —
    the trajectory supplies it — but everything else about the snapshot (mosaic,
    viewport, layout, car) still applies, so the movie is the same wall the
    still frames show. `progress`, if given, is called with `(done, total)`.
    """
    times = frame_times(trajectory, fps)
    mosaic = Mosaic.load(config.snapshot.mosaic)
    out_path = Path(out_path)
    sink = _open_sink(out_path, fps)

    try:
        for index, t in enumerate(times):
            x, y, heading = trajectory.sample_at(float(t))
            frame = compose_frame(
                config,
                mosaic,
                center_xy_m=(x, y),
                camera_yaw_rad=camera_yaw_for_heading(heading, config.car),
            )
            sink.write(index, frame)
            if progress is not None:
                progress(index + 1, len(times))
    finally:
        sink.close()

    return LapRender(
        out_path=out_path,
        frame_count=len(times),
        fps=fps,
        frame_size_px=sink.frame_size_px,
    )


class _PngSink:
    """Writes each frame as `frame_00000.png` in a directory."""

    def __init__(self, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        self._dir = out_dir
        self.frame_size_px: tuple[int, int] = (0, 0)

    def write(self, index: int, frame) -> None:
        self.frame_size_px = frame.size
        frame.save(self._dir / f"{FRAME_STEM}_{index:05d}.png", format="PNG")

    def close(self) -> None:
        pass


class _VideoSink:
    """Writes frames into an MP4 via OpenCV, opened lazily on the first frame.

    The writer needs the frame size up front and the layout does not hand it
    over until a frame exists, so the first `write` opens it.
    """

    def __init__(self, out_path: Path, fps: float) -> None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = out_path
        self._fps = fps
        self._writer: cv2.VideoWriter | None = None
        self.frame_size_px: tuple[int, int] = (0, 0)

    def write(self, index: int, frame) -> None:
        if self._writer is None:
            self.frame_size_px = frame.size
            self._writer = cv2.VideoWriter(
                str(self._path),
                cv2.VideoWriter_fourcc(*VIDEO_FOURCC),
                self._fps,
                frame.size,
            )
            if not self._writer.isOpened():
                raise RuntimeError(
                    f"could not open a {VIDEO_FOURCC} video writer for {self._path} "
                    f"at {frame.size[0]}×{frame.size[1]}"
                )
        self._writer.write(cv2.cvtColor(np.asarray(frame), cv2.COLOR_RGB2BGR))

    def close(self) -> None:
        if self._writer is not None:
            self._writer.release()


def _open_sink(out_path: Path, fps: float):
    if out_path.suffix.lower() == VIDEO_SUFFIX:
        return _VideoSink(out_path, fps)
    return _PngSink(out_path)
