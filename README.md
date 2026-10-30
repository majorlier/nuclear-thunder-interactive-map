# Nuclear Thunder Interactive Map

English | [简体中文](README.zh-CN.md)

Community-built interactive reference map for War Thunder's Nuclear Escalation event.

## Use the map

- Choose **Archipelago**, **South Eastern City** or **Kamchatka** (in-game
  "Volcano Valley", added in 2.60) first. Archipelago has the terrain-height
  overlay; City has its own extracted vehicle-road network and navigation mesh
  for route previews. Kamchatka has terrain height (line of sight, threat
  cover and the topographic layer work), but no mirrored mission in the
  datamine yet, so Swap sides is unavailable there; its level yields no usable
  road network, so convoys show straight-line previews.
- Choose a scenario. The selector converts the mission's rank range to BR
  using War Thunder's balance-level scale (`BR = 1 + rank / 3`), with the lower
  bound raised to the event's 8.7 minimum BR (a matchmaking rule that is not
  stored in the client files). An explicit BR value from generated data takes
  precedence if one is provided.
- Use the **Icons** selector to switch the whole map between NATO (default),
  WT, and USSR role symbols.
- Use **Swap sides** on Archipelago or South Eastern City to load its game-defined mirrored mission,
  including the opposing-side site locations and mission routes.
- Open folders in the map controls to show forces, airfields, depots, routes, and range overlays.
- Use **Threat cover** in the map tools to shade where one team's radars
  (yellow) and air-defence weapons (red) can see an aircraft at a chosen
  altitude above ground or sea level, including terrain masking. It uses the
  same extracted ranges as the range rings and does not model SAM altitude
  limits, radar clutter, or Earth curvature.
- Use **Language** to switch the interface between English and Simplified
  Chinese. The first visit follows the browser preference; the choice is then
  remembered.
- The selected map, scenario, side, icon style, language, drawer states, and
  threat settings are remembered in the browser between visits.
- Click a unit to show its available sensor, weapon, reload, ammo, and respawn
  information. The legend also lists site compositions, convoy compositions,
  and mission-level respawn behavior.

## Data updates

