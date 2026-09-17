"""Extract English in-game display names from the War Thunder localization table.

The mission files use internal unit identifiers (for example
``us_ai_sam_launcher_m192``), while the client gets the readable labels from
``lang/units.csv``.  This script keeps a small local lookup used by the map;
the ``_0`` localization row is the full name, ``_1`` is the short name and
``_2`` is the class/category.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def clean(value: str) -> str:
    """Normalize localization whitespace without changing the display text."""
    return re.sub(r"\s+", " ", (value or "").replace("\u00a0", " ")).strip()


def load_names(csv_path: Path) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, delimiter=";")
        header = next(reader, [])
        if len(header) < 2 or "English" not in header[1]:
            raise ValueError(f"Unexpected units.csv header in {csv_path}")
        for row in reader:
            if len(row) < 2:
                continue
            key = row[0].strip().lower()
            if not key:
                continue
            english = clean(row[1])
            if english:
                rows[key] = {"full": english}
            if len(row) > 1 and english:
                rows[key]["english"] = english
    return rows


def candidates(unit_id: str) -> list[str]:
    raw = str(unit_id or "").replace("\\", "/").strip().lower()
    if not raw:
        return []
    tail = raw.rsplit("/", 1)[-1]
    values: list[str] = []
    for value in (raw, tail):
        if value and value not in values:
            values.append(value)
    return [f"{value}_0" for value in values] + [f"{value}_shop" for value in values]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--units-csv", type=Path, required=True,
                        help="Path to lang.vromfs.bin_u/lang/units.csv")
    parser.add_argument("--specs", type=Path, default=ROOT / "unit_specs.json")
    parser.add_argument("--output", type=Path, default=ROOT / "unit_display_names.json")
    args = parser.parse_args()

    names = load_names(args.units_csv)
    specs = json.loads(args.specs.read_text(encoding="utf-8-sig"))
    output: dict[str, str] = {}
    missing: list[str] = []

    for key, spec in sorted(specs.items()):
        unit_id = spec.get("unit_class") or key
        match = next((names[candidate] for candidate in candidates(unit_id)
                      if candidate in names), None)
        if match is None:
            missing.append(key)
            continue
        label = match["full"]
        output[str(key).lower()] = label
        tail = str(key).replace("\\", "/").rsplit("/", 1)[-1].lower()
        output.setdefault(tail, label)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(f"Wrote {len(output)} display-name keys for {len(specs)} units to {args.output}")
    if missing:
        print(f"Missing {len(missing)} units: {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
