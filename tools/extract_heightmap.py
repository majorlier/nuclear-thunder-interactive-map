#!/usr/bin/env python3
"""Extract and render the compiled HM2 terrain in a Dagor DBLD level.

The War Thunder level stores the 4096x4096 terrain as Oodle-compressed
8x8 blocks.  This script can either decode those blocks with the open-source
`ooz` command-line tool or reuse an already-decoded chunk directory.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image


def iter_dagor_blocks(data: bytes, start: int = 12):
    position = start
    while position + 8 <= len(data):
        raw_length = struct.unpack_from("<I", data, position)[0]
        length = raw_length & 0x3FFFFFFF
        end = position + 4 + length
        if length < 4 or end > len(data):
            raise ValueError(f"Invalid Dagor block at 0x{position:X}")
        yield data[position + 4 : position + 8], data[position + 8 : end]
        position = end


def find_hm2(data: bytes) -> bytes:
    if data[:8] != b"DBLD3x64":
        raise ValueError("Input is not a DBLD3x64 level")
    for tag, payload in iter_dagor_blocks(data):
        if tag.strip(b"\0") == b"HM2":
            return payload
    raise ValueError("No HM2 block was found")


def parse_nested_chunks(payload: bytes, start: int = 48) -> list[bytes]:
    chunks = []
    position = start
    while position + 4 <= len(payload):
        raw_length = struct.unpack_from("<I", payload, position)[0]
        length = raw_length & 0x3FFFFFFF
        if not length:
            break
        end = position + 4 + length
        if end > len(payload):
            raise ValueError("HM2 contains an invalid compressed chunk")
        chunks.append(payload[position + 4 : end])
        position = end
    if len(chunks) < 2:
        raise ValueError("HM2 does not contain the expected chunk layout")
    return chunks


def run_ooz(ooz: Path, packed: bytes, output_size: int, destination: Path):
    # The stock ooz CLI expects an eight-byte uncompressed-size prefix.
    source = destination.with_suffix(".ooz")
    source.write_bytes(struct.pack("<Q", output_size) + packed)
    subprocess.run(
        [str(ooz), "-f", str(source), str(destination)],
        check=True,
    )


def decode_chunks(payload: bytes, ooz: Path, destination: Path) -> list[Path]:
    width_height = struct.unpack_from("<I", payload, 20)[0]
    width = width_height & 0x1FFF
    height = (width_height >> 13) & 0x1FFF
    layout = struct.unpack_from("<I", payload, 44)[0]
    block_shift = layout & 0xFF
    hierarchy_cell = (layout >> 8) & 0xFF
    block_width = 1 << block_shift
    block_count = (width // block_width) * (height // block_width)

    chunks = parse_nested_chunks(payload)
    variance_chunk_size = width * height // (len(chunks) - 1)
    hierarchy_levels = int(math.log2(width // hierarchy_cell))
    hierarchy_bytes = (4**hierarchy_levels - 1) // 3
    output_sizes = [
        block_count * 4 + hierarchy_bytes,
        *([variance_chunk_size] * (len(chunks) - 1)),
    ]

    destination.mkdir(parents=True, exist_ok=True)
    decoded = []
    for index, (chunk, output_size) in enumerate(zip(chunks, output_sizes)):
        output = destination / f"chunk{index}.raw"
        run_ooz(ooz, chunk, output_size, output)
        decoded.append(output)
    return decoded


def reconstruct_height(
    decoded: list[Path], width: int, height: int, block_shift: int
) -> np.ndarray:
    block_width = 1 << block_shift
    blocks_x = width // block_width
    blocks_y = height // block_width
    block_count = blocks_x * blocks_y

    info = np.fromfile(decoded[0], dtype="<u2", count=block_count * 2)
    info = info.reshape(block_count, 2)
    variance = np.concatenate(
        [np.fromfile(path, dtype=np.uint8) for path in decoded[1:]]
    ).reshape(block_count, block_width * block_width)

    # Dagor delta-codes each block independently before Oodle compression.
    variance = np.cumsum(
        variance.astype(np.uint16), axis=1, dtype=np.uint16
    ).astype(np.uint8)
    minimum = info[:, 0].astype(np.uint32)[:, None]
    delta = info[:, 1].astype(np.uint32)[:, None]
    values = (
        minimum + (variance.astype(np.uint32) * delta + 127) // 255
    ).astype(np.uint16)
    return (
        values.reshape(blocks_y, blocks_x, block_width, block_width)
        .transpose(0, 2, 1, 3)
        .reshape(height, width)
    )


def colorize_topography(meters: np.ndarray) -> np.ndarray:
    gradient_y, gradient_x = np.gradient(meters, 64.0)
    slope = np.pi / 2 - np.arctan(np.hypot(gradient_x, gradient_y))
    aspect = np.arctan2(-gradient_x, gradient_y)
    azimuth = np.deg2rad(315)
    altitude = np.deg2rad(42)
    shade = (
        np.sin(altitude) * np.sin(slope)
        + np.cos(altitude) * np.cos(slope) * np.cos(azimuth - aspect)
    )
    shade = np.clip((shade + 0.45) / 1.45, 0, 1)

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

    rgb = np.empty((*meters.shape, 3), dtype=np.float32)
    for channel in range(3):
        rgb[..., channel] = np.interp(
            meters, stops, colors[:, channel]
        )
    rgb *= (0.55 + 0.62 * shade)[..., None]

    land_height = np.maximum(meters, 0)
    minor_band = np.floor(land_height / 100).astype(np.int32)
    major_band = np.floor(land_height / 500).astype(np.int32)
    land = meters >= 0
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("level", type=Path)
    parser.add_argument("--ooz", type=Path, help="Path to ooz or ooz.exe")
    parser.add_argument(
        "--decoded-chunks",
        type=Path,
        help="Reuse a directory containing chunk0.raw, chunk1.raw, ...",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("."))
    args = parser.parse_args()

    payload = find_hm2(args.level.read_bytes())
    cell_size, minimum_height, height_range, origin_x, origin_z = (
        struct.unpack_from("<5f", payload, 0)
    )
    width_height = struct.unpack_from("<I", payload, 20)[0]
    width = width_height & 0x1FFF
    height = (width_height >> 13) & 0x1FFF
    layout = struct.unpack_from("<I", payload, 44)[0]
    block_shift = layout & 0xFF

    if args.decoded_chunks:
        decoded = sorted(args.decoded_chunks.glob("chunk*.raw"))
    else:
        if not args.ooz:
            parser.error("--ooz is required unless --decoded-chunks is used")
        temporary = tempfile.TemporaryDirectory(prefix="hm2_")
        decoded = decode_chunks(payload, args.ooz, Path(temporary.name))

    if len(decoded) < 2:
        raise ValueError("Decoded chunk files were not found")
    encoded_height = reconstruct_height(
        decoded, width, height, block_shift
    )
    meters = (
        minimum_height
        + encoded_height.astype(np.float32) * (height_range / 65535.0)
    )

    # Dagor rows run south-to-north; image rows run top-to-bottom.
    meters_image = meters[::-1]
    encoded_image = encoded_height[::-1]
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    Image.fromarray(colorize_topography(meters_image), "RGB").save(
        output_dir / "topographic_map.webp",
        "WEBP",
        lossless=True,
        method=6,
    )
    Image.fromarray((encoded_image >> 8).astype(np.uint8), "L").save(
        output_dir / "terrain_height_8bit.png",
        optimize=True,
    )

    metadata = {
        "source": args.level.name,
        "width": width,
        "height": height,
        "cell_size_m": cell_size,
        "origin_x_m": origin_x,
        "origin_z_m": origin_z,
        "encoded_min_height_m": minimum_height,
        "encoded_height_range_m": height_range,
        "actual_min_height_m": float(meters.min()),
        "actual_max_height_m": float(meters.max()),
        "browser_readout_vertical_precision_m": height_range / 255.0,
    }
    (output_dir / "terrain_meta.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
