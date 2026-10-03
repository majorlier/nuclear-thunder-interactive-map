#!/usr/bin/env python3
"""Extract and render the compiled HM2 terrain in a Dagor DBLD level.

The War Thunder level stores the 4096x4096 terrain as Oodle-compressed
8x8 blocks.  This script decodes those blocks with the ``pyooz`` Python
package (``pip install pyooz``), the open-source ``ooz`` command-line tool
(``--ooz``), or an already-decoded chunk directory (``--decoded-chunks``).

Example (live client)::

    python tools/extract_heightmap.py "F:\\Steam\\steamapps\\common\\War Thunder\\levels\\air_archipelago.bin" --output-dir .
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

from terrain_raster import colorize_topography, write_height_raster


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


def chunk_output_sizes(payload: bytes, chunks: list[bytes]) -> list[int]:
    width_height = struct.unpack_from("<I", payload, 20)[0]
    width = width_height & 0x1FFF
    height = (width_height >> 13) & 0x1FFF
    layout = struct.unpack_from("<I", payload, 44)[0]
    block_width = 1 << (layout & 0xFF)
    hierarchy_cell = (layout >> 8) & 0xFF
    block_count = (width // block_width) * (height // block_width)
    variance_chunk_size = width * height // (len(chunks) - 1)
    hierarchy_levels = int(math.log2(width // hierarchy_cell))
    hierarchy_bytes = (4**hierarchy_levels - 1) // 3
    return [
        block_count * 4 + hierarchy_bytes,
        *([variance_chunk_size] * (len(chunks) - 1)),
    ]


def decode_chunks_in_process(payload: bytes) -> list[bytes]:
    """Decode the Oodle chunks with the pyooz extension module."""
    import ooz  # provided by the ``pyooz`` package

    chunks = parse_nested_chunks(payload)
    return [
        bytes(ooz.decompress(chunk, size))
        for chunk, size in zip(chunks, chunk_output_sizes(payload, chunks))
    ]


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
    decoded: list[bytes], width: int, height: int, block_shift: int
) -> np.ndarray:
    block_width = 1 << block_shift
    blocks_x = width // block_width
    blocks_y = height // block_width
    block_count = blocks_x * blocks_y

    info = np.frombuffer(decoded[0], dtype="<u2", count=block_count * 2)
    info = info.reshape(block_count, 2)
    variance = np.concatenate(
        [np.frombuffer(chunk, dtype=np.uint8) for chunk in decoded[1:]]
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
    parser.add_argument(
        "--water-level",
        type=float,
        default=0.0,
        help="Sea-surface height in world metres (default 0; checked against the tactical map)",
    )
    parser.add_argument(
        "--height-downsample",
        type=int,
        default=2,
        help="Average NxN HM2 cells into the browser height raster (default 2: 4096 -> 2048 px)",
    )
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
        decoded = [path.read_bytes() for path in sorted(args.decoded_chunks.glob("chunk*.raw"))]
    elif args.ooz:
        temporary = tempfile.TemporaryDirectory(prefix="hm2_")
        decoded = [
            path.read_bytes()
            for path in decode_chunks(payload, args.ooz, Path(temporary.name))
        ]
    else:
        try:
            decoded = decode_chunks_in_process(payload)
        except ImportError:
            parser.error("install pyooz (pip install pyooz), or pass --ooz / --decoded-chunks")

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
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    Image.fromarray(
        colorize_topography(meters_image, cell_size, args.water_level), "RGB"
    ).save(
        output_dir / "topographic_map.webp",
        "WEBP",
        lossless=True,
        method=6,
    )
    encoding = write_height_raster(
        output_dir / "terrain_height_16bit.webp", meters_image, downsample=args.height_downsample
    )

    metadata = {
        "source": args.level.name,
        "width": width,
        "height": height,
        "cell_size_m": cell_size,
        "origin_x_m": origin_x,
        "origin_z_m": origin_z,
        "world_bounds_m": [origin_x, origin_x + cell_size * width],
        "water_level_m": args.water_level,
        "hm2_min_height_m": minimum_height,
        "hm2_height_range_m": height_range,
        "actual_min_height_m": float(meters.min()),
        "actual_max_height_m": float(meters.max()),
        "height_encoding": encoding,
    }
    (output_dir / "terrain_meta.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
