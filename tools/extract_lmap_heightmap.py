#!/usr/bin/env python3
"""Extract a terrain height preview from Dagor's optimized ``lmap`` stream.

War Thunder's newer levels do not necessarily contain an HM2 heightmap.  Their
``lmap`` block contains the land-mesh vertex buffers instead.  This utility
uses the open-source Dagor Asset Explorer parser for the container/decompression
step, then decodes the packed SCTYPE_SHORT4N land vertices and rasterizes them
into a regular heightmap.

The Asset Explorer is intentionally an external dependency: it is a developer
tool and is not needed by the map web application.  Pass its checkout with
``--asset-explorer`` (the default is the path used by the project author).
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import types
from pathlib import Path

import numpy as np
from PIL import Image


DEFAULT_ASSET_EXPLORER = Path(r"F:\WT_Stuff\Dagor-Asset-Explorer-master")


def load_asset_explorer(root: Path):
    """Load the parser without requiring its optional Qt GUI dependencies."""
    src = root / "src" / "dae"
    if not src.is_dir():
        raise FileNotFoundError(f"Asset Explorer source directory not found: {src}")
    lib = root / "lib" / "daKernel-dev.dll"
    if not lib.is_file():
        raise FileNotFoundError(f"Asset Explorer decompression DLL not found: {lib}")

    # The parser imports Qt only for its GUI helpers.  Small stubs keep the
    # command-line extractor independent of PyQt5.
    qt = types.ModuleType("PyQt5")
    core = types.ModuleType("PyQt5.QtCore")
    widgets = types.ModuleType("PyQt5.QtWidgets")
    core.QDir = type("QDir", (), {})
    core.Qt = type("Qt", (), {})
    core.QObject = type("QObject", (), {})
    widgets.QFileDialog = type(
        "QFileDialog", (), {"ExistingFiles": 0, "ExistingFile": 0}
    )
    widgets.QDialog = type("QDialog", (), {})
    # zstandard is optional in the extractor's Python environment.  lmap's
    # current stream is Oodle-compressed, but the parser still imports this.
    zstandard = types.ModuleType("zstandard")
    zstandard.ZstdDecompressor = type("ZstdDecompressor", (), {})
    sys.modules.update(
        {
            "PyQt5": qt,
            "PyQt5.QtCore": core,
            "PyQt5.QtWidgets": widgets,
            "zstandard": zstandard,
        }
    )
    sys.path.insert(0, str(src))

    import util.log as dag_log

    # Parsing 1024 cells is otherwise extremely noisy.  Keep the parser's
    # behavior but silence its diagnostic logger for this batch operation.
    dag_log.log = lambda *args, **kwargs: None
    dag_log.addLevel = lambda: None
    dag_log.subLevel = lambda: None

    from parse.dbld import DagorBinaryLevelData
    from util.fileread import BinFile

    # Land classes are unrelated to terrain elevation and can contain version-
    # specific DataBlock records the generic explorer does not understand.
    DagorBinaryLevelData.LandMeshManager.LandMesh.LandClassManager.__init__ = (
        lambda self, *args: None
    )
    return DagorBinaryLevelData, BinFile


def find_lmap(data: bytes) -> bytes:
    """Return the payload of the first top-level lmap block."""
    if data[:8] != b"DBLD3x64":
        raise ValueError("Input is not a DBLD3x64 level")
    pos = 12
    while pos + 8 <= len(data):
        raw_length = struct.unpack_from("<I", data, pos)[0]
        length = raw_length & 0x3FFFFFFF
        end = pos + 4 + length
        if length < 4 or end > len(data):
            raise ValueError(f"Invalid top-level block at 0x{pos:X}")
        if data[pos + 4 : pos + 8] == b"lmap":
            return data[pos + 8 : end]
        pos = end
    raise ValueError("No lmap block was found")


def decode_land_vertices(level: Path, asset_explorer: Path, lod: int = 0):
    DagorBinaryLevelData, BinFile = load_asset_explorer(asset_explorer)
    payload = find_lmap(level.read_bytes())
    manager = DagorBinaryLevelData.LandMeshManager(
        BinFile(payload), level.stem, str(level)
    )
    land = manager.lmesh
    mvd = land.matVData
    mvd.computeData()
    raw = mvd._MatVData__file  # parser-owned BinFile; stable in Asset Explorer

    # lmap land vertices are four normalized int16 values (x, y, z, w),
    # interleaved at an 8-byte stride.  Decode the buffers once and reference
    # the ranges used by each cell's LOD mesh.
    vertex_buffers: list[np.ndarray] = []
    for i in range(mvd.getVDCount()):
        gvd = mvd.getGlobalVertexData(i)
        if gvd.getVertexStride() != 8:
            vertex_buffers.append(np.empty((0, 4), dtype=np.int16))
            continue
        offset = mvd.getVertexDataOffset(i)
        values = np.frombuffer(
            raw.getData(),
            dtype="<i2",
            count=gvd.getVertexCnt() * 4,
            offset=offset,
        )
        vertex_buffers.append(values.reshape(-1, 4))

    # The world transform is the one used by LandMeshManager for packed land
    # vertices.  x/z are local to a 4096m cell; y is normalized against the
    # complete land bounding box.
    min_y = min(box[0][1] for box in land.cellBounds)
    max_y = max(box[1][1] for box in land.cellBounds)
    y_scale = 0.5 * (max_y - min_y)
    grid_x, grid_z = manager.mapSize
    cell_size = float(manager.landCellSize)
    origin_x, origin_z = manager.origin
    scale = np.array([cell_size * 0.5, y_scale, cell_size * 0.5])

    points: list[np.ndarray] = []
    for cell_id, cell in enumerate(land.cells):
        mesh = cell.land[lod][1]
        if mesh is None:
            continue
        x = cell_id % grid_x
        z = cell_id // grid_x
        offset = np.array(
            [
                cell_size * (x + origin_x + 0.5),
                y_scale,
                cell_size * (z + origin_z + 0.5),
            ]
        )
        for elem in mesh.elems:
            if elem.vData >= len(vertex_buffers):
                continue
            buf = vertex_buffers[elem.vData]
            start = max(0, elem.startV)
            end = min(buf.shape[0], start + max(0, elem.numV))
            if end <= start:
                continue
            points.append(buf[start:end, :3].astype(np.float32) / 32767.0 * scale + offset)

    if not points:
        raise ValueError(f"No packed land vertices found for LOD {lod}")
    return np.concatenate(points), {
        "grid_size": [int(grid_x), int(grid_z)],
        "cell_size_m": cell_size,
        "origin_cells": [int(origin_x), int(origin_z)],
        "height_bounds_m": [float(min_y), float(max_y)],
        "y_scale_m": float(y_scale),
        "landmesh_vertex_count": int(sum(len(p) for p in points)),
    }


def rasterize(points: np.ndarray, resolution: int, world_min: float, world_max: float):
    values = np.full((resolution, resolution), np.nan, dtype=np.float32)
    totals = np.zeros_like(values)
    counts = np.zeros_like(values, dtype=np.uint32)
    span = world_max - world_min
    ix = np.floor((points[:, 0] - world_min) / span * resolution).astype(np.int64)
    iz = np.floor((points[:, 2] - world_min) / span * resolution).astype(np.int64)
    valid = (ix >= 0) & (ix < resolution) & (iz >= 0) & (iz < resolution)
    flat = iz[valid] * resolution + ix[valid]
    np.add.at(totals.ravel(), flat, points[valid, 1])
    np.add.at(counts.ravel(), flat, 1)
    known = counts > 0
    values[known] = totals[known] / counts[known]

    # Fill sparse pixels from the mean of neighboring samples.  LOD0 has dense
    # coverage; this only closes cracks between triangle strips without leaving
    # directional streaks from copying the first neighbor encountered.
    for _ in range(20):
        if np.all(np.isfinite(values)):
            break
        old = values.copy()
        total = np.zeros_like(values)
        available = np.zeros_like(values, dtype=np.uint8)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            shifted = np.roll(old, (dy, dx), axis=(0, 1))
            finite = np.isfinite(shifted)
            total[finite] += shifted[finite]
            available[finite] += 1
        take = ~np.isfinite(values) & (available > 0)
        values[take] = total[take] / available[take]
    if not np.all(np.isfinite(values)):
        values[~np.isfinite(values)] = float(np.nanmean(values))
    return values[::-1]


def colorize(meters: np.ndarray, pixel_size: float) -> np.ndarray:
    gy, gx = np.gradient(meters, pixel_size)
    slope = np.pi / 2 - np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    shade = np.sin(np.deg2rad(42)) * np.sin(slope) + np.cos(np.deg2rad(42)) * np.cos(slope) * np.cos(np.deg2rad(315) - aspect)
    shade = np.clip((shade + 0.45) / 1.45, 0, 1)
    stops = np.array([-50, 0, 250, 500, 1000, 1750, 2750, 3750, 5000], dtype=np.float32)
    colors = np.array([[24, 65, 96], [70, 105, 68], [103, 126, 75], [144, 137, 83], [169, 126, 84], [151, 101, 82], [126, 105, 101], [151, 145, 137], [224, 222, 211]], dtype=np.float32)
    rgb = np.stack([np.interp(meters, stops, colors[:, c]) for c in range(3)], axis=-1)
    rgb *= (0.55 + 0.62 * shade)[..., None]
    return np.clip(rgb, 0, 255).astype(np.uint8)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("level", type=Path)
    parser.add_argument("--asset-explorer", type=Path, default=DEFAULT_ASSET_EXPLORER)
    parser.add_argument("--lod", type=int, default=0, choices=(0, 1))
    parser.add_argument("--resolution", type=int, default=1024)
    parser.add_argument("--output-dir", type=Path, default=Path("."))
    args = parser.parse_args()
    if args.resolution < 64:
        parser.error("--resolution must be at least 64")

    points, metadata = decode_land_vertices(args.level, args.asset_explorer, args.lod)
    world_min = metadata["cell_size_m"] * metadata["origin_cells"][0]
    world_max = metadata["cell_size_m"] * (metadata["grid_size"][0] + metadata["origin_cells"][0])
    world_min_z = metadata["cell_size_m"] * metadata["origin_cells"][1]
    world_max_z = metadata["cell_size_m"] * (metadata["grid_size"][1] + metadata["origin_cells"][1])
    if (world_min, world_max) != (world_min_z, world_max_z):
        raise ValueError("This rasterizer currently requires a square lmap world extent")
    height = rasterize(points, args.resolution, world_min, world_max)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = "southeastern_city"
    Image.fromarray(colorize(height, (world_max - world_min) / args.resolution), "RGB").save(args.output_dir / f"{stem}_topography.webp", "WEBP", lossless=True, method=6)
    encoded = np.clip((height - metadata["height_bounds_m"][0]) / (metadata["height_bounds_m"][1] - metadata["height_bounds_m"][0]) * 65535, 0, 65535).astype("<u2")
    Image.fromarray((encoded >> 8).astype(np.uint8), "L").save(args.output_dir / f"{stem}_height_8bit.png", optimize=True)
    height.astype("<f4").tofile(args.output_dir / f"{stem}_height_f32.bin")
    metadata.update({
        "source": args.level.name,
        "stream": "lmap/lndm",
        "lod": args.lod,
        "resolution": [args.resolution, args.resolution],
        "world_bounds_m": [world_min, world_max],
        "actual_min_height_m": float(height.min()),
        "actual_max_height_m": float(height.max()),
        "note": "Rasterized from embedded land-mesh vertices; it is not an HM2 source heightmap.",
    })
    (args.output_dir / f"{stem}_height_meta.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
