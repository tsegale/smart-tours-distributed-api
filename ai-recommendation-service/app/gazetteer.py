"""Place names for query understanding, from datasets/gazetteer (GeoNames, CC BY 4.0; see docs/datasets.md)."""

import csv
from pathlib import Path

from .query_parser import normalise

PLACES = Path(__file__).resolve().parents[1] / "datasets" / "gazetteer" / "places.csv"


def load_csv(path: Path | None = None) -> dict[str, tuple[str, float, float]]:
    """Normalised name -> (display name, latitude, longitude). The most populated place wins a shared name."""
    source = path or PLACES
    with source.open(encoding="utf-8", newline="") as lines:
        rows = sorted(csv.DictReader(lines), key=lambda row: int(row["population"]))
    return {normalise(row["name"]): (row["name"], float(row["latitude"]), float(row["longitude"])) for row in rows}
