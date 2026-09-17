# Nuclear Thunder Interactive Map

Community-built interactive reference map for War Thunder's Nuclear Escalation event.

## Use the map

- Choose **Archipelago** or **South Eastern City** first. Archipelago has the
  terrain-height overlay; City has its own extracted vehicle-road network and
  navigation mesh for route previews.
- Choose a scenario. The selector converts the mission's rank range to BR
  using War Thunder's balance-level scale (`BR = 1 + rank / 3`). An explicit
  BR value from generated data takes precedence if one is provided.
- Use the **Icons** selector to switch the whole map between NATO (default),
  WT, and USSR role symbols.
- Use **Swap REDFOR / BLUFOR locations** to move each faction's units to the
  matching opposing-side location used by the alternate event placement.
- Open folders in the map controls to show forces, airfields, depots, routes, and range overlays.
- Click a unit to show its available sensor, weapon, reload, ammo, and respawn
  information. The legend also lists site compositions, convoy compositions,
  and mission-level respawn behavior.

## Data updates

The map checks the public [War Thunder Datamine](https://github.com/gszabi99/War-Thunder-Datamine) for Nuclear Escalation changes. Updates are prepared for review before they reach the public map.

The updater follows the mission's imported ship-set files as well as the
mission itself. This keeps the 1970 fleets separate from the 1980/2018 fleets
and places each ship at its mission-defined patrol route.

Terrain and roads are maintained separately from the event data because they come from the compiled game level.

### Extracting the City heightmap

South Eastern City stores terrain in the optimized `lmap/lndm` land-mesh stream,
not as an `HM2` block. To regenerate the developer heightmap, install/keep a
local copy of Dagor Asset Explorer and run:

```text
python tools/extract_lmap_heightmap.py F:\WarThunderDev\levels\air_south_eastern_city.bin --output-dir generated\southeastern_city_heightmap
```

Use `--asset-explorer <folder>` if Asset Explorer is not in its default local
folder, and `--resolution 2048` for a larger raster. The command writes a
topographic preview, an 8-bit preview, a float32 height grid, and JSON metadata.
The generated heightmap is a developer artifact; the interactive map continues
to use the separately extracted map image.

To test a local copy, run a small local web server from this folder and open
`http://localhost:8000` in a browser (for example, run
`python -m http.server 8000`). Opening `index.html` directly from disk
will block the data files in most browsers.

The updater can be run manually with `python tools/update_from_datamine.py`.
It downloads the pinned Nuclear Escalation inputs, regenerates all three map
variants and unit/range data, and records the source commit in
`datamine-lock.json`. Review the generated files and the map preview before
committing or merging the update proposal.

For unit definitions from your local full checkout, add
`--aces-root F:\WT_Stuff\War-Thunder-Datamine\aces.vromfs.bin_u`; the mission
inputs still come from the configured datamine revision unless you change the
manifest.

### Sync local vehicle art

If you have a full local clone of the datamine, copy the matching slot icons,
vehicle-card art, role SVGs, and the strategic-bomber spawn symbols into this
repository with:

```text
python tools/sync_datamine_assets.py --datamine F:\WT_Stuff\War-Thunder-Datamine
```

The command preserves existing local artwork. Add `--force` only when you want
to replace it. A complete per-unit result is written to
`generated/datamine_asset_sync_report.json`; the command also prints the units
that still need artwork. Keeping these files locally means the deployed map
does not depend on live Wiki or datamine URLs.

## Vehicle card art

Vehicle-card art is derived automatically from the unit's `model` field in
the datamine. Add missing local artwork as `images/portraits/<model>.png`; for
example, the MPQ-34 CWAR's model is `mim_23_hawk_radar_cwar`, so its file is
`images/portraits/mim_23_hawk_radar_cwar.png`. New units do not need an edit
to `index.html`.

Map role symbols are kept separately under `images/icons/vanilla`,
`images/icons/nato`, and `images/icons/pact`. They are transparent datamine
SVGs; team colour is applied by the map when rendered.

### In-game display names

Readable English unit names are stored in the datamine localization table
`lang.vromfs.bin_u/lang/units.csv` (the `_0` row is the full name). Refresh the
local lookup after changing `unit_specs.json` or pulling a new datamine:

```text
python tools/extract_display_names.py --units-csv F:\WT_Stuff\War-Thunder-Datamine\lang.vromfs.bin_u\lang\units.csv
```

This writes `unit_display_names.json`; the map uses it first and falls back to
the internal identifier only when a mission-only unit has no localization row.

The extracted unit specs also retain the datamine's `type`, `onRadarAs`, and
`expClass` fields. The renderer uses those HUD classifications before falling
back to mission role/name heuristics.

This is an unofficial community project and is not affiliated with Gaijin Entertainment.
