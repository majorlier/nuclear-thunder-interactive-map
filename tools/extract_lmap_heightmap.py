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

from terrain_raster import colorize_topography, write_height_raster


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
    # complete land bounding box, i.e. it is centred on (min_y + max_y) / 2.
    # (Centring on y_scale alone put the whole map min_y too high: the City
    # sea read as +25 m instead of the -50 m seabed under a 0 m water plane.)
    min_y = min(box[0][1] for box in land.cellBounds)
    max_y = max(box[1][1] for box in land.cellBounds)
    y_scale = 0.5 * (max_y - min_y)
    y_centre = 0.5 * (max_y + min_y)
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
                y_centre,
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


def remove_flat_sea_artifacts(height, water_level=0.0, max_relief=10.0, max_pixels=400):
    """Drop flat, featureless "islands" that the land mesh leaves in open sea.

    Sparse sea cells rasterize some constant-height seam vertices (about +5 m
    on South Eastern City) into diamond-shaped blobs that the game's tactical
    map does not show. A blob is removed only when it is small, never rises
    more than ``max_relief`` above the water and is enclosed by sea; real
    islands have relief and are kept. Returns the number of pixels reset.
    """
    try:
        from scipy import ndimage
    except ImportError:
        print("WARNING: scipy is not installed; flat sea artifacts were not removed")
        return 0
    land = height > water_level
    labels, count = ndimage.label(land)
    if not count:
        return 0
    index = np.arange(1, count + 1)
    sizes = ndimage.sum(land, labels, index)
    peaks = ndimage.maximum(height, labels, index)
    remove = np.zeros(count + 1, dtype=bool)
    remove[1:] = (sizes <= max_pixels) & (peaks <= water_level + max_relief)
    mask = remove[labels]
    if not mask.any():
        return 0
    sea = ~land
    # Replace with the surrounding seabed height.
    fill = ndimage.grey_erosion(np.where(sea, height, np.inf), size=3)
    for _ in range(64):
        pending = mask & ~np.isfinite(fill)
        if not pending.any():
            break
        fill = np.where(np.isfinite(fill), fill, ndimage.grey_erosion(fill, size=3))
    seabed = float(np.median(height[sea])) if sea.any() else water_level
    height[mask] = np.where(np.isfinite(fill[mask]), np.minimum(fill[mask], water_level - 1), seabed)
    return int(mask.sum())


def write_outputs(height, metadata, output_dir, stem, world_min, world_max, water_level, resolution):
    """Write the topography preview, the 16-bit height PNG and the f32 grid."""
    output_dir.mkdir(parents=True, exist_ok=True)
    pixel_size = (world_max - world_min) / resolution
    Image.fromarray(colorize_topography(height, pixel_size, water_level), "RGB").save(
        output_dir / f"{stem}_topography.webp", "WEBP", lossless=True, method=6
    )
    encoding = write_height_raster(output_dir / f"{stem}_height_16bit.webp", height)
    height.astype("<f4").tofile(output_dir / f"{stem}_height_f32.bin")
    metadata.update({
        "resolution": [resolution, resolution],
        "world_bounds_m": [world_min, world_max],
        "water_level_m": water_level,
        "actual_min_height_m": float(height.min()),
        "actual_max_height_m": float(height.max()),
        "height_encoding": encoding,
        "note": "Rasterized from embedded land-mesh vertices; it is not an HM2 source heightmap.",
    })
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("level", type=Path)
    parser.add_argument("--asset-explorer", type=Path, default=DEFAULT_ASSET_EXPLORER)
    parser.add_argument("--lod", type=int, default=0, choices=(0, 1))
    parser.add_argument("--resolution", type=int, default=1024)
    parser.add_argument("--output-dir", type=Path, default=Path("."))
    parser.add_argument(
        "--water-level",
        type=float,
        default=0.0,
        help="Sea-surface height in world metres (air_south_eastern_city.blkx water_level is 0)",
    )
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
    metadata["removed_flat_sea_artifact_pixels"] = remove_flat_sea_artifacts(height, args.water_level)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = "southeastern_city"
    write_outputs(height, metadata, args.output_dir, stem, world_min, world_max, args.water_level, args.resolution)
    metadata.update({
        "source": args.level.name,
        "stream": "lmap/lndm",
        "lod": args.lod,
    })
    (args.output_dir / f"{stem}_height_meta.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
