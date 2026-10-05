"""Small binary-fixture regressions; no installed game or optional decoders needed."""
import struct
import unittest

from extract_navmesh import parse_candidate, parse_vand
from extract_roads import sample_spline


class LevelExtractionTests(unittest.TestCase):
    def test_triangle_packed_pair_preserves_both_indices(self):
        # Version 7 has a 0x64-byte header and 32-byte face records.
        data = bytearray(0x64 + 4 * 12 + 32)
        data[:4] = b"VAND"
        struct.pack_into("<I", data, 4, 7)
        struct.pack_into("<II", data, 0x18, 1, 4)
        for index, vertex in enumerate([(0, 0, 0), (10, 0, 0), (0, 0, 10), (10, 0, 10)]):
            struct.pack_into("<3f", data, 0x64 + index * 12, *vertex)
        struct.pack_into("<IIH", data, 0x64 + 4 * 12, 42, 1 | (2 << 16), 3)
        mesh = parse_vand(bytes(data))
        self.assertEqual(mesh["vertex_offset"], 0x64)
        self.assertEqual(mesh["triangles"], [[1, 2, 3]])

    def test_out_of_bounds_second_index_is_rejected(self):
        data = bytearray(3 * 12 + 32)
        struct.pack_into("<IIH", data, 3 * 12, 1, 0 | (4 << 16), 2)
        self.assertIsNone(parse_candidate(bytes(data), 0, 3, 1))

    def test_road_sampling_preserves_world_endpoints(self):
        spline = {"nodes": [
            {"position": [100, 5, 200], "in": [100, 5, 200], "out": [120, 5, 220]},
            {"position": [200, 5, 300], "in": [180, 5, 280], "out": [200, 5, 300]},
        ]}
        points = sample_spline(spline, 6)
        self.assertEqual(len(points), 7)
        self.assertEqual(points[0], [100, 5, 200])
        self.assertEqual(points[-1], [200, 5, 300])


if __name__ == "__main__":
    unittest.main()
