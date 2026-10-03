"""Shared raster output for the terrain extractors.

Both the HM2 (Archipelago) and lmap (South Eastern City) extractors produce
the same three browser assets:

* a shaded topographic preview (lossless WebP),
* a 16-bit height raster packed into an RGB PNG (red = high byte,
  green = low byte) because browsers only expose 8 bits per canvas channel,
* JSON metadata that tells the map how to decode that raster.

Heights are absolute world Y in metres.  ``water_level`` is the height of the
sea surface in the same coordinates (0 m on both current maps).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


HEIGHT_ENCODING = "rg16"


def colorize_topography(
    meters: np.ndarray, pixel_size: float, water_level: float = 0.0
) -> np.ndarray:
    """Hill-shaded hypsometric tint with 100 m / 500 m contour lines."""
    gradient_y, gradient_x = np.gradient(meters, pixel_size)
    slope = np.pi / 2 - np.arctan(np.hypot(gradient_x, gradient_y))
    aspect = np.arctan2(-gradient_x, gradient_y)
    azimuth = np.deg2rad(315)
    altitude = np.deg2rad(42)
    shade = (
        np.sin(altitude) * np.sin(slope)
        + np.cos(altitude) * np.cos(slope) * np.cos(azimuth - aspect)
    )
    shade = np.clip((shade + 0.45) / 1.45, 0, 1)

    relative = meters - water_level
    stops = np.array(
        [-50, 0, 250, 500, 1000, 1750, 2750, 3750, 5000],
        dtype=np.float32,
    )
    colors = np.array(
        [
            [24, 65, 96],
            [70, 105, 68],
            [103, 126, 75],
            [144, 137, 83],
            [169, 126, 84],
            [151, 101, 82],
            [126, 105, 101],
            [151, 145, 137],
            [224, 222, 211],
        ],
        dtype=np.float32,
    )
    rgb = np.stack(
        [np.interp(relative, stops, colors[:, channel]) for channel in range(3)],
        axis=-1,
    )
    # Water is drawn flat (no hill shading) so the sea reads as a surface
    # rather than as the seabed underneath it.
    land = relative >= 0
    rgb[land] *= (0.55 + 0.62 * shade)[land][..., None]
    rgb[~land] = np.array([24, 65, 96], dtype=np.float32) * 0.85

    land_height = np.maximum(relative, 0)
    minor_band = np.floor(land_height / 100).astype(np.int32)
    major_band = np.floor(land_height / 500).astype(np.int32)
    minor = land & (
        (minor_band != np.roll(minor_band, 1, axis=0))
        | (minor_band != np.roll(minor_band, 1, axis=1))
    )
    major = land & (
        (major_band != np.roll(major_band, 1, axis=0))
        | (major_band != np.roll(major_band, 1, axis=1))
    )
    rgb[minor] *= 0.66
    rgb[major] *= 0.55
    return np.clip(rgb, 0, 255).astype(np.uint8)


def write_height_raster(
    path: Path, meters: np.ndarray, step: float = 0.25, downsample: int = 1
) -> dict:
    """Write ``meters`` (image row order) as a 16-bit value packed into RGB.

    ``height = min_m + (red * 256 + green) * step_m``.  The default 0.25 m
    step keeps a 5 km relief range inside 16 bits, and lossless WebP keeps
    the browser asset close to the size of the old 8-bit PNG.
    """
    if downsample > 1:
        rows, cols = meters.shape
        rows -= rows % downsample
        cols -= cols % downsample
        meters = meters[:rows, :cols].reshape(
            rows // downsample, downsample, cols // downsample, downsample
        ).mean(axis=(1, 3))
    minimum = float(np.floor(np.nanmin(meters)))
    values = np.rint((meters - minimum) / step)
    if values.max() > 65535:
        raise ValueError(f"height range does not fit 16 bits at {step} m steps")
    values = values.astype(np.uint16)
    rgb = np.zeros((*values.shape, 3), dtype=np.uint8)
    rgb[..., 0] = values >> 8
    rgb[..., 1] = values & 0xFF
    image = Image.fromarray(rgb, "RGB")
    if path.suffix.lower() == ".webp":
        image.save(path, "WEBP", lossless=True, method=6)
    else:
        image.save(path, optimize=True)
    return {
        "format": HEIGHT_ENCODING,
        "image": path.name,
        "width": int(values.shape[1]),
        "height": int(values.shape[0]),
        "min_m": minimum,
        "step_m": step,
        "max_m": minimum + float(values.max()) * step,
    }