The map checks the public [War Thunder Datamine](https://github.com/gszabi99/War-Thunder-Datamine) for Nuclear Escalation changes. Updates are prepared for review before they reach the public map.

The updater follows the mission's imported ship-set files as well as the
mission itself. This keeps the 1970 fleets separate from the 1980/2018 fleets
and places each ship at its mission-defined patrol route.

Terrain and roads are maintained separately from the event data because they come from the compiled game level.

### Extracting the terrain heightmaps

Both maps read heights from the installed game client's compiled levels
(`<War Thunder>\levels\*.bin`). Each extractor writes a shaded topographic
overlay, a 16-bit height raster (`*_16bit.webp`: red = high byte, green = low
byte, 0.25 m steps) and a metadata JSON that the map reads at load time, so a
re-export needs no change to `index.html`. Heights are absolute metres; the
sea surface is 0 m on both maps.

Archipelago stores an `HM2` heightmap. Decoding needs Oodle, provided by the
`pyooz` package (`pip install pyooz`; `--ooz <ooz.exe>` also works):

```text
python tools/extract_heightmap.py "F:\Steam\steamapps\common\War Thunder\levels\air_archipelago.bin" --output-dir .
```

The current South Eastern City level also contains `HM2`. Use the same
extractor with a City filename prefix so Archipelago assets are not overwritten:

```text
python tools/extract_heightmap.py "<War Thunder>\levels\air_south_eastern_city.bin" --output-dir generated\southeastern_city_heightmap --output-stem southeastern_city --height-downsample 2
```

Kamchatka (2.60) works the same way; its level sets the water level to -5 m:

```text
python tools/extract_heightmap.py "<War Thunder>\levels\air_kamchatka.bin" --output-dir generated\kamchatka_heightmap --output-stem kamchatka --height-downsample 2 --water-level -5
```

If `pyooz` is unavailable, add `--asset-explorer <folder>` to use the local
Dagor Asset Explorer decompression DLL on Windows, or use `--ooz <ooz.exe>`.
City's source is a 4096 x 4096 grid at 32 m spacing; the browser receives a
2048 x 2048 raster at 64 m spacing after averaging 2 x 2 source cells. Both
maps retain 0.25 m vertical precision and bilinear height sampling.

For older City levels without `HM2`, `tools/extract_lmap_heightmap.py` remains
available as a land-mesh fallback (`--resolution 2048` for a larger raster).

Refresh City roads and navigation from that same installed level:

```text
python tools/extract_roads.py "<War Thunder>\levels\air_south_eastern_city.bin" --include-local-roads --output road_network_city.json
python tools/extract_navmesh.py "<War Thunder>\levels\air_south_eastern_city.bin" --output navmesh_city.json
```

Navigation extraction needs `zstandard` (`pip install zstandard`). Route
previews are reconstructed from the exported road geometry and navigation
mesh, not recordings of the game's live AI paths. The tactical background
image is maintained separately and is not replaced by these commands.

To test a local copy, run a small local web server from this folder and open
`http://localhost:8000` in a browser (for example, run
`python -m http.server 8000`). Opening `index.html` directly from disk
will block the data files in most browsers.

The updater can be run manually with `python tools/update_from_datamine.py`.
It downloads the pinned Nuclear Escalation inputs, regenerates all four map
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
to replace it. If it copies new role SVGs, rebuild the bundled icon set the map
actually loads:

```text
python tools/build_icon_templates.py
```
 A complete per-unit result is written to
`generated/datamine_asset_sync_report.json`; the command also prints the units
that still need artwork. Keeping these files locally means the deployed map
does not depend on live Wiki or datamine URLs.

## Translations

Interface copy lives in `i18n/<locale>.json` and `en.json` is the fallback, so a
missing or half-finished pack never blanks the UI. Add a language by copying
`i18n/en.json`, translating the values, and listing its id in
`I18N_SUPPORTED_LOCALES` in `index.html`; the language selector is built from
that list.

Each pack names itself through its own `language.nativeName` entry (for example
`"English"` or `"简体中文"`), so the selector shows languages in their native
form and no pack has to carry translations for the others. Keep that value in
the language's own script; the locale id is only a fallback when it is missing.

Keys and `{placeholder}` names must match the English pack exactly, and every
`t("key")` or `data-i18n` key in the page must exist. `tools/check_frontend.js`
enforces both, so a stale translation fails CI before it reaches the map.

Names that come from data are not duplicated in the packs:

- unit names load from `unit_display_names.json` as
  `{"<locale>": {"<id>": "<name>"}}`, exported from `units.csv` for every
  language column the client ships; a flat `{id: name}` table is still accepted
  as the English default;
- weapon names load from `unit_weapon_display_names.json`, exported from
  `units_weaponry.csv`; a weapon without a row falls back to the cleaned
  identifier, so a missing translation never leaves an empty label;
- scenario labels keep their English default in `presets.json`, and a pack may
  override them with `preset.<id>` keys;
- map labels (`map.<id>`), team names and the internal unit roles
  (`role.<role>`) are translated by the pack, with the data value or the cleaned
  identifier as the fallback.

Units that are missing from `unit_display_names.json` still render through the
identifier cleanup, so new datamine entries show up before their names are
translated.

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

Readable unit names come from the datamine localization table
`lang.vromfs.bin_u/lang/units.csv` (the `_0` row is the full name). That table
has one column per language; the extractor writes the languages the map
offers (English and Simplified Chinese) in a single pass. Refresh the lookup after changing `unit_specs.json` or pulling a
new datamine:

```text
python tools/extract_display_names.py --units-csv F:\WT_Stuff\War-Thunder-Datamine\lang.vromfs.bin_u\lang\units.csv
```

This writes `unit_display_names.json` as `{"<locale>": {"<id>": "<name>"}}`. The
map prefers the current language, then English, and falls back to the internal
identifier only when an event-only unit has no localization row. Values are
cleaned of the zero-width spaces the Chinese columns use as word separators.

The `_1` row is exported as an extra `<id>#short` key only when it differs from
the full name. Site popups, airport defence lists and legend summaries use that
short label so a long vehicle name cannot overflow a marker button, while the
unit card behind them keeps the full `_0` name.

Weapon labels come from `lang.vromfs.bin_u/lang/units_weaponry.csv`, where each
weapon file name has a `weapons/<name>` row and sometimes a `/short` row. Those
keys keep the client's capitalisation, so matching is case-insensitive; the
short name is used when no full name exists, and weapons without any row keep
the cleaned identifier instead of an empty label:

```text
python tools/extract_weapon_names.py --weaponry-csv F:\WT_Stuff\War-Thunder-Datamine\lang.vromfs.bin_u\lang\units_weaponry.csv
```

This writes `unit_weapon_display_names.json` in the same locale keyed shape.

AI engagement rings come from each weapon's `aimMaxDist` / `attackMaxDist`
and the vehicle's `AttackMaxRadius`: the AI only fires inside the tightest of
these, which is often far shorter than the missile's flight range (the I-HAWK
flies 110 km but is aimed out to 40 km). A vehicle with several weapons uses
its longest-reaching one.

The extracted unit specs also retain the datamine's `type`, `onRadarAs`, and
`expClass` fields. The renderer uses those HUD classifications before falling
back to mission role/name heuristics.

This is an unofficial community project and is not affiliated with Gaijin Entertainment.
