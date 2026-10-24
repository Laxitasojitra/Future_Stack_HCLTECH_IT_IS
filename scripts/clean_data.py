"""Remove Kaggle-only columns from the raw CSVs (run once; already applied to data/raw).

A column is removed when its name starts with "kaggle_" or when it is a "source"
column whose values are "kaggle"/"added" (the Kaggle provenance flag). Rows are
kept. Columns such as drug_interactions.source, which hold a real citation, stay.
The matching rows are also removed from data_dictionary.csv.

Usage:  python scripts/clean_data.py [data_dir]
"""

import csv
import sys
from pathlib import Path

DATA_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "data" / "raw"
PROVENANCE_VALUES = {"kaggle", "added", ""}


def read(path):
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    return rows[0], rows[1:]


def write(path, header, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def kaggle_columns(header, rows):
    drop = []
    for i, name in enumerate(header):
        values = {r[i].strip().lower() for r in rows if i < len(r)}
        if name.lower().startswith("kaggle_"):
            drop.append(i)
        elif name.lower() == "source" and "kaggle" in values and values <= PROVENANCE_VALUES:
            drop.append(i)
    return drop


def main():
    removed = {}
    for path in sorted(DATA_DIR.glob("*.csv")):
        if path.name == "data_dictionary.csv":
            continue
        header, rows = read(path)
        drop = kaggle_columns(header, rows)
        if not drop:
            continue
        keep = [i for i in range(len(header)) if i not in drop]
        removed[path.stem] = [header[i] for i in drop]
        write(path, [header[i] for i in keep], [[r[i] for i in keep] for r in rows])

    dd = DATA_DIR / "data_dictionary.csv"
    if dd.exists() and removed:
        header, rows = read(dd)
        rows = [r for r in rows if r[1] not in removed.get(r[0], [])]
        write(dd, header, rows)

    for table, cols in removed.items():
        print(f"{table}: removed {', '.join(cols)}")
    if not removed:
        print("Nothing to remove (already clean).")


if __name__ == "__main__":
    main()
