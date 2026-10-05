"""Regression checks for mission-template roles and runtime ship placement."""

import unittest

from build_map_data import (
    TemplateRegistry, create_buildings, lifecycle_for_unit,
    mission_site_category, ship_with_mission_route,
    unit_spawn_rank_rules, spawned_at_rank,
)
from update_from_datamine import mission_import_paths


def matrix(x=0, y=0, z=0):
    return [[1, 0, 0], [0, 1, 0], [0, 0, 1], [x, y, z]]


class MissionExtractionTests(unittest.TestCase):
    def setUp(self):
        self.registry = TemplateRegistry({
            "assembly_area": {
                "assembly_area__tankFactoryUnitName": "factory",
                "assembly_area__fuelStorageUnitName": "fuel",
                "assembly_area__ammoStorageUnitName": "ammo",
            },
            "assembly_area_1980": {"_use": "assembly_area"},
            "fuel_factory_1980": {"fuel_factory:tag": {}},
            "ammo_factory_1980": {"ammo_factory:tag": {}},
            "restore_unit_by_timer": {},
            "cover": {"_use": "restore_unit_by_timer"},
        })
        self.site = {
            "unit_class": "nt_assembly_area_nomesh",
            "tm": matrix(100, 0, 200),
            "additionalEcsTemplates": {"assembly_area_1980": {}},
        }

    def test_assembly_role_does_not_depend_on_mesh(self):
        for model in ["nt_assembly_area_foundation", "nt_assembly_area_nomesh", "future_mesh"]:
            self.site["unit_class"] = model
            self.assertEqual(mission_site_category(self.site, self.registry), "assembly_area")

    def test_typed_factory_tags(self):
        for role in ["fuel_factory", "ammo_factory"]:
            site = {"additionalEcsTemplates": {"fortification+" + role + "_1980": {}}}
            self.assertEqual(mission_site_category(site, self.registry), role)

    def test_nomesh_depot_keeps_buildings_and_cover_lifecycle(self):
        layout = {"slots": [
            {"slot_type": "fuel_storage", "local_pos": [10, 0, 20]},
            {"slot_type": "ammo_storage", "local_pos": [-10, 0, -20]},
        ]}
        buildings, used = create_buildings(
            self.site, layout, ["assembly_area_1980"], [], self.registry,
        )
        self.assertEqual([b["name"] for b in buildings], ["factory", "fuel", "ammo"])
        self.assertEqual(buildings[1]["world_pos"], [110, 0, 220])
        self.assertEqual(used, {0, 1})
        self.assertEqual(lifecycle_for_unit("cover", self.site, self.registry)["kind"], "repair_with_site")

    def test_ship_uses_mission_route_not_zero_placeholder(self):
        ship = {"name": "t1_aircraftcarrier_01", "_source_group": "ships", "tm": matrix()}
        mission = {"wayPoints": {"t1_aircraftcarrier_01_waypoints": {
            "closed_waypoints": True,
            "way": {"first": {"tm": matrix(1000, 0, 2000)}},
        }}}
        self.assertEqual(ship_with_mission_route(ship, mission)["tm"][3], [1000, 0, 2000])
        self.assertIsNone(ship_with_mission_route(ship, {}))

    def test_shared_fleet_import_is_discovered_without_carrier_files(self):
        mission = {"imports": {"import_record": [{
            "file": "gameData/missions/templates/units_sets/nuclear_escalation_ship_sets/nuclear_escalation_ships_1980.blk",
        }]}}
        self.assertEqual(mission_import_paths(mission), [
            "mis.vromfs.bin_u/gamedata/missions/templates/units_sets/nuclear_escalation_ship_sets/nuclear_escalation_ships_1980.blkx",
        ])

    def test_mlrs_spawn_only_above_1970_rank_boundary(self):
        mission = {"triggers": {"spawn_mlrs": {
            "conditions": {"varCompareInt": {
                "var_value": "mission_rank", "value": 30,
                "comparasion_func": "more",
            }},
            "actions": {"unitRespawn": [
                {"object": "t1_mlrs_01"}, {"object": "t2_mlrs_01"},
            ]},
        }}}
        rules = unit_spawn_rank_rules(mission)
        for unit in ["t1_mlrs_01", "t2_mlrs_01"]:
            for rank in [0, 26, 27, 30]:
                self.assertFalse(spawned_at_rank(rules, unit, rank))
            for rank in [31, 36, 37, 50]:
                self.assertTrue(spawned_at_rank(rules, unit, rank))


if __name__ == "__main__":
    unittest.main()
