# Nuclear Thunder Interactive Map

Community-built interactive reference map for War Thunder's Nuclear Escalation event.

## Use the map

- Choose a scenario and check its displayed BR bracket(s).
- Use **Swap REDFOR / BLUFOR locations** to move each faction's units to the
  matching opposing-side location used by the alternate event placement.
- Open folders in the map controls to show forces, airfields, depots, routes, and range overlays.
- Click a unit to show its available sensor and weapon information.

## Data updates

The map checks the public [War Thunder Datamine](https://github.com/gszabi99/War-Thunder-Datamine) for Nuclear Escalation changes. Updates are prepared for review before they reach the public map.

Terrain and roads are maintained separately from the event data because they come from the compiled game level.

## BR brackets

The datamine defines scenario presets but does not say which vehicle BRs Gaijin
allows in them. Those labels are deliberately kept in
[`br_brackets.json`](br_brackets.json), separate from the generated data.

Before merging an automatic datamine proposal, edit the matching preset entry
if the event BR bracket changed. Each entry is a list, so a single bracket is:

```json
"rank-31-36": ["11.3–13.0"]
```

and a split 1980 bracket is:

```json
"rank-31-36": ["11.3–12.0", "12.0–13.0"]
```

The map shows these as `[11.3–12.0] [12.0–13.0]`. The automatic update keeps
this file unchanged and flags any detected scenario that has no BR label in the
workflow summary.

This is an unofficial community project and is not affiliated with Gaijin Entertainment.
