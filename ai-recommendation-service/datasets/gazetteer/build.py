#!/usr/bin/env python3
"""Builds ai-recommendation-service/datasets/gazetteer/places.csv from GeoNames country dumps. See docs/datasets.md for
the attribution.

    python datasets/gazetteer/build.py <folder holding NA.zip BW.zip ZW.zip ZM.zip ZA.zip AO.zip>

Most GeoNames entries are farms, wells and hills whose names are ordinary words: "Rock", "Long", "Sun". Matching those
would turn a query's words into false place filters. So this keeps settlements of some size, parks, and named
landforms a visitor would ask for, and only their main names. A few well-known short names GeoNames doesn't have are
added by hand.
"""

from __future__ import annotations

import csv
import io
import sys
import zipfile
from pathlib import Path

COUNTRIES = ("NA", "BW", "ZW", "ZM", "ZA", "AO")
ALWAYS = {"PPLC", "PPLA", "PPLA2", "PRK", "RESN", "RESW", "RES", "CNYN"}
NAMIBIA_LANDFORMS = {"PAN", "DUNE", "DSRT", "FLLS", "PLAT", "CAPE"}
MIN_POPULATION = {"NA": 1000}
DEFAULT_MIN_POPULATION = 20000
# name, latitude, longitude, country: what visitors say, where GeoNames has only a longer or another name.
ALIASES = [
    ("Etosha", -18.855, 16.329, "NA"), ("Etosha National Park", -18.855, 16.329, "NA"),
    ("Etosha-Nationalpark", -18.855, 16.329, "NA"), ("Etosha Pan", -18.75, 16.25, "NA"),
    ("Sossusvlei", -24.733, 15.293, "NA"), ("Sesriem", -24.487, 15.801, "NA"), ("Deadvlei", -24.76, 15.29, "NA"),
    ("Fish River Canyon", -27.583, 17.583, "NA"), ("Fischfluss-Canyon", -27.583, 17.583, "NA"),
    ("Skeleton Coast", -20.0, 13.1, "NA"), ("Skelettküste", -20.0, 13.1, "NA"), ("Damaraland", -20.35, 14.3, "NA"),
    ("Kaokoland", -18.5, 13.0, "NA"), ("Caprivi", -17.8, 23.0, "NA"), ("Zambezi Region", -17.8, 24.0, "NA"),
    ("Twyfelfontein", -20.595, 14.373, "NA"), ("Spitzkoppe", -21.827, 15.195, "NA"),
    ("Waterberg", -20.507, 17.238, "NA"), ("Waterberg Plateau", -20.507, 17.238, "NA"),
    ("Kolmanskop", -26.705, 15.231, "NA"), ("Kolmannskuppe", -26.705, 15.231, "NA"),
    ("Luderitz", -26.648, 15.159, "NA"), ("Lüderitz", -26.648, 15.159, "NA"),
    ("Walfischbucht", -22.957, 14.508, "NA"), ("Epupa", -17.0, 13.246, "NA"), ("Epupa Falls", -17.0, 13.246, "NA"),
    ("Ai-Ais", -27.917, 17.483, "NA"), ("Cape Cross", -21.774, 13.952, "NA"), ("Kreuzkap", -21.774, 13.952, "NA"),
    ("Naukluft", -24.26, 16.24, "NA"), ("Namib", -24.0, 15.3, "NA"), ("Kalahari", -24.5, 20.0, "NA"),
    ("Okavango", -19.283, 22.9, "BW"), ("Okavango Delta", -19.283, 22.9, "BW"), ("Okavango-Delta", -19.283, 22.9, "BW"),
    ("Chobe", -18.667, 24.5, "BW"), ("Makgadikgadi", -20.75, 25.5, "BW"), ("Kasane", -17.817, 25.15, "BW"),
    ("Maun", -19.983, 23.417, "BW"), ("Victoria Falls", -17.925, 25.857, "ZW"), ("Viktoriafälle", -17.925, 25.857, "ZW"),  # noqa: E501
    ("Hwange", -18.629, 26.5, "ZW"), ("Livingstone", -17.841, 25.854, "ZM"), ("Cape Town", -33.925, 18.424, "ZA"),
    ("Kapstadt", -33.925, 18.424, "ZA"), ("Kgalagadi", -25.6, 20.8, "ZA"), ("Namaqualand", -29.665, 17.886, "ZA"),
    ("Lubango", -14.917, 13.5, "AO"),
]  # fmt: skip
OUT = Path(__file__).with_name("places.csv")


def main(folder: str) -> None:
    rows: dict[str, tuple] = {}
    for country in COUNTRIES:
        with zipfile.ZipFile(Path(folder) / f"{country}.zip") as archive, archive.open(f"{country}.txt") as raw:
            for row in csv.reader(io.TextIOWrapper(raw, encoding="utf-8"), delimiter="\t", quoting=csv.QUOTE_NONE):
                name, latitude, longitude, feature_class, code, population = (
                    row[1],
                    row[4],
                    row[5],
                    row[6],
                    row[7],
                    int(row[14] or 0),
                )
                keep = (
                    code in ALWAYS
                    or (feature_class == "P" and population >= MIN_POPULATION.get(country, DEFAULT_MIN_POPULATION))
                    or (country == "NA" and code in NAMIBIA_LANDFORMS and row[3])
                )
                if keep and len(name) > 3:
                    current = rows.get(name)
                    if current is None or population > current[4]:
                        rows[name] = (name, float(latitude), float(longitude), country, population)
    for name, latitude, longitude, country in ALIASES:
        rows[name] = (name, latitude, longitude, country, rows.get(name, (0, 0, 0, 0, 0))[4])
    with OUT.open("w", encoding="utf-8", newline="\n") as out:
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(["name", "latitude", "longitude", "country", "population"])
        writer.writerows(sorted(rows.values()))
    print(f"{len(rows)} places")


if __name__ == "__main__":
    main(sys.argv[1])
