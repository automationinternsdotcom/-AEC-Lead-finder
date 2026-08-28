"""Atomic CSV write: a crash mid-write leaves the old file."""
from __future__ import annotations

import csv
import os


def write_csv(path: str, rows: list[dict], fields: list[str]) -> None:
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(tmp, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fields)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)
    os.replace(tmp, path)


if __name__ == "__main__":
    import tempfile

    target = os.path.join(tempfile.mkdtemp(), "t.csv")
    write_csv(target, [{"a": "1", "b": "2"}, {"a": "3"}], ["a", "b"])
    with open(target, newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    assert rows == [{"a": "1", "b": "2"}, {"a": "3", "b": ""}]
    assert not os.path.exists(target + ".tmp")
    print("csvio self-check OK")
