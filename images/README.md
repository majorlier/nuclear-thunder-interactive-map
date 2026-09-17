# Local image assets

- `slots/`: compact hover icons. The sync writes both the mission unit-class
  filename and (when different) a model-keyed alias, so two BLK classes that
  share one model use the same icon (for example both M192 variants can use
  `mim_23_hawk_launcher_m192.png`).
- `portraits/`: large vehicle-card artwork, named `av_<model>_001.png` (the legacy `icons/av_…` location remains supported).
- `icons/vanilla/`: neutral map-role symbols such as `def_spaa_radar.svg`.
- `icons/nato/`: NATO ally/enemy symbols (`def_nato_ally_*` and
  `def_nato_enemy_*`).
- `icons/pact/`: Pact/USSR symbols (`def_ussr_*`).

These role SVGs are white and transparent in the datamine. The map applies
REDFOR/BLUFOR colour filters at render time instead of storing pre-coloured
copies.

The **Icons** selector in the map switches the active family globally. NATO is
the default; Vanilla uses neutral `def_*` symbols and Pact uses `def_ussr_*`.

The map uses local assets first. Missing slot art falls back to the team NATO symbol; missing portrait art can still fall back to the existing local artwork and encyclopedia image where available.

To populate these folders from a full local datamine checkout, run
`python tools/sync_datamine_assets.py --datamine <path-to-datamine>` from the
map repository. The generated JSON report records the source path and whether
each map unit was copied, already present, or missing.
