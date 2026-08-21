import argparse
import json
import os
import struct


HSPL_MAGIC = b"hspl"
ROAD_PREFIXES = (
    "decal_asphalt_road_highway_",
    "decal_road_gravel_sand_",
)


def align4(value):
    return (value + 3) & ~3


def cubic_bezier(start, control1, control2, end, t):
    inverse = 1.0 - t
    return [
        inverse**3 * start[axis]
        + 3.0 * inverse**2 * t * control1[axis]
        + 3.0 * inverse * t**2 * control2[axis]
        + t**3 * end[axis]
        for axis in range(3)
    ]


def parse_hspl_at(data, magic_offset):
    if magic_offset + 8 > len(data):
        return None

    spline_count = struct.unpack_from("<I", data, magic_offset + 4)[0]
    if not 1 <= spline_count <= 100_000:
        return None

    position = magic_offset + 8
    splines = []
    try:
        for _ in range(spline_count):
            name_length = struct.unpack_from("<I", data, position)[0]
            position += 4
            if not 1 <= name_length <= 1024:
                return None

            name_bytes = data[position : position + name_length]
            if len(name_bytes) != name_length:
                return None
            name = name_bytes.rstrip(b"\0").decode("utf-8")
            position += align4(name_length)

            node_count = struct.unpack_from("<I", data, position)[0]
            position += 4
            if not 2 <= node_count <= 1_000_000:
                return None

            float_count = node_count * 9
            byte_count = float_count * 4
            if position + byte_count > len(data):
                return None
            values = struct.unpack_from(f"<{float_count}f", data, position)
            position += byte_count

            nodes = []
            for node_index in range(node_count):
                offset = node_index * 9
                nodes.append(
                    {
                        "in": values[offset : offset + 3],
                        "position": values[offset + 3 : offset + 6],
                        "out": values[offset + 6 : offset + 9],
                    }
                )
            splines.append({"name": name, "nodes": nodes})
    except (UnicodeDecodeError, struct.error):
        return None

    return splines


def find_hspl_section(data):
    offset = 0
    while True:
        offset = data.find(HSPL_MAGIC, offset)
        if offset < 0:
            break
        splines = parse_hspl_at(data, offset)
        if splines and any(
            spline["name"].startswith(ROAD_PREFIXES) for spline in splines
        ):
            return splines
        offset += len(HSPL_MAGIC)
    raise ValueError("Could not find a valid road spline (hspl) section")


def sample_spline(spline, subdivisions):
    nodes = spline["nodes"]
    points = [nodes[0]["position"]]
    for index in range(len(nodes) - 1):
        current = nodes[index]
        following = nodes[index + 1]
        for step in range(1, subdivisions + 1):
            points.append(
                cubic_bezier(
                    current["position"],
                    current["out"],
                    following["in"],
                    following["position"],
                    step / subdivisions,
                )
            )
    return [[round(value, 2) for value in point] for point in points]


def main():
    parser = argparse.ArgumentParser(
        description="Extract renderable road splines from a compiled War Thunder level"
    )
    parser.add_argument("level", help="Path to the compiled level .bin")
    parser.add_argument(
        "--output",
        default="road_network.json",
        help="Output JSON path (default: road_network.json)",
    )
    parser.add_argument(
        "--subdivisions",
        type=int,
        default=6,
        help="Curve samples per pair of spline nodes (default: 6)",
    )
    args = parser.parse_args()

    if args.subdivisions < 1:
        parser.error("--subdivisions must be at least 1")

    with open(args.level, "rb") as source:
        data = source.read()

    splines = find_hspl_section(data)
    roads = []
    for spline in splines:
        name = spline["name"]
        if name.startswith("decal_asphalt_road_highway_"):
            surface = "asphalt"
        elif name.startswith("decal_road_gravel_sand_"):
            surface = "gravel_sand"
        else:
            continue
        roads.append(
            {
                "name": name,
                "surface": surface,
                "points": sample_spline(spline, args.subdivisions),
            }
        )

    output_path = os.path.abspath(args.output)
    with open(output_path, "w", encoding="utf-8") as destination:
        json.dump(roads, destination, separators=(",", ":"))

    asphalt = sum(road["surface"] == "asphalt" for road in roads)
    gravel = sum(road["surface"] == "gravel_sand" for road in roads)
    sampled_points = sum(len(road["points"]) for road in roads)
    print(
        f"Saved {len(roads)} roads ({asphalt} asphalt, {gravel} gravel/sand) "
        f"with {sampled_points} sampled points to {output_path}"
    )


if __name__ == "__main__":
    main()
