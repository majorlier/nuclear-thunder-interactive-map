# Nuclear Thunder Interactive Map

An interactive reference map for War Thunder's **Nuclear Escalation** event. It includes event sites, forces for the 1970/1980/2018 eras, mission logistics, roads, terrain, and unit information.

## Using GitHub Desktop

The folder to open in GitHub Desktop is:

`F:\WT_Stuff\nuclear-thunder-interactive-map`

The similarly named `NuclearThunderMap_updated` folder is the older working archive. Keep it as a backup, but make new changes in the GitHub folder so GitHub Desktop can track them.

For a normal update:

1. Open GitHub Desktop and select this repository.
2. Click **Fetch origin**, then **Pull origin** if it appears.
3. Make or review the changes.
4. Check the changed-file list in GitHub Desktop.
5. Enter a short summary, click **Commit to current branch**, then **Push origin**.

Use a separate branch for substantial changes. That makes it easy to preview, compare, or discard an update without disturbing the published `main` branch.

## Automatic datamine checks

The repository contains a scheduled GitHub Action that checks [gszabi99's War Thunder Datamine](https://github.com/gszabi99/War-Thunder-Datamine) once per day. When relevant Nuclear Escalation files change, it:

1. Downloads the current mission and event templates.
2. Rebuilds `map_data.json` and `mission_logic.json`.
3. Validates eras, map positions, names, roads, and unit references.
4. Opens or refreshes a **draft pull request** for review.

It never pushes generated event data directly to `main`. Review the map preview and merge the draft only when the result looks correct.

After this workflow is first added to GitHub, enable **Allow GitHub Actions to create and approve pull requests** under **Repository Settings → Actions → General → Workflow permissions**. The workflow only needs permission to create its update branch and draft pull request.

You can also run it immediately from the repository's **Actions** tab by selecting **Check War Thunder datamine → Run workflow**.

## Checking locally

Python 3 is the only requirement for the event-data updater:

```powershell
python tools/update_from_datamine.py --check
python tools/validate_data.py
```

To actually replace local generated event files after checking:

```powershell
python tools/update_from_datamine.py
python tools/validate_data.py
```

The updater records the exact upstream commit and file fingerprints in `datamine-lock.json`, making later changes easy to audit.

## What still needs a local extraction

The public datamine provides the mission and event-template data used for sites and units. The road network, terrain height image, and topographic background come from the game's compiled level assets and are not rebuilt by the daily workflow. If Gaijin changes the actual terrain or roads, use the included local extraction tools with a current game extraction, then review those larger visual changes separately.

The helper scripts live in `tools/`:

- `build_map_data.py` builds event sites and era-specific units.
- `extract_mission_logic.py` builds logistics and mission overlays.
- `extract_unit_specs.py` refreshes unit detail cards from a local extracted game folder.
- `extract_roads.py` and `extract_heightmap.py` handle compiled level data.
- `validate_data.py` checks that the generated files agree with the interface.
