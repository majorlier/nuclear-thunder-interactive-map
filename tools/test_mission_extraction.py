"""Regression checks for mission-template roles and runtime ship placement."""

import unittest

from build_map_data import (
    TemplateRegistry, create_buildings, discover_presets, imported_documents,
    launcher_spawn_area_name, lifecycle_for_unit, mission_site_category,
    mission_spawn_rank_rules, ship_with_mission_route, spawned_at_rank,
    trigger_objects_exist, unit_spawn_rank_rules,
)
from extract_mission_logic import extract_respawn_rules
from update_from_datamine import mission_import_paths, new_import_paths


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

    def test_import_nested_in_a_listed_fleet_file_is_discovered(self):
        # The fleet index imports one file per era. When Gaijin added the
        # 2000 set, the updater silently dropped it (and every 2018 ship)
        # because only mission files were searched for imports.
        sets = "mis.vromfs.bin_u/gamedata/missions/templates/units_sets/nuclear_escalation_ship_sets/"
        fleet_index = {"imports": {"import_record": [
            {"file": "gameData/missions/templates/units_sets/nuclear_escalation_ship_sets/nuclear_escalation_ships_1980.blk", "rankRange": [31, 38]},
            {"file": "gameData/missions/templates/units_sets/nuclear_escalation_ship_sets/nuclear_escalation_ships_2000.blk", "rankRange": [39, 50]},
        ]}}
        known = {sets + "nuclear_escalation_ships_1980.blkx"}
        self.assertEqual(
            new_import_paths([fleet_index], known),
            [sets + "nuclear_escalation_ships_2000.blkx"],
        )
        # Each path is reported once, and listed files are not re-fetched.
        self.assertEqual(new_import_paths([fleet_index, fleet_index], known), [])

    def ground_sets(self):
        """A 2.61-style mission: launchers and their triggers are imported."""
        ground = "gameData/missions/templates/units_sets/nuclear_escalation_ground_sets/"
        mission = {
            "imports": {"import_record": [
                {"file": ground + "nuclear_escalation_tbm.blk"},
                {"file": ground + "nuclear_escalation_mlrs.blk"},
            ]},
            "areas": {
                "t1_mlrs_spawn_area_01": {"tm": matrix(10, 0, 20)},
                "t1_tactical_missile_spawn_area_01": {"tm": matrix(30, 0, 40)},
            },
        }
        mlrs_trigger = {
            "conditions": {
                "varCompareInt": {
                    "var_value": "mission_rank", "value": 30,
                    "comparasion_func": "more",
                },
                "objectExists": [{"target": "t1_mlrs_spawn_area_01", "type": "area"}],
            },
            "actions": {
                "unitRespawn": [{"object": "t1_mlrs_01", "target": "t1_mlrs_spawn_area_01"}],
                "unitRespawnAfterTime": [{
                    "object_name": "t1_mlrs_01", "time_to_respawn": 600.0,
                }],
            },
        }
        missing_area_trigger = {
            "conditions": {"objectExists": [{"target": "t1_mlrs_spawn_area_05", "type": "area"}]},
            "actions": {"unitRespawnAfterTime": [{
                "object_name": "t1_mlrs_05", "time_to_respawn": 600.0,
            }]},
        }
        documents = {
            "nuclear_escalation_tbm": {"imports": {"import_record": [
                {"file": ground + "nuclear_escalation_tbm_1970.blk", "rankRange": [0, 38]},
                {"file": ground + "nuclear_escalation_tbm_2000.blk", "rankRange": [39, 50]},
            ]}},
            "nuclear_escalation_tbm_1970": {"units": {"tankModels": [
                {"name": "t1_tactical_missile_launcher_01", "unit_class": "tochka"},
            ]}},
            "nuclear_escalation_tbm_2000": {"units": {"tankModels": [
                {"name": "t1_tactical_missile_launcher_01", "unit_class": "iskander"},
            ]}},
            "nuclear_escalation_mlrs": {
                "units": {"tankModels": [{"name": "t1_mlrs_01", "unit_class": "smerch"}]},
                "triggers": {
                    "spawn_mlrs_01": mlrs_trigger,
                    "spawn_mlrs_05": missing_area_trigger,
                },
            },
        }
        return mission, documents

    def test_imported_launchers_follow_the_era_and_imported_triggers(self):
        mission, documents = self.ground_sets()

        def tbm_class(rank):
            return [
                unit["unit_class"]
                for document in imported_documents(mission, documents, rank)
                for unit in document.get("units", {}).get("tankModels", [])
                if "tactical" in unit["name"]
            ]

        self.assertEqual(tbm_class(37), ["tochka"])
        self.assertEqual(tbm_class(40), ["iskander"])
        # Without a rank every import is followed, both era files included.
        self.assertEqual(len(imported_documents(mission, documents)), 4)
        # The MLRS rank rule now lives in the imported file, not the mission.
        self.assertEqual(unit_spawn_rank_rules(mission), {})
        for rank, spawned in [(30, False), (31, True)]:
            rules = mission_spawn_rank_rules(mission, documents, rank)
            self.assertEqual(spawned_at_rank(rules, "t1_mlrs_01", rank), spawned)
        self.assertEqual(launcher_spawn_area_name("t1_mlrs_01"), "t1_mlrs_spawn_area_01")
        self.assertEqual(
            launcher_spawn_area_name("t2_tactical_missile_launcher_03"),
            "t2_tactical_missile_spawn_area_03",
        )
        self.assertIsNone(launcher_spawn_area_name("t1_longrange_radar_01"))

    def test_respawn_timers_come_from_imports_and_need_their_area(self):
        mission, documents = self.ground_sets()
        mlrs_triggers = documents["nuclear_escalation_mlrs"]["triggers"]
        self.assertTrue(trigger_objects_exist(mlrs_triggers["spawn_mlrs_01"], mission))
        self.assertFalse(trigger_objects_exist(mlrs_triggers["spawn_mlrs_05"], mission))
        rules = extract_respawn_rules(mission, imported_documents(mission, documents))
        self.assertEqual(
            [(rule["object"], rule["system"], rule["trigger"]) for rule in rules],
            [("t1_mlrs_01", "MLRS", "spawn_mlrs_01")],
        )

    def test_era_split_imports_add_scenario_boundaries(self):
        # Templates switch at 37 and 40, but the 2000-era ship set starts at
        # 39, so 37-39 would mix two fleets in one scenario.
        site = {"additionalEcsTemplates": {
            "assembly_area_1980": {"rankRange": [31, 36]},
            "assembly_area_2018_1": {"rankRange": [37, 39]},
            "assembly_area_2018_2": {"rankRange": [40, 50]},
        }}
        sets = "gameData/missions/templates/units_sets/nuclear_escalation_ship_sets/"
        mission = {
            "units": {"objectGroups": [site]},
            "imports": {"import_record": [{"file": sets + "nuclear_escalation_ships.blk"}]},
        }
        documents = {"nuclear_escalation_ships": {"imports": {"import_record": [
            {"file": sets + "nuclear_escalation_ships_1980.blk", "rankRange": [31, 38]},
            {"file": sets + "nuclear_escalation_ships_2000.blk", "rankRange": [39, 50]},
        ]}}}
        self.assertEqual(
            [preset["rank_range"] for preset in discover_presets(mission)],
            [[31, 36], [37, 39], [40, 50]],
        )
        self.assertEqual(
            [(preset["rank_range"], preset["label"]) for preset in discover_presets(mission, documents)],
            [([31, 36], "1980"), ([37, 38], "2018 1"), ([39, 39], "2018 2"),
             ([40, 50], "2018 3")],
        )

    def test_later_template_replaces_inherited_unit_list(self):
        # 2.61.0.42: tank_factory_2010 is tank_factory_2018 with the 2010
        # air-defence preset; its unit list replaces the 2018 one rather than
        # adding to it. A ...Templ building's units still add up.
        key = "units_spawn_on_init__unitSettingsTeam1:array"
        registry = TemplateRegistry({
            "preset_2018": {"_group": {key: {"unit:object": [{"name": "pantsir", "count": 1}]}}},
            "preset_2010": {"_group": {key: {"unit:object": {"name": "2s6", "count": 3}}}},
            "tank_factory_2018": {"_use": "preset_2018"},
            "tank_factory_2010": {"_use": ["tank_factory_2018", "preset_2010"]},
            "assembly_area_2010": {"assembly_area__tankFactoryTempl": "fortification+tank_factory_2010",
                                   "units_spawn_on_init__unitSettingsTeam1:array": {"unit:object": {"name": "truck"}}},
        })
        names = lambda templates: sorted(u["name"] for u in registry.unit_settings(templates, 1))
        self.assertEqual(names(["tank_factory_2018"]), ["pantsir"])
        self.assertEqual(names(["tank_factory_2010"]), ["2s6"])
        self.assertEqual(names(["assembly_area_2010"]), ["2s6", "truck"])
        self.assertEqual(names(["tank_factory_2010"]) and registry.unit_settings(["tank_factory_2010"], 2), [])

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
