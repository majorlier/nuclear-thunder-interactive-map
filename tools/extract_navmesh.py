#!/usr/bin/env python3
"""Extract the compact VAND navigation mesh embedded in a Dagor level.

The level's Lnav block is a small compressed wrapper around a VAND mesh.  The
export keeps the triangle mesh in world coordinates so the browser can build
an adjacency graph for route previews.  It is still an extraction of the
baked mesh, not a claim about runtime avoidance or dynamic obstacles.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
from pathlib import Path

import zstandard as zstd


DBLD_MAGIC = b"DBLD3x64"


def iter_dbld_blocks(data: bytes):
    if not data.startswith(DBLD_MAGIC) or len(data) < 48:
        return
    metadata_size = struct.unpack_from("<I", data, 12)[0]
    position = metadata_size + 0x10
    while position + 8 <= len(data):
        stored_size = struct.unpack_from("<I", data, position)[0]
        if stored_size < 4:
            break
        end = position + stored_size + 4
        if end > len(data):
            break
        tag = data[position + 4 : position + 8].rstrip(b"\0")
        payload = data[position + 8 : end]
        yield tag.decode("ascii", errors="replace"), payload
        position = end


def find_lnav_payload(data: bytes) -> bytes:
    for tag, payload in iter_dbld_blocks(data) or ():
        if tag == "Lnav":
            return payload
    raise ValueError("No Lnav block was found in the DBLD level")


def decompress_vand(payload: bytes) -> bytes:
    if payload.startswith(b"VAND"):
        return payload
    if len(payload) < 5:
        raise ValueError("Lnav payload is too short")
    # Dagor's compressed block prefix is three size bytes followed by a
    # compression method byte.  The compressed data is a zstd frame here.
    if payload[3] != 0x40:
        raise ValueError(f"Unsupported Lnav compression method 0x{payload[3]:02x}")
    return zstd.ZstdDecompressor().decompress(payload[4:])


def parse_candidate(data: bytes, vertex_offset: int, vertex_count: int, triangle_count: int):
    vertex_end = vertex_offset + vertex_count * 12
    records_end = vertex_end + triangle_count * 32
    if vertex_offset < 0 or records_end > len(data):
        return None

    vertices = [
        list(struct.unpack_from("<3f", data, vertex_offset + index * 12))
        for index in range(vertex_count)
    ]
    if not all(all(math.isfinite(value) for value in vertex) for vertex in vertices):
        return None

    triangles = []
    non_degenerate = 0
    for index in range(triangle_count):
        record = vertex_end + index * 32
        _, packed, third = struct.unpack_from("<IHH", data, record)
        triangle = [packed & 0xFFFF, packed >> 16, third]
        if any(value >= vertex_count for value in triangle):
            return None
        triangles.append(triangle)
        if len(set(triangle)) == 3:
            non_degenerate += 1

    if not triangles or non_degenerate < triangle_count * 0.9:
        return None
    return vertices, triangles, non_degenerate


def parse_vand(data: bytes):
    if not data.startswith(b"VAND") or len(data) < 0x48:
        raise ValueError("Decoded navigation data is not a VAND mesh")

    version = struct.unpack_from("<I", data, 4)[0]
    triangle_count = struct.unpack_from("<I", data, 0x18)[0]
    vertex_count = struct.unpack_from("<I", data, 0x1C)[0]
    if not 1 <= vertex_count <= 10_000_000:
        raise ValueError(f"Invalid VAND vertex count: {vertex_count}")
    if not 1 <= triangle_count <= 10_000_000:
        raise ValueError(f"Invalid VAND triangle count: {triangle_count}")

    # Version 7 city meshes commonly carry a quantization/bounds header before
    # the vertex array.  Try the known layouts and select the one whose
    # triangle records form a valid non-degenerate mesh.
    candidates = []
    for vertex_offset in (0x48, 0x64, 0x80, 0xA0, 0xC0):
        parsed = parse_candidate(data, vertex_offset, vertex_count, triangle_count)
        if parsed:
            candidates.append((parsed[2], vertex_offset, parsed))
    if not candidates:
        raise ValueError("Could not locate a valid VAND vertex/triangle layout")
    _, vertex_offset, (vertices, triangles, _) = max(candidates)

    bounds = {
        "min": [min(vertex[axis] for vertex in vertices) for axis in range(3)],
        "max": [max(vertex[axis] for vertex in vertices) for axis in range(3)],
    }
    return {
        "format": "VAND",
        "version": version,
        "vertex_offset": vertex_offset,
        "triangle_record_stride": 32,
        "vertices": vertices,
        "triangles": triangles,
        "bounds": bounds,
    }


def main():
    parser = argparse.ArgumentParser(description="Extract a Dagor Lnav/VAND navigation mesh")
    parser.add_argument("level", type=Path)
    parser.add_argument("--output", type=Path, default=Path("navmesh.json"))
    args = parser.parse_args()

    vand = decompress_vand(find_lnav_payload(args.level.read_bytes()))
    mesh = parse_vand(vand)
    mesh["source"] = args.level.name
    args.output.write_text(json.dumps(mesh, separators=(",", ":")), encoding="utf-8")
    print(
        f"Saved VAND v{mesh['version']} with {len(mesh['vertices'])} vertices and "
        f"{len(mesh['triangles'])} triangles to {args.output}"
    )


if __name__ == "__main__":
    main()
